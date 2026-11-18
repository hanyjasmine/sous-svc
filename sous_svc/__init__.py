import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from .config import Config, features
from .limits import RateLimiter
from .recipes import SpoonacularClient
from .routes import register_routes
from .store import MemoryStore, SupabaseStore
from .web import WebTopup


def create_app(config: type[Config] | dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if isinstance(config, dict):
        app.config.update(config)
    elif config is not None:
        app.config.from_object(config)

    logging.basicConfig(level=logging.INFO)

    store = _make_store(app)
    app.extensions["sous.store"] = store
    app.extensions["sous.spoonacular"] = SpoonacularClient(app.config["SPOONACULAR_API_KEY"])
    app.extensions["sous.guest_limiter"] = RateLimiter(app.config["GUEST_SEARCHES_PER_HOUR"])
    app.extensions["sous.guest_chat_limiter"] = RateLimiter(app.config["GUEST_CHAT_TURNS_PER_HOUR"])

    app.extensions["sous.web"] = WebTopup(
        store,
        model=app.config["WEB_SEARCH_MODEL"],
        # tests opt in explicitly so they never hit the network
        enabled=features(app.config)["web_search_fallback"] and not app.config.get("TESTING"),
        below=app.config["WEB_TOPUP_BELOW"],
    )

    register_routes(app)

    # json errors everywhere so the frontend never parses html
    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        return jsonify(error=err.name, message=err.description), err.code

    return app


def _make_store(app: Flask):
    ttl = app.config["SESSION_TTL_SECONDS"]
    url, key = app.config["SUPABASE_URL"], app.config["SUPABASE_SECRET_KEY"]
    if app.config.get("TESTING") or not (url and key):
        app.logger.warning("supabase not configured, using in-memory store")
        return MemoryStore(ttl)

    from supabase import create_client

    return SupabaseStore(create_client(url, key), ttl)
