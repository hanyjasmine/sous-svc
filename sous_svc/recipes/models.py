from dataclasses import dataclass, field
from typing import Literal

RecipeSource = Literal["spoonacular", "web", "nyt"]


@dataclass(frozen=True)
class Ingredient:
    name: str
    quantity: str | None = None
    aisle: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "quantity": self.quantity, "aisle": self.aisle}


@dataclass(frozen=True)
class Recipe:
    id: str
    source: RecipeSource
    title: str
    summary: str
    ingredients: list[Ingredient] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_url: str | None = None
    author: str | None = None
    image_url: str | None = None
    total_minutes: int | None = None
    servings: int | None = None

    # camelCase to match src/types/recipe.ts in the frontend
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "sourceUrl": self.source_url,
            "title": self.title,
            "author": self.author,
            "imageUrl": self.image_url,
            "summary": self.summary,
            "ingredients": [i.to_dict() for i in self.ingredients],
            "steps": self.steps,
            "tags": self.tags,
            "totalMinutes": self.total_minutes,
            "servings": self.servings,
        }


@dataclass(frozen=True)
class SearchPage:
    recipes: list[Recipe]
    # how many results the provider has for these filters in total
    total: int
