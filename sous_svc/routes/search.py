import logging

from flask import Blueprint, abort, current_app, jsonify, request

from ..auth import current_user_id
from ..limits import RateLimiter, client_ip
from ..llm import parse_query
from ..pool import (
    PAGE_SIZE,
    PoolResult,
    cache_pool,
    find_pool,
    page,
    query_cache_key,
    session_dict,
)
from ..recipes import ProviderError, QuotaExceeded
from ..store import Pool, SearchSession, Store
from ..web import WebTopup

log = logging.getLogger(__name__)

bp = Blueprint("search", __name__)

MAX_QUERY_LENGTH = 300


def _store() -> Store:
    return current_app.extensions["sous.store"]


def _provider():
    return current_app.extensions["sous.spoonacular"]


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

    web: WebTopup = current_app.extensions["sous.web"]
    query_key = query_cache_key(query)
    if cached := _store().get_cached_pool(query_key):
        session = _store().create_session(query, cached, user_id)
        return jsonify(session_dict(session, _provider(), _store())), 201

    filters = parse_query(query, current_app.config["ANTHROPIC_MODEL"])
    try:
        result = find_pool(filters, _provider(), _store())
    except ProviderError as e:
        # the web can stand in when spoonacular is down or out of quota
        if not web.enabled:
            if isinstance(e, QuotaExceeded):
                return jsonify(error="quota_exceeded", message=str(e)), 429
            log.error("recipe search failed: %s", e)
            return jsonify(error="provider_error", message="Recipe search is unavailable"), 502
        log.warning("recipe api failed, searching the web instead: %s", e)
        result = PoolResult(Pool(filters, []), [filters.cache_key()], fresh=True)

    session = _store().create_session(query, result.pool, user_id)
    result.cache_keys.append(query_key)
    if web.needed(result):
        web.start(session, result, query)
    else:
        cache_pool(_store(), result)
    return jsonify(session_dict(session, _provider(), _store())), 201


def load_session(session_id: str) -> SearchSession:
    session = _store().get_session(session_id)
    if session is None:
        abort(404, "search session not found or expired")
    return session


@bp.get("/sessions/<session_id>")
def get_session(session_id: str):
    return jsonify(session_dict(load_session(session_id), _provider(), _store()))


@bp.get("/sessions/<session_id>/results")
def get_results(session_id: str):
    session = load_session(session_id)
    cursor = request.args.get("cursor", 0, type=int)
    limit = request.args.get("limit", PAGE_SIZE, type=int)
    if cursor < 0 or not 1 <= limit <= 30:
        abort(400, "bad cursor or limit")
    return jsonify(page(session, _provider(), _store(), cursor, limit))
