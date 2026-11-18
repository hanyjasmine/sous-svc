import json
import threading
import time

from flask import Blueprint, Response, abort, current_app, jsonify, request, stream_with_context

from ..auth import current_user_id
from ..limits import RateLimiter, client_ip
from ..llm.chat import ChatContext, count_user_turns, display_messages, run_turn
from .search import load_session

bp = Blueprint("chat", __name__)

MAX_MESSAGE_LENGTH = 1000
MAX_TURNS = 30
# a turn that never finished releases its lock after this long
BUSY_TIMEOUT = 120

_busy: dict[str, float] = {}
_busy_lock = threading.Lock()


@bp.get("/sessions/<session_id>/messages")
def list_messages(session_id: str):
    session = load_session(session_id)
    history = current_app.extensions["sous.store"].list_messages(session.id)
    return jsonify(messages=display_messages(history))


@bp.post("/sessions/<session_id>/chat")
def chat(session_id: str):
    store = current_app.extensions["sous.store"]
    session = load_session(session_id)
    user_id = current_user_id()
    if session.user_id and session.user_id != user_id:
        abort(403, "this search belongs to another account")

    body = request.get_json(silent=True) or {}
    history = store.list_messages(session.id)
    if body.get("greet") is True:
        # the original query opens the conversation, once
        if history:
            return jsonify(error="already_started", message="Chat already started"), 409
        text = session.query
    else:
        text = str(body.get("message", "")).strip()
        if not text:
            abort(400, "message is required")
        if len(text) > MAX_MESSAGE_LENGTH:
            abort(400, f"message must be {MAX_MESSAGE_LENGTH} characters or fewer")

    if count_user_turns(history) >= MAX_TURNS:
        return jsonify(
            error="turn_limit", message="This chat is full. Start a new search to keep going."
        ), 429
    ip = client_ip()
    chat_limiter: RateLimiter = current_app.extensions["sous.guest_chat_limiter"]
    if user_id is None and not chat_limiter.allow(ip):
        return jsonify(
            error="rate_limited", message="Too many messages. Sign in or try again in a bit."
        ), 429
    if not _claim(session.id):
        return jsonify(error="busy", message="Sous is still replying"), 409

    search_limiter: RateLimiter = current_app.extensions["sous.guest_limiter"]
    ctx = ChatContext(
        session=session,
        store=store,
        provider=current_app.extensions["sous.spoonacular"],
        model=current_app.config["ANTHROPIC_MODEL"],
        allow_search=lambda: user_id is not None or search_limiter.allow(ip),
    )

    def events():
        try:
            for name, data in run_turn(ctx, history, text):
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
        finally:
            _release(session.id)

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        # keep proxies from buffering the stream
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _claim(session_id: str) -> bool:
    now = time.time()
    with _busy_lock:
        started = _busy.get(session_id)
        if started and now - started < BUSY_TIMEOUT:
            return False
        _busy[session_id] = now
        return True


def _release(session_id: str) -> None:
    with _busy_lock:
        _busy.pop(session_id, None)
