from flask import Blueprint, current_app, jsonify

from ..config import features

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    return jsonify(status="ok", features=features(current_app.config))
