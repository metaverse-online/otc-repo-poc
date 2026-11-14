"""Append-only audit trail helper."""
from __future__ import annotations

import json

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def audit(action: str, entity: str, entity_id: str | int | None = None, **meta) -> AuditLog:
    actor = current_user if getattr(current_user, "is_authenticated", False) else None
    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", "system"),
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        meta=json.dumps(meta, default=str, sort_keys=True) if meta else None,
        ip=request.headers.get("X-Forwarded-For", request.remote_addr) if has_request_context() else None,
    )
    db.session.add(entry)
    return entry
