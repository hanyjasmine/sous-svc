from sous_svc.recipes import Ingredient, Recipe
from sous_svc.store.supabase import from_row, source_id, to_row


def test_recipe_round_trips_through_a_row():
    recipe = Recipe(
        id="spoonacular-637103",
        source="spoonacular",
        title="Caribbean Chicken Thighs",
        summary="Spicy.",
        ingredients=[Ingredient("chicken thighs", "4", "meat")],
        steps=["Marinate.", "Grill."],
        tags=["gluten free"],
        source_url="https://example.com",
        image_url="https://img.spoonacular.com/recipes/637103-636x393.jpg",
        total_minutes=45,
        servings=2,
    )
    row = to_row(recipe)
    assert row["source_id"] == "637103"
    row["id"] = "db-uuid"
    assert from_row(row) == recipe


def test_source_id_keeps_dashes_after_the_prefix():
    assert source_id("web-some-site-slug") == "some-site-slug"
