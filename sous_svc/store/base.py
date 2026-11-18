import time
from dataclasses import dataclass, field
from typing import Protocol

from ..recipes import Recipe, SearchFilters


@dataclass
class Pool:
    filters: SearchFilters
    recipes: list[Recipe]
    # results already pulled from the provider, the next fetch starts here
    offset: int = 0
    # results the provider has for these filters
    total: int = 0

    @property
    def has_more(self) -> bool:
        return self.offset < self.total


WEB_JOB_TIMEOUT = 180


@dataclass
class SearchSession:
    id: str
    query: str
    pool: Pool
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    # none means the whole pool is showing
    visible_ids: list[str] | None = None
    # "pending" while web results are being fetched in the background
    web_status: str | None = None
    web_started: float | None = None

    @property
    def web_pending(self) -> bool:
        # a job lost to a restart shouldn't keep the frontend polling forever
        return (
            self.web_status == "pending"
            and self.web_started is not None
            and time.time() - self.web_started < WEB_JOB_TIMEOUT
        )

    @property
    def filters(self) -> SearchFilters:
        return self.pool.filters

    @property
    def narrowed(self) -> bool:
        return self.visible_ids is not None

    def view(self) -> list[Recipe]:
        if self.visible_ids is None:
            return self.pool.recipes
        keep = set(self.visible_ids)
        return [r for r in self.pool.recipes if r.id in keep]


class RecipeNotFound(Exception):
    pass


class Store(Protocol):
    def create_session(self, query: str, pool: Pool, user_id: str | None) -> SearchSession: ...

    def get_session(self, session_id: str) -> SearchSession | None: ...

    # appends recipes fetched past the current offset
    def extend_pool(
        self, session: SearchSession, recipes: list[Recipe], offset: int, total: int
    ) -> None: ...

    # swaps in a new search's pool and clears any narrowing
    def replace_pool(self, session: SearchSession, pool: Pool) -> None: ...

    def set_visible(self, session: SearchSession, recipe_ids: list[str] | None) -> None: ...

    def set_web_status(self, session_id: str, status: str) -> None: ...

    def get_cached_pool(self, key: str) -> Pool | None: ...

    def put_cached_pool(self, key: str, pool: Pool) -> None: ...

    # raw claude messages, oldest first
    def list_messages(self, session_id: str) -> list[dict]: ...

    def append_messages(self, session_id: str, messages: list[dict]) -> None: ...

    def list_saved(self, user_id: str) -> list[Recipe]: ...

    def save_recipe(self, user_id: str, recipe_id: str) -> None: ...

    def unsave_recipe(self, user_id: str, recipe_id: str) -> None: ...
