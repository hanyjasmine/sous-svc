import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import anthropic
from pydantic import ValidationError

from ..pool import cache_pool, find_pool, page
from ..recipes import ProviderError, QuotaExceeded, SearchFilters
from ..store import SearchSession, Store
from .client import get_client
from .prompts import CHAT_SYSTEM

log = logging.getLogger(__name__)

# model calls per user turn, the last one can't run tools
MAX_ROUNDS = 4
MAX_TOKENS = 8000

_POOL_TAG = re.compile(r"\[pool ([0-9a-f]{12})\]")

Event = tuple[str, dict]


def _filters_schema() -> dict:
    schema = SearchFilters.model_json_schema()
    schema.pop("title", None)
    schema["additionalProperties"] = False
    return schema


TOOLS = [
    {
        "name": "narrow_results",
        "description": (
            "Show only some of the recipes already in the user's result pool. Use when the "
            "request can be answered from the pool. Pass every id that genuinely matches; "
            "the list keeps its original order. Free and instant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recipe_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ids from the pool, e.g. spoonacular-715769",
                }
            },
            "required": ["recipe_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "show_all_results",
        "description": "Undo any narrowing and show the whole result pool again.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "new_search",
        "description": (
            "Fetch a new pool of recipes from the recipe API, replacing the current pool and "
            "any narrowing. Use only when the pool can't satisfy the request. Pass the complete "
            "filters for what the user wants now, keeping earlier constraints that still "
            "apply. Takes a few seconds and uses shared API quota. Returns the new pool."
        ),
        "input_schema": _filters_schema(),
    },
]


@dataclass
class ChatContext:
    session: SearchSession
    store: Store
    provider: object
    model: str
    # false once a guest has used up their searches
    allow_search: Callable[[], bool]


def run_turn(ctx: ChatContext, history: list[dict], user_text: str) -> Iterator[Event]:
    new: list[dict] = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
    pool_text = pool_message(ctx.session)
    # only resend the pool when it changed since the model last saw it
    if _tag(pool_text) != last_pool_tag(history):
        new.append({"role": "system", "content": pool_text})
    messages = [*history, *new]

    try:
        for round_no in range(MAX_ROUNDS):
            # whole message at once, never the model's working before a tool call
            with get_client().messages.stream(
                model=ctx.model,
                max_tokens=MAX_TOKENS,
                system=CHAT_SYSTEM,
                tools=TOOLS,
                messages=messages,
                # short chat turns, low effort keeps replies quick
                output_config={"effort": "low"},
                cache_control={"type": "ephemeral"},
            ) as stream:
                msg = stream.get_final_message()

            tool_uses = [b for b in msg.content if b.type == "tool_use"]
            refused = msg.stop_reason == "refusal"
            blocks = [
                b.model_dump(exclude_none=True)
                for b in msg.content
                # a refusal can cut a tool call off, never replay it
                if not (refused and b.type == "tool_use")
            ]
            if blocks:
                _push(messages, new, {"role": "assistant", "content": blocks})
            text = "\n\n".join(b.text for b in msg.content if b.type == "text").strip()
            if refused:
                yield "message", {"content": text or "Sorry, I can't help with that one."}
                break
            if not tool_uses:
                if text:
                    yield "message", {"content": text}
                break

            results = []
            for tu in tool_uses:
                if round_no == MAX_ROUNDS - 1 or msg.stop_reason == "max_tokens":
                    results.append(_result(tu.id, "tool limit reached, reply to the user", True))
                    continue
                result, update = run_tool(ctx, tu.name, tu.input, tu.id)
                results.append(result)
                if update:
                    yield "results", update
            _push(messages, new, {"role": "user", "content": results})
    except anthropic.APIError as e:
        log.error("chat turn failed: %s", e)
        yield "error", {"message": "Sous couldn't reply just now. Try again in a moment."}
        return

    ctx.store.append_messages(ctx.session.id, new)
    yield "done", {}


def run_tool(ctx: ChatContext, name: str, args, tool_use_id: str) -> tuple[dict, dict | None]:
    session = ctx.session
    if not isinstance(args, dict):
        return _result(tool_use_id, "invalid input", True), None

    if name == "narrow_results":
        ids = args.get("recipe_ids")
        if not isinstance(ids, list):
            return _result(tool_use_id, "recipe_ids must be a list", True), None
        known = {r.id for r in session.pool.recipes}
        keep = [i for i in dict.fromkeys(ids) if isinstance(i, str) and i in known]
        if not keep:
            return _result(
                tool_use_id, "none of those ids are in the pool, nothing changed", True
            ), None
        ctx.store.set_visible(session, keep)
        session.visible_ids = keep
        titles = "; ".join(r.title for r in session.view())
        note = f"Now showing {len(keep)} of {len(session.pool.recipes)}: {titles}"
        if len(keep) < 3:
            note += ". That's a thin list, offer a new search if they want more options."
        return _result(tool_use_id, note), _update(ctx)

    if name == "show_all_results":
        ctx.store.set_visible(session, None)
        session.visible_ids = None
        return _result(tool_use_id, f"Showing all {len(session.pool.recipes)} recipes."), _update(
            ctx
        )

    if name == "new_search":
        try:
            filters = SearchFilters.model_validate(args)
        except ValidationError as e:
            return _result(tool_use_id, f"invalid filters: {e.errors()[:3]}", True), None
        if not ctx.allow_search():
            msg = "guest search limit reached this hour, suggest signing in or trying later"
            return _result(tool_use_id, msg, True), None
        try:
            result = find_pool(filters, ctx.provider, ctx.store)
        except QuotaExceeded:
            msg = "the recipe API's daily quota is used up, it resets at midnight utc"
            return _result(tool_use_id, msg, True), None
        except ProviderError as e:
            log.error("chat search failed: %s", e)
            return _result(tool_use_id, "recipe search is unavailable right now", True), None
        if not result.pool.recipes:
            return _result(tool_use_id, "no recipes found, the pool is unchanged"), None
        ctx.store.replace_pool(session, result.pool)
        cache_pool(ctx.store, result)
        session.pool, session.visible_ids = result.pool, None
        return _result(tool_use_id, pool_message(session)), _update(ctx)

    return _result(tool_use_id, f"unknown tool {name}", True), None


def pool_message(session: SearchSession) -> str:
    pool = session.pool
    lines = [
        " | ".join(
            [
                r.id,
                r.title,
                f"{r.total_minutes} min" if r.total_minutes else "? min",
                ", ".join(r.tags) or "-",
                ", ".join(i.name for i in r.ingredients) or "-",
            ]
        )
        for r in pool.recipes
    ]
    body = "\n".join(lines) or "(empty)"
    filters = json.dumps(pool.filters.model_dump(exclude_defaults=True), sort_keys=True)
    digest = hashlib.sha256(f"{filters}\n{body}".encode()).hexdigest()[:12]
    more = ", more available upstream" if pool.has_more else ""
    showing = ", ".join(session.visible_ids) if session.visible_ids is not None else "all"
    return (
        f"[pool {digest}]\n"
        f"Filters: {filters}\n"
        f"Recipes in pool: {len(pool.recipes)}{more}\n"
        f"Showing: {showing}\n\n{body}"
    )


def last_pool_tag(history: list[dict]) -> str | None:
    for msg in reversed(history):
        if found := _POOL_TAG.findall(json.dumps(msg["content"])):
            return found[-1]
    return None


# turn raw claude history into the bubbles the frontend shows
def display_messages(history: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, msg in enumerate(history):
        if msg["role"] == "system" or _calls_tool(msg):
            continue
        text = "\n\n".join(_texts(msg["content"])).strip()
        if not text:
            continue
        if msg["role"] == "assistant" and out and out[-1]["role"] == "assistant":
            out[-1]["content"] += f"\n\n{text}"
        else:
            out.append({"id": f"m{i}", "role": msg["role"], "content": text})
    return out


def count_user_turns(history: list[dict]) -> int:
    return sum(1 for m in history if m["role"] == "user" and _texts(m["content"]))


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [b["text"] for b in content if b.get("type") == "text" and b.get("text")]


def _calls_tool(msg: dict) -> bool:
    content = msg["content"]
    return isinstance(content, list) and any(b.get("type") == "tool_use" for b in content)


def _tag(pool_text: str) -> str:
    return _POOL_TAG.match(pool_text).group(1)


def _push(messages: list[dict], new: list[dict], msg: dict) -> None:
    messages.append(msg)
    new.append(msg)


def _result(tool_use_id: str, content: str, error: bool = False) -> dict:
    out = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if error:
        out["is_error"] = True
    return out


def _update(ctx: ChatContext) -> dict:
    return page(ctx.session, ctx.provider, ctx.store)
