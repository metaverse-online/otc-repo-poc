"""Minimal CSRF + role helpers (POC-grade, dependency-free)."""
from __future__ import annotations

import secrets
from functools import wraps

from flask import abort, request, session
from flask_login import current_user, login_required


def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def validate_csrf() -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("_csrf")
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        abort(400, description="Missing or invalid CSRF token. Reload the page and retry.")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403, description="Administrator access required.")
        return view(*args, **kwargs)

    return wrapper
