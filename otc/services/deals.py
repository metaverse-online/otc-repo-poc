"""Deal state machine + quoting helpers.

The whole point of the product lives here: the fiat leg can only be released
once the crypto leg is confirmed on-chain. Everything else is plumbing.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from flask import abort
from flask_login import current_user

from ..config import Config
from ..extensions import db
from ..models import (
    AGREED,
    AWAITING_DEPOSIT,
    CRYPTO_CONFIRMED,
    CRYPTO_PENDING,
    CREATED,
    DISPUTED,
    FIAT_SENT,
    NEGOTIATION,
    REJECTED,
    SETTLED,
    Deal,
    PlatformWallet,
    User,
)
from .audit import audit

# status -> statuses reachable through an explicit user action
TRANSITIONS: dict[str, tuple[str, ...]] = {
    CREATED: (NEGOTIATION, AGREED, REJECTED),
    NEGOTIATION: (AGREED, REJECTED, DISPUTED),
    AGREED: (AWAITING_DEPOSIT, DISPUTED, REJECTED),
    AWAITING_DEPOSIT: (CRYPTO_PENDING, CRYPTO_CONFIRMED, DISPUTED, REJECTED),
    CRYPTO_PENDING: (CRYPTO_CONFIRMED, AWAITING_DEPOSIT, DISPUTED),
    CRYPTO_CONFIRMED: (FIAT_SENT, DISPUTED, REJECTED),
    FIAT_SENT: (SETTLED, DISPUTED),
    SETTLED: (DISPUTED,),
    DISPUTED: (AWAITING_DEPOSIT, CRYPTO_CONFIRMED, FIAT_SENT, REJECTED),
    REJECTED: (),
}

PERMISSIONS: dict[str, set[str]] = {
    # transition target -> who may trigger it
    NEGOTIATION: {"owner"},
    AGREED: {"owner", "counterparty", "admin"},
    AWAITING_DEPOSIT: {"admin"},
    CRYPTO_PENDING: {"owner", "admin"},
    CRYPTO_CONFIRMED: {"admin"},
    FIAT_SENT: {"admin"},
    SETTLED: {"admin"},
    DISPUTED: {"owner", "counterparty", "admin"},
    REJECTED: {"admin"},
}


def quantize_ngn(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quote(amount_crypto: Decimal, rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Return (gross_ngn, fee_ngn, net_ngn)."""
    gross = quantize_ngn(amount_crypto * rate)
    fee = quantize_ngn(gross * Decimal(Config.FEE_BPS) / Decimal(10000))
    return gross, fee, gross - fee


def role_for(deal: Deal, user: User) -> str:
    if user.is_admin:
        return "admin"
    if deal.initiator_id == user.id:
        return "owner"
    if deal.counterparty_id == user.id:
        return "counterparty"
    return "none"


def can_transition(deal: Deal, target: str, user: User) -> bool:
    if target not in TRANSITIONS.get(deal.status, ()):  # legal edge in the graph
        return False
    return role_for(deal, user) in PERMISSIONS.get(target, set())


def transition(deal: Deal, target: str, user: User, *, note: str | None = None, force: bool = False) -> None:
    """Apply a status change, enforcing the graph + the fiat-release guard."""
    if not force and not can_transition(deal, target, user):
        abort(403, description=f"Cannot move deal from {deal.status} to {target}.")

    # Hard guard from the specification: no fiat before confirmed crypto.
    if target == FIAT_SENT and deal.status != CRYPTO_CONFIRMED:
        abort(409, description="Fiat cannot be released before the crypto leg is confirmed on-chain.")
    if target == SETTLED and deal.status != FIAT_SENT:
        abort(409, description="A deal can only settle after the fiat leg is marked as sent.")

    previous = deal.status
    deal.status = target
    deal.updated_at = datetime.utcnow()
    if target == SETTLED:
        deal.settled_at = datetime.utcnow()

    audit("deal.transition", "deal", deal.id, ref=deal.ref, frm=previous, to=target, note=note)
    if note:
        db.session.add(_system_message(deal, f"Status: {previous} → {target}. {note}", user))
    db.session.commit()


def _system_message(deal: Deal, body: str, user: User):
    from ..models import Message

    return Message(deal_id=deal.id, author_id=user.id, body=body)


def ensure_instructions(deal: Deal) -> None:
    """Snapshot the desk deposit address + payout details when instructions go out."""
    wallet = PlatformWallet.query.filter_by(network=deal.network).first()
    if wallet and wallet.address:
        deal.deposit_address = wallet.address
    if not deal.confirmations_required:
        from ..models import NETWORKS

        deal.confirmations_required = NETWORKS[deal.network]["required_conf"]


def client_exposure_ngn(client_id: int) -> Decimal:
    """Open (not settled / not rejected) fiat exposure for one client."""
    from sqlalchemy import func

    total = (
        db.session.query(func.coalesce(func.sum(Deal.fiat_amount), 0))
        .filter(
            Deal.initiator_id == client_id,
            Deal.status.notin_((SETTLED, REJECTED)),
        )
        .scalar()
    )
    return Decimal(total or 0)


def within_limit(client: User, amount_ngn: Decimal) -> bool:
    if not client.monthly_limit_ngn:
        return True
    return client_exposure_ngn(client.id) + amount_ngn <= Decimal(client.monthly_limit_ngn)
