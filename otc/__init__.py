"""Application factory for the private OTC settlement desk (POC)."""
from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from .config import BASE_DIR, UPLOAD_DIR, Config
from .extensions import db, login_manager

load_dotenv(BASE_DIR / ".env")


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object)
    app.config["UPLOAD_DIR"] = str(UPLOAD_DIR)

    db.init_app(app)
    login_manager.init_app(app)

    _register_security(app)
    _register_jinja(app)
    _register_blueprints(app)
    _register_errors(app)
    _register_cli(app)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.context_processor
    def _inject_globals():
        from .models import AGREED, CRYPTO_CONFIRMED, NETWORKS, STATUS_META, Deal, User

        queue_count = kyc_count = 0
        if current_user.is_authenticated and current_user.is_admin:
            queue_count = Deal.query.filter(Deal.status.in_((AGREED, CRYPTO_CONFIRMED))).count()
            kyc_count = User.query.filter_by(role="client", kyc_status="pending").count()

        return {
            "brand": app.config["BRAND"],
            "env_name": app.config["ENV_NAME"],
            "chain_demo": app.config["CHAIN_DEMO"],
            "networks": NETWORKS,
            "status_meta": STATUS_META,
            "nav_section": request.endpoint.split(".")[0] if request.endpoint else "",
            "admin_queue_count": queue_count,
            "admin_kyc_count": kyc_count,
        }

    @app.get("/")
    def home():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        return redirect(url_for("admin.overview" if current_user.is_admin else "portal.overview"))

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "app": "otc-desk", "env": app.config["ENV_NAME"]})

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return app


def _register_security(app: Flask) -> None:
    from .security import csrf_token, validate_csrf

    app.before_request(validate_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @login_manager.user_loader
    def load_user(user_id: str):
        from .models import User

        return db.session.get(User, int(user_id)) if user_id and user_id.isdigit() else None


def _register_jinja(app: Flask) -> None:
    from .formatting import fmt_crypto, fmt_dt, fmt_ngn, timeago
    from .models import FIAT_METHODS, STATUS_META

    app.jinja_env.filters["ngn"] = fmt_ngn
    app.jinja_env.filters["crypto"] = fmt_crypto
    app.jinja_env.filters["dt"] = fmt_dt
    app.jinja_env.filters["ago"] = timeago
    app.jinja_env.globals["fiat_methods"] = FIAT_METHODS
    app.jinja_env.globals["status_meta"] = STATUS_META

    @app.template_filter("tojson_pretty")
    def _tojson_pretty(value):
        import json

        try:
            return json.dumps(json.loads(value), indent=2)
        except Exception:  # noqa: BLE001
            return value


def _register_blueprints(app: Flask) -> None:
    from .blueprints.admin import bp as admin_bp
    from .blueprints.api import bp as api_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.portal import bp as portal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)


def _register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def _handle_error(err):
        code = getattr(err, "code", 500)
        message = getattr(err, "description", "Something went wrong.")
        if request.path.startswith("/api/"):
            return jsonify({"error": message, "code": code}), code
        return render_template("errors/error.html", code=code, message=message), code

    @app.errorhandler(500)
    def _handle_500(err):  # pragma: no cover
        db.session.rollback()
        return render_template("errors/error.html", code=500, message="Internal error."), 500


def _register_cli(app: Flask) -> None:
    from .cli import register_cli

    register_cli(app)
