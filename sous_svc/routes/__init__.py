from flask import Flask

from .chat import bp as chat_bp
from .health import bp as health_bp
from .saved import bp as saved_bp
from .search import bp as search_bp


def register_routes(app: Flask) -> None:
    app.register_blueprint(health_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(saved_bp)
    app.register_blueprint(chat_bp)
