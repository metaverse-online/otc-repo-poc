from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User
from ..services.audit import audit

bp = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme:
        return None
    return target if target.startswith("/") else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            audit("auth.login_failed", "user", email)
            db.session.commit()
            flash("Invalid email or password.", "danger")
            return render_template("login.html", email=email), 401

        if not user.is_active:
            flash("This account is disabled. Contact the desk operator.", "danger")
            return render_template("login.html", email=email), 403

        session.clear()
        login_user(user, remember=False)
        user.last_login_at = datetime.utcnow()
        audit("auth.login", "user", user.id, role=user.role)
        db.session.commit()

        destination = _safe_next(request.args.get("next"))
        if destination:
            return redirect(destination)
        return redirect(url_for("admin.overview" if user.is_admin else "portal.overview"))

    return render_template("login.html", email="")


@bp.post("/logout")
@login_required
def logout():
    audit("auth.logout", "user", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"))
