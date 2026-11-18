import os
from collections.abc import Mapping
from typing import Any

from dotenv import load_dotenv

# override so .env edits apply when the dev reloader restarts the app
load_dotenv(override=True)


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")

    SPOONACULAR_API_KEY = os.getenv("SPOONACULAR_API_KEY", "")

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
    TWILIO_VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")

    # feature flags
    WEB_SEARCH_FALLBACK_ENABLED = _flag("WEB_SEARCH_FALLBACK_ENABLED", True)
    NYT_MCP_ENABLED = _flag("NYT_MCP_ENABLED")
    # personal use only, full nyt recipes are paywalled content
    NYT_MCP_FULL_RECIPES = _flag("NYT_MCP_FULL_RECIPES")

    GUEST_SEARCHES_PER_HOUR = int(os.getenv("GUEST_SEARCHES_PER_HOUR", "10"))
    GUEST_CHAT_TURNS_PER_HOUR = int(os.getenv("GUEST_CHAT_TURNS_PER_HOUR", "40"))

    # spoonacular terms allow caching results for up to an hour
    SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))


def features(cfg: Mapping[str, Any]) -> dict[str, bool]:
    nyt = bool(cfg["NYT_MCP_ENABLED"])
    return {
        "web_search_fallback": bool(cfg["WEB_SEARCH_FALLBACK_ENABLED"]),
        "nyt_mcp": nyt,
        "nyt_full_recipes": nyt and bool(cfg["NYT_MCP_FULL_RECIPES"]),
    }
