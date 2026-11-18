import time
from dataclasses import dataclass, field
from typing import Protocol

from ..recipes import Recipe, SearchFilters


@dataclass
class SearchSession:
    id: str
    query: str
    filters: SearchFilters
    recipes: list[Recipe]
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query,
            "filters": self.filters.model_dump(),
            "recipes": [r.to_dict() for r in self.recipes],
        }


class RecipeNotFound(Exception):
    pass


class Store(Protocol):
    def create_session(
        self, query: str, filters: SearchFilters, recipes: list[Recipe], user_id: str | None
    ) -> SearchSession: ...

    def get_session(self, session_id: str) -> SearchSession | None: ...

    def list_saved(self, user_id: str) -> list[Recipe]: ...

    def save_recipe(self, user_id: str, recipe_id: str) -> None: ...

    def unsave_recipe(self, user_id: str, recipe_id: str) -> None: ...
