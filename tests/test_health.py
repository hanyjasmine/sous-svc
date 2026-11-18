from sous_svc import create_app


def test_health_reports_ok_and_features():
    client = create_app({"TESTING": True}).test_client()
    res = client.get("/health")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ok"
    assert set(body["features"]) == {"web_search_fallback", "nyt_mcp", "nyt_full_recipes"}


def test_nyt_full_recipes_needs_nyt_enabled():
    app = create_app({"NYT_MCP_ENABLED": False, "NYT_MCP_FULL_RECIPES": True})
    features = app.test_client().get("/health").get_json()["features"]
    assert features["nyt_full_recipes"] is False


def test_unknown_route_returns_json_error():
    client = create_app({"TESTING": True}).test_client()
    res = client.get("/nope")
    assert res.status_code == 404
    assert res.get_json()["error"] == "Not Found"
