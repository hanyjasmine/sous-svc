import httpx
import pytest

from sous_svc.recipes import QuotaExceeded, SearchFilters, SpoonacularClient
from sous_svc.recipes.spoonacular import (
    clean_summary,
    format_amount,
    to_params,
    to_recipe,
    to_steps,
)

RAW = {
    "id": 637103,
    "title": "Caribbean Chicken Thighs",
    "image": "https://img.spoonacular.com/recipes/637103-312x231.jpg",
    "readyInMinutes": 45,
    "servings": 2,
    "sourceUrl": "https://www.foodista.com/recipe/JQ2NJZ7B/caribbean-chicken-thighs",
    "sourceName": "Foodista",
    "diets": ["gluten free", "dairy free"],
    "cuisines": ["Central American", "Caribbean"],
    "dishTypes": ["lunch", "main course"],
    "summary": "The recipe <b>Caribbean Chicken Thighs</b> takes 45 minutes. It is spicy. "
    "It serves 2 &amp; is tasty.",
    "extendedIngredients": [
        {
            "aisle": "Meat",
            "name": "chicken thighs",
            "nameClean": "chicken thighs",
            "amount": 4.0,
            "unit": "",
            "measures": {"us": {"amount": 4.0, "unitShort": ""}},
        },
        {
            "aisle": "Produce;Spices and Seasonings",
            "name": "lime juice",
            "amount": 0.333,
            "measures": {"us": {"amount": 0.333, "unitShort": "cups"}},
        },
    ],
    "analyzedInstructions": [
        {
            "name": "",
            "steps": [{"number": 1, "step": " Marinate. "}, {"number": 2, "step": "Grill."}],
        }
    ],
}


def test_to_recipe_maps_fields():
    r = to_recipe(RAW)
    assert r.id == "spoonacular-637103"
    assert r.image_url == "https://img.spoonacular.com/recipes/637103-636x393.jpg"
    assert r.author == "Foodista"
    assert r.steps == ["Marinate.", "Grill."]
    assert r.ingredients[0].quantity == "4"
    assert r.ingredients[1].quantity == "1/3 cups"
    assert r.ingredients[1].aisle == "produce"
    assert r.tags == ["gluten free", "dairy free", "central american", "main course"]
    assert r.to_dict()["imageUrl"] == r.image_url


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, ""),
        (0, ""),
        (2.0, "2"),
        (0.5, "1/2"),
        (1.5, "1 1/2"),
        (0.25, "1/4"),
        (2.333, "2 1/3"),
    ],
)
def test_format_amount(amount, expected):
    assert format_amount(amount) == expected


def test_clean_summary_strips_html_and_trims():
    assert clean_summary(RAW["summary"]) == (
        "The recipe Caribbean Chicken Thighs takes 45 minutes. It is spicy."
    )


def test_to_params_only_sends_set_filters():
    f = SearchFilters.from_raw("cookies").model_copy(
        update={"diets": ["vegan", "gluten free"], "max_ready_minutes": 30}
    )
    assert to_params(f) == {"query": "cookies", "diet": "vegan,gluten free", "maxReadyTime": 30}


def _client(handler) -> SpoonacularClient:
    http = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    return SpoonacularClient("key", http=http)


def test_search_sends_key_header_and_parses_results():
    def handler(req: httpx.Request):
        assert req.headers["x-api-key"] == "key"
        assert req.url.params["addRecipeInformation"] == "true"
        assert req.url.params["offset"] == "20"
        return httpx.Response(200, json={"results": [RAW], "totalResults": 57})

    page = _client(handler).search(SearchFilters.from_raw("chicken"), offset=20)
    assert [r.title for r in page.recipes] == ["Caribbean Chicken Thighs"]
    assert page.total == 57


def test_search_raises_quota_exceeded_on_402():
    client = _client(lambda req: httpx.Response(402, json={}))
    with pytest.raises(QuotaExceeded):
        client.search(SearchFilters.from_raw("chicken"))


def test_clean_summary_drops_rhetorical_questions():
    raw = "Soup is a main course. Watching your figure? It has 300 calories. Enjoy."
    assert clean_summary(raw) == "Soup is a main course. It has 300 calories."


def test_steps_prefer_source_list():
    raw = {
        "instructions": (
            "<ol><li>Simmer until reduced by 1/3.</li><li>Stir in the chocolate</li></ol>"
        ),
        "analyzedInstructions": [{"steps": [{"step": "Simmer until reduced by 1/"}]}],
    }
    assert to_steps(raw) == ["Simmer until reduced by 1/3.", "Stir in the chocolate"]


def test_steps_split_source_paragraphs_and_strip_numbering():
    raw = {"instructions": "<p>1. Preheat the oven.</p><p>2. Bake &amp; cool .</p>"}
    assert to_steps(raw) == ["Preheat the oven.", "Bake & cool."]


def test_steps_repair_analyzed_fallback():
    raw = {
        "instructions": "",
        "analyzedInstructions": [
            {"steps": [{"step": "Simmer until reduced by 1/"}, {"step": "3. Then cool"}]},
            {"steps": [{"step": "Whisk the cream"}, {"step": "until stiff peaks form."}]},
        ],
    }
    assert to_steps(raw) == [
        "Simmer until reduced by 1/3. Then cool.",
        "Whisk the cream until stiff peaks form.",
    ]


def test_steps_split_single_paragraph_by_sentence():
    raw = {"instructions": "Mix it. Bake it."}
    assert to_steps(raw) == ["Mix it.", "Bake it."]
