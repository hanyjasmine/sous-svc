import threading
import time
import uuid

from ..recipes import Recipe, SearchFilters
from .base import RecipeNotFound, SearchSession


# used in tests and when supabase isn't configured, everything vanishes on restart
class MemoryStore:
    def __init__(self, ttl_seconds: int, max_sessions: int = 500):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._sessions: dict[str, SearchSession] = {}
        self._recipes: dict[str, Recipe] = {}
        self._saved: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def create_session(
        self, query: str, filters: SearchFilters, recipes: list[Recipe], user_id: str | None
    ) -> SearchSession:
        session = SearchSession(str(uuid.uuid4()), query, filters, recipes, user_id)
        with self._lock:
            self._evict()
            self._sessions[session.id] = session
            self._recipes.update({r.id: r for r in recipes})
        return session

    def get_session(self, session_id: str) -> SearchSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and time.time() - session.created_at > self._ttl:
                del self._sessions[session_id]
                return None
            return session

    def list_saved(self, user_id: str) -> list[Recipe]:
        with self._lock:
            return [self._recipes[rid] for rid in reversed(self._saved.get(user_id, []))]

    def save_recipe(self, user_id: str, recipe_id: str) -> None:
        with self._lock:
            if recipe_id not in self._recipes:
                raise RecipeNotFound(recipe_id)
            saved = self._saved.setdefault(user_id, [])
            if recipe_id not in saved:
                saved.append(recipe_id)

    def unsave_recipe(self, user_id: str, recipe_id: str) -> None:
        with self._lock:
            saved = self._saved.get(user_id, [])
            if recipe_id in saved:
                saved.remove(recipe_id)

    def _evict(self) -> None:
        now = time.time()
        for sid in [s.id for s in self._sessions.values() if now - s.created_at > self._ttl]:
            del self._sessions[sid]
        while len(self._sessions) >= self._max:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            del self._sessions[oldest.id]
