import pytest
from conftest import SUPABASE_URL, FakeProvider, recipe

from sous_svc import create_app
from sous_svc.limits import RateLimiter
from sous_svc.recipes import QuotaExceeded, Recipe, SearchFilters

RECIPE = Recipe(id="spoonacular-1", source="spoonacular", title="Soup", summary="Warm.")


@pytest.fixture
def make_client(monkeypatch):
    def make(provider, filters=None):
        parsed = filters or SearchFilters.from_raw("soup")
        monkeypatch.setattr("sous_svc.routes.search.parse_query", lambda q, model: parsed)
        app = create_app({"TESTING": True})
        app.extensions["sous.spoonacular"] = provider
        return app.test_client()

    return make


def test_search_creates_session_and_returns_recipes(make_client):
    client = make_client(FakeProvider([[RECIPE]]))
    res = client.post("/search", json={"query": "warm soup"})
    assert res.status_code == 201
    body = res.get_json()
    assert body["query"] == "warm soup"
    assert body["recipes"][0]["title"] == "Soup"

    again = client.get(f"/sessions/{body['id']}")
    assert again.status_code == 200
    assert again.get_json()["recipes"][0]["id"] == "spoonacular-1"


def test_search_relaxes_soft_filters_when_empty(make_client):
    strict = SearchFilters.from_raw("soup").model_copy(
        update={"cuisines": ["Thai"], "diets": ["vegan"]}
    )
    provider = FakeProvider([[], [RECIPE]])
    res = make_client(provider, strict).post("/search", json={"query": "thai vegan soup"})
    assert res.status_code == 201
    assert len(provider.calls) == 2
    assert provider.calls[1][0].cuisines == []
    assert provider.calls[1][0].diets == ["vegan"]


def test_search_rejects_empty_query(make_client):
    res = make_client(FakeProvider()).post("/search", json={"query": "  "})
    assert res.status_code == 400


def test_search_returns_429_when_quota_used_up(make_client):
    res = make_client(FakeProvider(error=QuotaExceeded("used up"))).post(
        "/search", json={"query": "soup"}
    )
    assert res.status_code == 429
    assert res.get_json()["error"] == "quota_exceeded"


def test_unknown_session_is_404(make_client):
    assert make_client(FakeProvider()).get("/sessions/nope").status_code == 404


def test_guests_are_rate_limited_per_ip(make_client):
    client = make_client(FakeProvider())
    client.application.extensions["sous.guest_limiter"] = RateLimiter(limit=2)
    ip = {"X-Forwarded-For": "1.2.3.4"}
    assert client.post("/search", json={"query": "a"}, headers=ip).status_code == 201
    assert client.post("/search", json={"query": "b"}, headers=ip).status_code == 201
    res = client.post("/search", json={"query": "c"}, headers=ip)
    assert res.status_code == 429
    assert res.get_json()["error"] == "rate_limited"
    # another ip still has room
    other = {"X-Forwarded-For": "5.6.7.8"}
    assert client.post("/search", json={"query": "d"}, headers=other).status_code == 201


def test_signed_in_users_skip_guest_limit(make_client, auth_header):
    client = make_client(FakeProvider())
    client.application.config["SUPABASE_URL"] = SUPABASE_URL
    client.application.extensions["sous.guest_limiter"] = RateLimiter(limit=0)
    res = client.post("/search", json={"query": "a"}, headers=auth_header)
    assert res.status_code == 201


def test_results_page_and_extend_from_provider(make_client):
    first = [recipe(i) for i in range(30)]
    more = [recipe(i) for i in range(30, 50)]
    provider = FakeProvider([first, more], total=100)
    client = make_client(provider)

    body = client.post("/search", json={"query": "soup"}).get_json()
    assert len(body["recipes"]) == 10
    assert body["nextCursor"] == 10
    # 100 upstream, capped at what scrolling can reach
    assert body["total"] == 90

    res = client.get(f"/sessions/{body['id']}/results?cursor=20").get_json()
    assert [r["id"] for r in res["recipes"]][0] == "spoonacular-20"
    assert len(provider.calls) == 1

    # past the pool pulls the next provider page
    res = client.get(f"/sessions/{body['id']}/results?cursor=30").get_json()
    assert res["recipes"][0]["id"] == "spoonacular-30"
    assert res["total"] == 90
    assert provider.calls[1][2] == 30


def test_last_page_has_no_cursor(make_client):
    client = make_client(FakeProvider([[recipe(i) for i in range(12)]]))
    body = client.post("/search", json={"query": "soup"}).get_json()
    res = client.get(f"/sessions/{body['id']}/results?cursor=10").get_json()
    assert len(res["recipes"]) == 2
    assert res["nextCursor"] is None


def test_same_filters_reuse_cached_pool(make_client):
    provider = FakeProvider([[RECIPE]])
    client = make_client(provider)
    a = client.post("/search", json={"query": "warm soup"}).get_json()
    b = client.post("/search", json={"query": "soup please"}).get_json()
    assert len(provider.calls) == 1
    assert a["id"] != b["id"]
    assert b["recipes"][0]["id"] == "spoonacular-1"


def test_total_settles_on_loaded_count_when_provider_runs_out(make_client):
    provider = FakeProvider([[recipe(i) for i in range(30)], [recipe(30), recipe(31)]], total=40)
    client = make_client(provider)
    body = client.post("/search", json={"query": "soup"}).get_json()
    assert body["total"] == 40
    res = client.get(f"/sessions/{body['id']}/results?cursor=30").get_json()
    # spoonacular said 40 but only 32 came back, total follows what's real
    assert len(res["recipes"]) == 2
    assert res["total"] == 32
    assert res["nextCursor"] is None
