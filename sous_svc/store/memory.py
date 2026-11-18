import threading
import time
import uuid
from dataclasses import replace

from ..recipes import Recipe
from .base import Pool, RecipeNotFound, SearchSession


# used in tests and when supabase isn't configured, everything vanishes on restart
class MemoryStore:
    def __init__(self, ttl_seconds: int, max_sessions: int = 500):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._sessions: dict[str, SearchSession] = {}
        self._messages: dict[str, list[dict]] = {}
        self._cache: dict[str, tuple[float, Pool]] = {}
        self._recipes: dict[str, Recipe] = {}
        self._saved: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def create_session(self, query: str, pool: Pool, user_id: str | None) -> SearchSession:
        session = SearchSession(str(uuid.uuid4()), query, _copy(pool), user_id)
        with self._lock:
            self._evict()
            self._sessions[session.id] = session
            self._remember(pool.recipes)
        return _snapshot(session)

    def get_session(self, session_id: str) -> SearchSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and time.time() - session.created_at > self._ttl:
                del self._sessions[session_id]
                self._messages.pop(session_id, None)
                return None
            return _snapshot(session) if session else None

    def extend_pool(
        self, session: SearchSession, recipes: list[Recipe], offset: int, total: int
    ) -> None:
        with self._lock:
            stored = self._sessions[session.id]
            # visible_ids already leaves new arrivals out of a narrowed view
            stored.pool.recipes.extend(recipes)
            stored.pool.offset, stored.pool.total = offset, total
            self._remember(recipes)

    def replace_pool(self, session: SearchSession, pool: Pool) -> None:
        with self._lock:
            stored = self._sessions[session.id]
            stored.pool = _copy(pool)
            stored.visible_ids = None
            self._remember(pool.recipes)

    def set_visible(self, session: SearchSession, recipe_ids: list[str] | None) -> None:
        with self._lock:
            visible = list(recipe_ids) if recipe_ids is not None else None
            self._sessions[session.id].visible_ids = visible

    def set_web_status(self, session_id: str, status: str) -> None:
        with self._lock:
            if stored := self._sessions.get(session_id):
                stored.web_status = status
                if status == "pending":
                    stored.web_started = time.time()

    def get_cached_pool(self, key: str) -> Pool | None:
        with self._lock:
            hit = self._cache.get(key)
            if not hit or time.time() - hit[0] > self._ttl:
                return None
            return _copy(hit[1])

    def put_cached_pool(self, key: str, pool: Pool) -> None:
        with self._lock:
            self._cache[key] = (time.time(), _copy(pool))
            self._remember(pool.recipes)

    def list_messages(self, session_id: str) -> list[dict]:
        with self._lock:
            return list(self._messages.get(session_id, []))

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        with self._lock:
            self._messages.setdefault(session_id, []).extend(messages)

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

    def _remember(self, recipes: list[Recipe]) -> None:
        self._recipes.update({r.id: r for r in recipes})

    def _evict(self) -> None:
        now = time.time()
        for sid in [s.id for s in self._sessions.values() if now - s.created_at > self._ttl]:
            del self._sessions[sid]
            self._messages.pop(sid, None)
        while len(self._sessions) >= self._max:
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            del self._sessions[oldest.id]
            self._messages.pop(oldest.id, None)


# callers get copies so their edits never leak into the store
def _copy(pool: Pool) -> Pool:
    return replace(pool, recipes=list(pool.recipes))


def _snapshot(session: SearchSession) -> SearchSession:
    visible = list(session.visible_ids) if session.visible_ids is not None else None
    return replace(session, pool=_copy(session.pool), visible_ids=visible)
