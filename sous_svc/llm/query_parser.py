import logging

import anthropic

from ..recipes.filters import SearchFilters
from .client import get_client
from .prompts import QUERY_PARSER_SYSTEM

log = logging.getLogger(__name__)


def parse_query(query: str, model: str) -> SearchFilters:
    try:
        response = get_client().beta.messages.parse(
            model=model,
            max_tokens=2048,
            system=QUERY_PARSER_SYSTEM,
            messages=[{"role": "user", "content": query}],
            output_format=SearchFilters,
            # simple extraction, low effort keeps it fast
            output_config={"effort": "low"},
            # retry on another model if a safety classifier declines
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as e:
        log.warning("query parse failed, using raw query: %s", e)
        return SearchFilters.from_raw(query)

    if response.stop_reason == "refusal" or response.parsed_output is None:
        log.warning("query parse returned no filters (stop_reason=%s)", response.stop_reason)
        return SearchFilters.from_raw(query)
    return response.parsed_output
