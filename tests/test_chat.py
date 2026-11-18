import json
from dataclasses import dataclass, field

import pytest
from conftest import SUPABASE_URL, FakeProvider, make_token, recipe

from sous_svc import create_app
from sous_svc.recipes import SearchFilters


@dataclass
class Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None

    def model_dump(self, exclude_none: bool = False) -> dict:
        data = {k: v for k, v in self.__dict__.items()}
        return {k: v for k, v in data.items() if v is not None} if exclude_none else data


@dataclass
class Message:
    content: list[Block]
    stop_reason: str = "end_turn"


class FakeStream:
    def __init__(self, message: Message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


@dataclass
class FakeClaude:
    replies: list[Message]
    requests: list[dict] = field(default_factory=list)

    @property
    def messages(self):
        return self

    def stream(self, **kwargs):
        # copy so later appends don't rewrite what this call saw
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return FakeStream(self.replies.pop(0))


def text(t: str) -> Message:
    return Message([Block("text", text=t)])


def tool(name: str, args: dict, say: str | None = None) -> Message:
    blocks = [Block("text", text=say)] if say else []
    return Message([*blocks, Block("tool_use", id=f"tu_{name}", name=name, input=args)], "tool_use")


@pytest.fixture
def setup(monkeypatch):
    def make(replies, provider=None, first=None):
        claude = FakeClaude(list(replies))
        monkeypatch.setattr("sous_svc.llm.chat.get_client", lambda: claude)
        monkeypatch.setattr(
            "sous_svc.routes.search.parse_query", lambda q, m: SearchFilters.from_raw("soup")
        )
        app = create_app({"TESTING": True, "SUPABASE_URL": SUPABASE_URL})
        app.extensions["sous.spoonacular"] = provider or FakeProvider(
            [first or [recipe(i) for i in range(1, 6)]]
        )
        client = app.test_client()
        session = client.post("/search", json={"query": "warm soup"}).get_json()
        return client, session["id"], claude

    return make


def events(res) -> list[tuple[str, dict]]:
    out = []
    for chunk in res.get_data(as_text=True).strip().split("\n\n"):
        name, data = chunk.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_greet_streams_reply_and_saves_history(setup):
    client, sid, claude = setup([text("Thanks for choosing Sous!")])
    res = client.post(f"/sessions/{sid}/chat", json={"greet": True})
    assert res.mimetype == "text/event-stream"
    assert events(res) == [("message", {"content": "Thanks for choosing Sous!"}), ("done", {})]

    sent = claude.requests[0]["messages"]
    assert sent[0]["content"][0]["text"] == "warm soup"
    assert sent[1]["role"] == "system"
    assert sent[1]["content"].startswith("[pool ")

    shown = client.get(f"/sessions/{sid}/messages").get_json()["messages"]
    assert [(m["role"], m["content"]) for m in shown] == [
        ("user", "warm soup"),
        ("assistant", "Thanks for choosing Sous!"),
    ]
    # greeting only happens once
    assert client.post(f"/sessions/{sid}/chat", json={"greet": True}).status_code == 409


def test_narrow_updates_view(setup):
    client, sid, claude = setup(
        [
            tool("narrow_results", {"recipe_ids": ["spoonacular-2", "spoonacular-4", "nope"]}),
            text("Here are two."),
        ]
    )
    evs = events(client.post(f"/sessions/{sid}/chat", json={"message": "only even ones"}))
    results = [d for n, d in evs if n == "results"]
    assert [r["id"] for r in results[0]["recipes"]] == ["spoonacular-2", "spoonacular-4"]
    assert results[0]["narrowed"] is True

    # the tool result went back to the model
    tool_result = claude.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "thin list" in tool_result["content"]

    session = client.get(f"/sessions/{sid}").get_json()
    assert session["total"] == 2
    assert session["narrowed"] is True


def test_show_all_clears_narrowing(setup):
    client, sid, _ = setup(
        [
            tool("narrow_results", {"recipe_ids": ["spoonacular-1"]}),
            text("Just one."),
            tool("show_all_results", {}),
            text("All back."),
        ]
    )
    client.post(f"/sessions/{sid}/chat", json={"message": "just the first"}).get_data()
    client.post(f"/sessions/{sid}/chat", json={"message": "show everything"}).get_data()
    session = client.get(f"/sessions/{sid}").get_json()
    assert session["total"] == 5
    assert session["narrowed"] is False


def test_new_search_replaces_pool(setup):
    provider = FakeProvider([[recipe(1)], [recipe(10, "Brownies"), recipe(11, "Blondies")]])
    filters = SearchFilters.from_raw("brownies").model_dump()
    client, sid, claude = setup(
        [tool("new_search", filters, say="Let me look."), text("Found some.")], provider
    )
    evs = events(client.post(f"/sessions/{sid}/chat", json={"message": "desserts instead"}))
    # the text before the tool call never reaches the user
    assert [d for n, d in evs if n == "message"] == [{"content": "Found some."}]
    results = next(d for n, d in evs if n == "results")
    assert [r["title"] for r in results["recipes"]] == ["Brownies", "Blondies"]
    assert provider.calls[-1][0].query == "brownies"

    # the new pool goes back to the model in the tool result
    tool_result = claude.requests[1]["messages"][-1]["content"][0]["content"]
    assert "Brownies" in tool_result

    shown = client.get(f"/sessions/{sid}/messages").get_json()["messages"]
    assert shown[-1]["content"] == "Found some."


def test_invalid_search_filters_return_tool_error(setup):
    client, sid, claude = setup([tool("new_search", {"query": 3}), text("Hmm.")])
    client.post(f"/sessions/{sid}/chat", json={"message": "x"}).get_data()
    result = claude.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True


def test_pool_is_not_resent_when_unchanged(setup):
    client, sid, claude = setup([text("Hi."), text("Sure.")])
    client.post(f"/sessions/{sid}/chat", json={"greet": True}).get_data()
    client.post(f"/sessions/{sid}/chat", json={"message": "thanks"}).get_data()
    second = claude.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "system", "assistant", "user"]


def test_tool_loop_is_capped(setup):
    narrow = tool("narrow_results", {"recipe_ids": ["spoonacular-1"]})
    client, sid, claude = setup([narrow, narrow, narrow, narrow])
    evs = events(client.post(f"/sessions/{sid}/chat", json={"message": "x"}))
    assert evs[-1] == ("done", {})
    assert len(claude.requests) == 4
    last = client.application.extensions["sous.store"].list_messages(sid)[-1]
    assert last["content"][0]["is_error"] is True


def test_other_users_session_is_forbidden(setup):
    client, _, _ = setup([])
    mine = client.post(
        "/search", json={"query": "soup"}, headers={"Authorization": f"Bearer {make_token()}"}
    ).get_json()
    other = {"Authorization": f"Bearer {make_token(sub='22222222-2222-2222-2222-222222222222')}"}
    res = client.post(f"/sessions/{mine['id']}/chat", json={"message": "hi"}, headers=other)
    assert res.status_code == 403


def test_empty_message_is_rejected(setup):
    client, sid, _ = setup([])
    assert client.post(f"/sessions/{sid}/chat", json={"message": " "}).status_code == 400


def test_thin_new_search_tops_up_from_the_web(setup):
    from test_web import WEB, fake_web

    from sous_svc.web import WebTopup

    provider = FakeProvider([[recipe(1)], [recipe(10, "Brownies")]])
    filters = SearchFilters.from_raw("brownies").model_dump()
    client, sid, claude = setup([tool("new_search", filters), text("Found some.")], provider)
    find_urls, fetch = fake_web(WEB)
    app = client.application
    app.extensions["sous.web"] = WebTopup(
        app.extensions["sous.store"],
        model="m",
        enabled=True,
        below=15,
        find_urls=find_urls,
        fetch=fetch,
        submit=lambda fn, *args: fn(*args),
    )
    client.post(f"/sessions/{sid}/chat", json={"message": "fudgy brownies"}).get_data()
    # the web search gets the user's words, not just the filters
    assert find_urls.calls == ["fudgy brownies"]
    session = client.get(f"/sessions/{sid}").get_json()
    assert [r["title"] for r in session["recipes"]] == [
        "Brownies",
        "Web Carrots",
        "Web Glazed Carrots",
    ]
