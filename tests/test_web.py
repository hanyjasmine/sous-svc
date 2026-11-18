import json

import pytest
from conftest import FakeProvider, recipe

from sous_svc import create_app
from sous_svc.recipes import QuotaExceeded, SearchFilters
from sous_svc.web import WebTopup
from sous_svc.web.fetch import is_public_url
from sous_svc.web.jsonld import (
    canonical_url,
    duration_minutes,
    extract_recipe,
    parse_ingredient,
    web_id,
)

RECIPE_LD = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "WebSite", "name": "Example Kitchen"},
        {
            "@type": ["Recipe"],
            "name": "Maple Roasted Carrots",
            "description": "<p>Sweet &amp; sticky. Great for a crowd. Want more?</p>",
            "author": [{"@type": "Person", "name": "Sam Cook"}],
            "image": [{"@type": "ImageObject", "url": "https://img.example.com/carrots.jpg"}],
            "recipeYield": ["6", "6 servings"],
            "prepTime": "PT10M",
            "cookTime": "PT35M",
            "recipeCuisine": "American",
            "recipeCategory": "Side Dish, Thanksgiving",
            "recipeIngredient": [
                "2 lbs carrots, peeled",
                "3 tablespoons maple syrup",
                "1 (14 oz) can chickpeas",
                "salt to taste",
            ],
            "recipeInstructions": [
                {
                    "@type": "HowToSection",
                    "name": "Roast",
                    "itemListElement": [
                        {"@type": "HowToStep", "text": "1. Heat the oven to 425F."},
                        {"@type": "HowToStep", "text": "Toss carrots with syrup."},
                    ],
                },
                {"@type": "HowToStep", "text": "Roast 35 minutes."},
            ],
        },
    ],
}


def page(ld) -> str:
    return f'<html><script type="application/ld+json">{json.dumps(ld)}</script></html>'


def test_extract_recipe_from_graph():
    r = extract_recipe(page(RECIPE_LD), "https://www.example.com/carrots/?utm_source=x")
    assert r.title == "Maple Roasted Carrots"
    assert r.source == "web"
    assert r.source_url == "https://example.com/carrots"
    assert r.id == web_id("https://example.com/carrots")
    assert r.author == "Sam Cook"
    assert r.image_url == "https://img.example.com/carrots.jpg"
    assert r.servings == 6
    assert r.total_minutes == 45
    assert r.summary == "Sweet & sticky. Great for a crowd."
    assert r.steps == ["Heat the oven to 425F.", "Toss carrots with syrup.", "Roast 35 minutes."]
    assert r.tags == ["american", "side dish", "thanksgiving"]
    assert r.ingredients[0].quantity == "2 lbs"
    assert r.ingredients[0].name == "carrots, peeled"


def test_page_without_full_recipe_is_skipped():
    thin = {"@type": "Recipe", "name": "Teaser", "recipeIngredient": ["1 egg"]}
    assert extract_recipe(page(thin), "https://example.com/x") is None
    assert extract_recipe("<html>no data</html>", "https://example.com/x") is None
    assert (
        extract_recipe('<script type="application/ld+json">{oops</script>', "https://a.co") is None
    )


@pytest.mark.parametrize(
    ("line", "quantity", "name"),
    [
        ("3 tablespoons maple syrup", "3 tablespoons", "maple syrup"),
        ("1 1/2 cups (190g) flour", "1 1/2 cups", "flour"),
        ("½ tsp. salt", "½ tsp.", "salt"),
        ("2-3 cloves garlic", "2-3 cloves", "garlic"),
        ("salt to taste", None, "salt to taste"),
        ("6-8 medium potatoes ((if large, cut in half)*)", "6-8", "medium potatoes"),
        ("1 tsp sea salt ((divided))", "1 tsp", "sea salt"),
    ],
)
def test_parse_ingredient(line, quantity, name):
    ing = parse_ingredient(line)
    assert (ing.quantity, ing.name) == (quantity, name)


def test_duration_minutes():
    assert duration_minutes("PT1H30M") == 90
    assert duration_minutes("PT45M") == 45
    assert duration_minutes("P0DT2H") == 120
    assert duration_minutes("soon") is None


def test_canonical_url_ignores_tracking_and_slash():
    assert canonical_url("http://www.a.com/r/1/?x=1#top") == "https://a.com/r/1"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.5/",
        "file:///etc/passwd",
        "ftp://a.com",
    ],
)
def test_private_and_odd_urls_are_blocked(url):
    assert not is_public_url(url)


# web pages keyed by url, every one a valid recipe unless listed in broken
def fake_web(titles: dict[str, str], broken=()):
    def find_urls(request, filters, model, have):
        find_urls.calls.append(request)
        return list(titles)

    find_urls.calls = []

    def fetch(url, http):
        if url in broken:
            return "<html></html>"
        return page({**RECIPE_LD["@graph"][1], "name": titles[url]})

    return find_urls, fetch


@pytest.fixture
def make_client(monkeypatch):
    def make(provider, titles, broken=()):
        monkeypatch.setattr(
            "sous_svc.routes.search.parse_query", lambda q, m: SearchFilters.from_raw("carrots")
        )
        app = create_app({"TESTING": True})
        app.extensions["sous.spoonacular"] = provider
        find_urls, fetch = fake_web(titles, broken)
        app.extensions["sous.web"] = WebTopup(
            app.extensions["sous.store"],
            model="m",
            enabled=True,
            below=15,
            find_urls=find_urls,
            fetch=fetch,
            # run the job inline instead of on a thread
            submit=lambda fn, *args: fn(*args),
        )
        return app.test_client(), find_urls

    return make


WEB = {"https://a.com/1": "Web Carrots", "https://b.com/2": "Web Glazed Carrots"}


def test_thin_results_get_web_topup(make_client):
    client, find_urls = make_client(FakeProvider([[recipe(1, "Api Carrots")]]), WEB)
    body = client.post("/search", json={"query": "carrot sides"}).get_json()
    assert find_urls.calls == ["carrot sides"]

    session = client.get(f"/sessions/{body['id']}").get_json()
    assert [r["title"] for r in session["recipes"]] == [
        "Api Carrots",
        "Web Carrots",
        "Web Glazed Carrots",
    ]
    assert session["webPending"] is False
    assert session["total"] == 3


def test_pages_without_recipes_are_dropped(make_client):
    client, _ = make_client(FakeProvider([[]]), WEB, broken={"https://b.com/2"})
    body = client.post("/search", json={"query": "carrots"}).get_json()
    session = client.get(f"/sessions/{body['id']}").get_json()
    assert [r["title"] for r in session["recipes"]] == ["Web Carrots"]


def test_plenty_of_results_skip_the_web(make_client):
    client, find_urls = make_client(FakeProvider([[recipe(i) for i in range(20)]]), WEB)
    client.post("/search", json={"query": "carrots"})
    assert find_urls.calls == []


def test_web_stands_in_when_quota_is_used_up(make_client):
    client, _ = make_client(FakeProvider(error=QuotaExceeded("used up")), WEB)
    res = client.post("/search", json={"query": "carrots"})
    assert res.status_code == 201
    session = client.get(f"/sessions/{res.get_json()['id']}").get_json()
    assert len(session["recipes"]) == 2
    assert {r["source"] for r in session["recipes"]} == {"web"}


def test_topped_up_pool_is_cached_with_web_results(make_client):
    provider = FakeProvider([[recipe(1)]])
    client, find_urls = make_client(provider, WEB)
    client.post("/search", json={"query": "carrots"})
    again = client.post("/search", json={"query": "carrots again"}).get_json()
    assert len(provider.calls) == 1
    assert len(find_urls.calls) == 1
    assert again["total"] == 3


def test_web_results_for_a_replaced_pool_are_dropped(make_client):
    client, _ = make_client(FakeProvider([[recipe(1)]]), WEB)
    app = client.application
    store = app.extensions["sous.store"]
    web = app.extensions["sous.web"]
    # hold the job, swap the pool, then let it finish
    held = []
    web._submit = lambda fn, *args: held.append((fn, args))
    body = client.post("/search", json={"query": "carrots"}).get_json()
    assert body["webPending"] is True

    session = store.get_session(body["id"])
    from sous_svc.store import Pool

    store.replace_pool(session, Pool(SearchFilters.from_raw("cake"), [recipe(9, "Cake")]))
    fn, args = held[0]
    fn(*args)
    after = client.get(f"/sessions/{body['id']}").get_json()
    assert [r["title"] for r in after["recipes"]] == ["Cake"]
    assert after["webPending"] is False


def test_tags_use_one_wording():
    node = {**RECIPE_LD["@graph"][1], "recipeCuisine": "United States", "recipeCategory": ["Sides"]}
    assert extract_recipe(page(node), "https://a.com/x").tags == ["american", "side dish"]


def test_single_paragraph_steps_split_by_sentence():
    para = "Heat the oven. " * 20 + "Bake until golden. Serve warm."
    node = {**RECIPE_LD["@graph"][1], "recipeInstructions": para}
    steps = extract_recipe(page(node), "https://a.com/x").steps
    assert steps[-2:] == ["Bake until golden.", "Serve warm."]
