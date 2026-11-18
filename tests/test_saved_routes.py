import pytest
from conftest import SUPABASE_URL, make_token

from sous_svc import create_app
from sous_svc.recipes import Recipe, SearchFilters
from sous_svc.store import Pool

SOUP = Recipe(id="spoonacular-1", source="spoonacular", title="Soup", summary="Warm.")
STEW = Recipe(id="spoonacular-2", source="spoonacular", title="Stew", summary="Hearty.")


@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SUPABASE_URL": SUPABASE_URL})
    # recipes must exist (from a search) before they can be saved
    app.extensions["sous.store"].create_session(
        "dinner", Pool(SearchFilters.from_raw("dinner"), [SOUP, STEW]), None
    )
    return app.test_client()


def test_saved_requires_sign_in(client):
    assert client.get("/saved").status_code == 401
    assert client.put("/saved/spoonacular-1").status_code == 401


def test_bad_token_is_rejected(client):
    res = client.get("/saved", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_expired_token_is_rejected(client):
    token = make_token(exp=1)
    res = client.get("/saved", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_wrong_issuer_is_rejected(client):
    token = make_token(iss="https://evil.example/auth/v1")
    res = client.get("/saved", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_save_list_unsave(client, auth_header):
    assert client.put("/saved/spoonacular-1", headers=auth_header).status_code == 204
    assert client.put("/saved/spoonacular-2", headers=auth_header).status_code == 204
    # saving twice is a no-op
    assert client.put("/saved/spoonacular-1", headers=auth_header).status_code == 204

    titles = [r["title"] for r in client.get("/saved", headers=auth_header).get_json()["recipes"]]
    assert titles == ["Stew", "Soup"]

    assert client.delete("/saved/spoonacular-2", headers=auth_header).status_code == 204
    titles = [r["title"] for r in client.get("/saved", headers=auth_header).get_json()["recipes"]]
    assert titles == ["Soup"]


def test_saved_lists_are_per_user(client, auth_header):
    client.put("/saved/spoonacular-1", headers=auth_header)
    other = {"Authorization": f"Bearer {make_token(sub='someone-else')}"}
    assert client.get("/saved", headers=other).get_json()["recipes"] == []


def test_save_unknown_recipe_is_404(client, auth_header):
    assert client.put("/saved/spoonacular-999", headers=auth_header).status_code == 404
