import logging

from flask import Blueprint, abort, current_app, jsonify, request

from ..auth import current_user_id
from ..limits import RateLimiter, client_ip
from ..llm import parse_query
from ..recipes import ProviderError, QuotaExceeded
from ..store import Store

log = logging.getLogger(__name__)

bp = Blueprint("search", __name__)

MAX_QUERY_LENGTH = 300


def _store() -> Store:
    return current_app.extensions["sous.store"]


@bp.post("/search")
def create_search():
    body = request.get_json(silent=True) or {}
    query = str(body.get("query", "")).strip()
    if not query:
        abort(400, "query is required")
    if len(query) > MAX_QUERY_LENGTH:
        abort(400, f"query must be {MAX_QUERY_LENGTH} characters or fewer")

    user_id = current_user_id()
    # guests share the spoonacular quota, so cap them per ip
    limiter: RateLimiter = current_app.extensions["sous.guest_limiter"]
    if user_id is None and not limiter.allow(client_ip()):
        return jsonify(
            error="rate_limited", message="Too many searches. Sign in or try again in a bit."
        ), 429

    filters = parse_query(query, current_app.config["ANTHROPIC_MODEL"])
    provider = current_app.extensions["sous.spoonacular"]

    try:
        recipes = provider.search(filters)
        # soft filters can zero things out, retry once without them
        if not recipes and filters.is_relaxable():
            filters = filters.relaxed()
            recipes = provider.search(filters)
    except QuotaExceeded as e:
        return jsonify(error="quota_exceeded", message=str(e)), 429
    except ProviderError as e:
        log.error("recipe search failed: %s", e)
        return jsonify(error="provider_error", message="Recipe search is unavailable"), 502

    session = _store().create_session(query, filters, recipes, user_id)
    return jsonify(session.to_dict()), 201


@bp.get("/sessions/<session_id>")
def get_session(session_id: str):
    session = _store().get_session(session_id)
    if session is None:
        abort(404, "search session not found or expired")
    return jsonify(session.to_dict())
