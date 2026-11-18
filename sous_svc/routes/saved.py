from flask import Blueprint, abort, current_app, jsonify

from ..auth import current_user_id, require_user
from ..store import RecipeNotFound, Store

bp = Blueprint("saved", __name__)


def _store() -> Store:
    return current_app.extensions["sous.store"]


@bp.get("/saved")
@require_user
def list_saved():
    recipes = _store().list_saved(current_user_id())
    return jsonify(recipes=[r.to_dict() for r in recipes])


@bp.put("/saved/<recipe_id>")
@require_user
def save(recipe_id: str):
    try:
        _store().save_recipe(current_user_id(), recipe_id)
    except RecipeNotFound:
        abort(404, "recipe not found")
    return "", 204


@bp.delete("/saved/<recipe_id>")
@require_user
def unsave(recipe_id: str):
    _store().unsave_recipe(current_user_id(), recipe_id)
    return "", 204
