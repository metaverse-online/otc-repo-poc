"""Tiny JSON surface used by the deal page live polling."""
from __future__ import annotations

from flask import Blueprint, abort, jsonify
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Deal

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/deals/<int:deal_id>/status")
@login_required
def deal_status(deal_id: int):
    deal = db.session.get(Deal, deal_id)
    if not deal:
        abort(404)
    if not deal.can_view(current_user):
        abort(403)

    last = deal.checks[0] if deal.checks else None
    return jsonify(
        {
            "ref": deal.ref,
            "status": deal.status,
            "label": deal.status_label,
            "tone": deal.status_tone,
            "txid": deal.txid,
            "confirmations": deal.confirmations_seen,
            "required": deal.confirmations_required,
            "explorer": deal.explorer_url,
            "last_check": None
            if not last
            else {
                "status": last.status,
                "confirmations": last.confirmations,
                "required": last.required_conf,
                "provider": last.provider,
                "simulated": last.simulated,
                "checked_at": last.checked_at.isoformat(timespec="seconds"),
            },
        }
    )
