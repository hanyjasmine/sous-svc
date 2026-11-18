import json
import logging

import anthropic

from ..llm.client import get_client
from ..llm.prompts import WEB_FINDER_SYSTEM
from ..recipes import SearchFilters

log = logging.getLogger(__name__)

# server tool turns can pause, resume this many times at most
MAX_CALLS = 4

SUBMIT_TOOL = {
    "name": "submit_recipe_urls",
    "description": "Submit the recipe page urls you found, best first. Call once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
        "required": ["urls"],
        "additionalProperties": False,
    },
}

WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    # dynamic filtering spends one use per query it runs from code
    "max_uses": 8,
    # pages here never carry a usable recipe
    "blocked_domains": [
        "pinterest.com",
        "youtube.com",
        "tiktok.com",
        "instagram.com",
        "facebook.com",
        "reddit.com",
    ],
}


def find_recipe_urls(
    request: str, filters: SearchFilters, model: str, have: list[str], limit: int = 12
) -> list[str]:
    prompt = (
        f"Request: {request}\n"
        f"Filters: {json.dumps(filters.model_dump(exclude_defaults=True))}\n"
        f"Find up to {limit} recipe pages."
    )
    if have:
        prompt += "\nThey already have these, find different ones:\n" + "\n".join(have[:30])
    messages: list[dict] = [{"role": "user", "content": prompt}]
    seen_in_results: list[str] = []

    try:
        for _ in range(MAX_CALLS):
            msg = get_client().messages.create(
                model=model,
                max_tokens=8000,
                system=WEB_FINDER_SYSTEM,
                tools=[WEB_SEARCH_TOOL, SUBMIT_TOOL],
                messages=messages,
                output_config={"effort": "low"},
            )
            seen_in_results += _result_urls(msg.content)
            for block in msg.content:
                if block.type == "tool_use" and block.name == "submit_recipe_urls":
                    urls = block.input.get("urls") if isinstance(block.input, dict) else None
                    if isinstance(urls, list):
                        return [u for u in urls if isinstance(u, str)][:limit]
            # a long search turn pauses, sending it back lets the server carry on
            if msg.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": msg.content})
    except anthropic.APIError as e:
        log.warning("web recipe search failed: %s", e)

    # no submission, fall back to whatever the searches turned up
    return list(dict.fromkeys(seen_in_results))[:limit]


def _result_urls(content) -> list[str]:
    urls = []
    for block in content:
        if block.type != "web_search_tool_result" or not isinstance(block.content, list):
            continue
        urls += [r.url for r in block.content if getattr(r, "url", None)]
    return urls
