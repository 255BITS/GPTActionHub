import sys
from pathlib import Path
shared_path = str(Path(__file__).resolve().parent.parent.parent)
sys.path.append(shared_path)

from flask import Flask
import os
from app.routes.actions_routes import actions_bp
from app.routes.auth_routes import auth_bp
from app.routes.api_routes import api_bp
from app.routes.public_routes import public_bp
from app.routes.log_routes import log_bp
from app.services.auth_service import register_app_error_handlers, setup_oauth
from shared.utils import humanize

def create_app():
    app = Flask(__name__)
    secret_key = os.environ.get("FLASK_SECRET")
    if not secret_key or secret_key == "development":
        raise RuntimeError("Set FLASK_SECRET to a private, persistent random secret before starting the portal")
    app.secret_key = secret_key
    app.register_blueprint(actions_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(public_bp)
    register_app_error_handlers(app)
    setup_oauth(app)
    app.jinja_env.filters['zip'] = zip
    app.jinja_env.filters['humanize'] = humanize
    return app
