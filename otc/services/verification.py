"""Glue between a deal and the chain provider: persist checks + advance status."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from ..extensions import db
from ..models import AWAITING_DEPOSIT, CRYPTO_CONFIRMED, CRYPTO_PENDING, ChainCheck, Deal, User
from . import chain
from .audit import audit
from .deals import transition


def run_check(deal: Deal, txid: str, actor: User) -> chain.ChainResult:
    result = chain.verify_transfer(
        network=deal.network,
        txid=txid,
        expected=Decimal(deal.amount_crypto),
        to_address=deal.deposit_address,
        required=deal.confirmations_required,
    )

    if txid:
        deal.txid = txid
    deal.confirmations_seen = result.confirmations

    db.session.add(
        ChainCheck(
            deal_id=deal.id,
            txid=txid or "—",
            network=deal.network,
            status=result.status,
            confirmations=result.confirmations,
            required_conf=result.required,
            amount=result.amount,
            from_address=result.from_address,
            to_address=result.to_address,
            provider=result.provider,
            simulated=result.simulated,
            detail=result.detail,
            checked_at=datetime.utcnow(),
        )
    )
    audit(
        "chain.check",
        "deal",
        deal.id,
        ref=deal.ref,
        status=result.status,
        confirmations=result.confirmations,
        provider=result.provider,
        simulated=result.simulated,
    )

    if result.status == chain.STATUS_CONFIRMED and deal.status in (AWAITING_DEPOSIT, CRYPTO_PENDING):
        transition(
            deal,
            CRYPTO_CONFIRMED,
            actor,
            force=True,
            note=f"On-chain confirmed ({result.confirmations}/{result.required}).",
        )
    elif deal.status == AWAITING_DEPOSIT and result.status in (chain.STATUS_PENDING, chain.STATUS_MISMATCH):
        transition(
            deal,
            CRYPTO_PENDING,
            actor,
            force=True,
            note=f"Transaction seen ({result.status}).",
        )
    else:
        db.session.commit()

    return result
