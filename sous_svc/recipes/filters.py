import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

# values spoonacular accepts, kept as literals so claude can only pick valid ones
Cuisine = Literal[
    "African", "Asian", "American", "British", "Cajun", "Caribbean", "Chinese",
    "Eastern European", "European", "French", "German", "Greek", "Indian", "Irish",
    "Italian", "Japanese", "Jewish", "Korean", "Latin American", "Mediterranean",
    "Mexican", "Middle Eastern", "Nordic", "Southern", "Spanish", "Thai", "Vietnamese",
]  # fmt: skip
Diet = Literal[
    "gluten free", "ketogenic", "vegetarian", "lacto-vegetarian", "ovo-vegetarian",
    "vegan", "pescetarian", "paleo", "primal", "low fodmap", "whole30",
]  # fmt: skip
Intolerance = Literal[
    "dairy", "egg", "gluten", "grain", "peanut", "seafood", "sesame", "shellfish",
    "soy", "sulfite", "tree nut", "wheat",
]  # fmt: skip
MealType = Literal[
    "main course", "side dish", "dessert", "appetizer", "salad", "bread", "breakfast",
    "soup", "beverage", "sauce", "marinade", "fingerfood", "snack", "drink",
]  # fmt: skip


class SearchFilters(BaseModel):
    query: str = Field(
        description="dish name or main ingredient matched against titles, e.g. 'chicken "
        "thighs'. empty string if the request names neither"
    )
    cuisines: list[Cuisine] = Field(description="only if the user named or clearly implied one")
    diets: list[Diet] = Field(description="dietary patterns the recipe must satisfy")
    intolerances: list[Intolerance] = Field(description="allergens or ingredients to avoid")
    include_ingredients: list[str] = Field(
        description="ingredients the user has or wants used, singular lowercase"
    )
    exclude_ingredients: list[str] = Field(description="ingredients the user dislikes")
    meal_type: MealType | None = Field(description="dish type if clear from the request")
    max_ready_minutes: int | None = Field(description="total time limit in minutes if stated")

    @classmethod
    def from_raw(cls, query: str) -> "SearchFilters":
        return cls(
            query=query,
            cuisines=[],
            diets=[],
            intolerances=[],
            include_ingredients=[],
            exclude_ingredients=[],
            meal_type=None,
            max_ready_minutes=None,
        )

    # keep hard constraints, drop the soft ones that most often zero out results
    def relaxed(self) -> "SearchFilters":
        return self.model_copy(
            update={"cuisines": [], "include_ingredients": [], "meal_type": None}
        )

    def is_relaxable(self) -> bool:
        return bool(self.cuisines or self.include_ingredients or self.meal_type)

    # same filters in any order or casing share one cached pool
    def cache_key(self) -> str:
        norm = {
            k: sorted(str(x).lower() for x in v) if isinstance(v, list) else v
            for k, v in self.model_dump().items()
        }
        norm["query"] = " ".join(self.query.lower().split())
        return hashlib.sha256(json.dumps(norm, sort_keys=True).encode()).hexdigest()[:32]
