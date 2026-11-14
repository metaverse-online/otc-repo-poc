from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..models import (
    AGREED,
    AWAITING_DEPOSIT,
    CRYPTO_CONFIRMED,
    CRYPTO_PENDING,
    DISPUTED,
    FIAT_SENT,
    NETWORKS,
    OPEN_STATUSES,
    REJECTED,
    SETTLED,
    AuditLog,
    ChainCheck,
    Deal,
    PlatformWallet,
    Rate,
    User,
    WalletAddress,
)
from ..security import admin_required
from ..services.audit import audit
from ..services.deals import client_exposure_ngn, ensure_instructions, transition
from ..services.files import save_receipt
from ..services.verification import run_check

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _deal(deal_id: int) -> Deal:
    deal = db.session.get(Deal, deal_id)
    if not deal:
        abort(404)
    return deal


@bp.get("/")
@admin_required
def overview():
    since = datetime.utcnow() - timedelta(days=30)
    month = datetime.utcnow() - timedelta(days=30)

    settled = Deal.query.filter(Deal.status == SETTLED)
    volume_30d = (
        db.session.query(func.coalesce(func.sum(Deal.fiat_amount), 0))
        .filter(Deal.status == SETTLED, Deal.settled_at >= since)
        .scalar()
    )
    fees_30d = (
        db.session.query(func.coalesce(func.sum(Deal.fee_ngn), 0))
        .filter(Deal.status == SETTLED, Deal.settled_at >= since)
        .scalar()
    )
    exposure = (
        db.session.query(func.coalesce(func.sum(Deal.fiat_amount), 0))
        .filter(Deal.status.in_(OPEN_STATUSES))
        .scalar()
    )

    clients = User.query.filter_by(role="client").all()
    pending_kyc = sum(1 for c in clients if c.kyc_status == "pending")
    pending_wallets = WalletAddress.query.filter_by(whitelisted=False).count()
    disputes = Deal.query.filter_by(status=DISPUTED).count()

    # 14-day settled volume chart
    chart = []
    for offset in range(13, -1, -1):
        day = (datetime.utcnow() - timedelta(days=offset)).date()
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        total = (
            db.session.query(func.coalesce(func.sum(Deal.fiat_amount), 0))
            .filter(Deal.settled_at >= start, Deal.settled_at < end, Deal.status == SETTLED)
            .scalar()
        )
        chart.append({"day": day, "value": float(total or 0)})
    peak = max((c["value"] for c in chart), default=0) or 1

    recent = Deal.query.order_by(Deal.created_at.desc()).limit(8).all()
    rates = {r.asset: r for r in Rate.query.all()}
    upcoming = (
        Deal.query.filter(Deal.status.in_((AGREED, CRYPTO_PENDING, CRYPTO_CONFIRMED)))
        .order_by(Deal.updated_at.desc())
        .limit(6)
        .all()
    )

    return render_template(
        "admin/overview.html",
        page_title="Desk overview",
        page_kicker=datetime.utcnow().strftime("%A %d %B %Y · UTC"),
        volume_30d=volume_30d,
        fees_30d=fees_30d,
        exposure=exposure,
        clients_count=len(clients),
        pending_kyc=pending_kyc,
        pending_wallets=pending_wallets,
        disputes=disputes,
        chart=chart,
        peak=peak,
        recent=recent,
        rates=rates,
        upcoming=upcoming,
        settled_count=settled.count(),
        month_count=Deal.query.filter(Deal.created_at >= month).count(),
    )


@bp.post("/rates")
@admin_required
def rates_update():
    for asset in ("BTC", "USDT"):
        raw = (request.form.get(f"rate_{asset}") or "").strip()
        if not raw:
            continue
        try:
            value = Decimal(raw.replace(",", ""))
        except Exception:  # noqa: BLE001
            flash(f"Invalid rate for {asset}.", "danger")
            return redirect(url_for("admin.overview"))
        rate = Rate.query.filter_by(asset=asset).first()
        if not rate:
            rate = Rate(asset=asset, ngn_rate=value)
            db.session.add(rate)
        else:
            rate.ngn_rate = value
            rate.updated_at = datetime.utcnow()
        audit("rate.update", "rate", asset, value=str(value))
    db.session.commit()
    flash("Reference rates updated.", "success")
    return redirect(url_for("admin.overview"))


@bp.get("/queue")
@admin_required
def queue():
    base = Deal.query
    counts = {
        "action": base.filter(Deal.status.in_((AGREED, CRYPTO_CONFIRMED))).count(),
        "flight": base.filter(Deal.status.in_((AWAITING_DEPOSIT, CRYPTO_PENDING, FIAT_SENT))).count(),
        "disputes": base.filter_by(status=DISPUTED).count(),
    }

    requested = request.args.get("tab")
    if requested in counts:
        tab = requested
    else:
        tab = next((name for name in ("action", "flight", "disputes") if counts[name]), "action")

    if tab == "flight":
        deals = base.filter(Deal.status.in_((AWAITING_DEPOSIT, CRYPTO_PENDING, FIAT_SENT))).order_by(
            Deal.updated_at.asc()
        ).all()
    elif tab == "disputes":
        deals = base.filter_by(status=DISPUTED).order_by(Deal.updated_at.asc()).all()
    else:
        deals = base.filter(Deal.status.in_((AGREED, CRYPTO_CONFIRMED))).order_by(Deal.updated_at.asc()).all()
    return render_template(
        "admin/queue.html",
        page_title="Deal queue",
        page_kicker="Approval & verification",
        deals=deals,
        tab=tab,
        counts=counts,
    )


@bp.post("/deals/<int:deal_id>/action")
@admin_required
def deal_action(deal_id: int):
    deal = _deal(deal_id)
    action = request.form.get("action", "")

    if action == "publish":
        if deal.status != AGREED:
            abort(409, description="Only agreed deals can receive settlement instructions.")
        ensure_instructions(deal)
        if not deal.deposit_address:
            flash("Configure the desk settlement wallet for this network first.", "danger")
            return redirect(url_for("admin.verify"))
        transition(deal, AWAITING_DEPOSIT, current_user, note="Settlement instructions published.")

    elif action == "release":
        if deal.status != CRYPTO_CONFIRMED:
            abort(409, description="Crypto leg is not confirmed yet.")
        reference = (request.form.get("fiat_reference") or "").strip()
        if len(reference) < 4:
            flash("Enter the bank transfer reference before releasing fiat.", "danger")
            return redirect(url_for("portal.deal_detail", deal_id=deal.id))
        deal.fiat_reference = reference[:80]
        uploaded = save_receipt(request.files.get("receipt"))
        if uploaded:
            deal.receipt_file = uploaded
        transition(deal, FIAT_SENT, current_user, note=f"NGN released · ref {reference}.")

    elif action == "settle":
        transition(deal, SETTLED, current_user, note="Deal closed and archived.")

    elif action == "reject":
        reason = (request.form.get("reason") or "").strip()
        if deal.status == SETTLED:
            abort(409, description="A settled deal cannot be rejected.")
        transition(deal, REJECTED, current_user, note=reason or "Rejected by the desk.")

    elif action == "recheck":
        txid = (request.form.get("txid") or deal.txid or "").strip()
        result = run_check(deal, txid, actor=current_user)
        flash(f"Verification: {result.status} — {result.detail}", "success" if result.ok else "warn")

    elif action == "note":
        from ..models import Message

        body = (request.form.get("body") or "").strip()
        if body:
            db.session.add(Message(deal_id=deal.id, author_id=current_user.id, body=body[:4000]))
            audit("deal.message", "deal", deal.id, ref=deal.ref)
            db.session.commit()
            flash("Internal note added to the thread.", "success")

    else:
        abort(400, description="Unknown action.")

    return redirect(request.form.get("next") or url_for("portal.deal_detail", deal_id=deal.id))


@bp.get("/verify")
@admin_required
def verify():
    wallets = {w.network: w for w in PlatformWallet.query.all()}
    open_deals = (
        Deal.query.filter(Deal.status.in_((AGREED, AWAITING_DEPOSIT, CRYPTO_PENDING, CRYPTO_CONFIRMED)))
        .order_by(Deal.created_at.desc())
        .limit(60)
        .all()
    )
    checks = ChainCheck.query.order_by(ChainCheck.checked_at.desc()).limit(20).all()
    return render_template(
        "admin/verify.html",
        page_title="On-chain verification",
        page_kicker="Provider console",
        wallets=wallets,
        open_deals=open_deals,
        checks=checks,
    )


@bp.post("/wallets")
@admin_required
def wallets_save():
    for network in NETWORKS:
        address = (request.form.get(f"address_{network}") or "").strip()
        label = (request.form.get(f"label_{network}") or "").strip()[:80] or "Desk treasury"
        wallet = PlatformWallet.query.filter_by(network=network).first()
        if not wallet:
            wallet = PlatformWallet(network=network, address=address, label=label)
            db.session.add(wallet)
        else:
            if address != wallet.address:
                audit("wallet.desk_update", "platform_wallet", network, address=address)
            wallet.address = address
            wallet.label = label
            wallet.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Desk settlement wallets saved.", "success")
    return redirect(url_for("admin.verify"))


@bp.post("/verify")
@admin_required
def verify_run():
    deal = _deal(int(request.form.get("deal_id") or 0))
    txid = (request.form.get("txid") or "").strip()
    result = run_check(deal, txid, actor=current_user)
    flash(
        f"{deal.ref}: {result.status} · {result.confirmations}/{result.required} confirmations",
        "success" if result.ok else "warn",
    )
    return redirect(url_for("admin.verify"))


@bp.get("/clients")
@admin_required
def clients():
    people = User.query.filter_by(role="client").order_by(User.created_at.desc()).all()
    exposure = {c.id: client_exposure_ngn(c.id) for c in people}
    pending_wallets = (
        WalletAddress.query.filter_by(whitelisted=False)
        .join(User, WalletAddress.user_id == User.id)
        .order_by(WalletAddress.created_at.desc())
        .all()
    )
    return render_template(
        "admin/clients.html",
        page_title="Clients",
        page_kicker="Limits, KYC & risk",
        clients=people,
        exposure=exposure,
        pending_wallets=pending_wallets,
        deal_counts={row[0]: row[1] for row in db.session.query(Deal.initiator_id, func.count(Deal.id)).group_by(Deal.initiator_id)},
    )


@bp.post("/clients/<int:user_id>/action")
@admin_required
def client_action(user_id: int):
    client = db.session.get(User, user_id)
    if not client or client.role != "client":
        abort(404)
    action = request.form.get("action", "")

    if action == "kyc_verify":
        client.kyc_status = "verified"
        audit("client.kyc", "user", client.id, status="verified")
    elif action == "kyc_reject":
        client.kyc_status = "rejected"
        audit("client.kyc", "user", client.id, status="rejected")
    elif action == "toggle_risk":
        client.risk_flag = not client.risk_flag
        audit("client.risk", "user", client.id, risk=client.risk_flag)
    elif action == "limit":
        raw = (request.form.get("monthly_limit_ngn") or "").replace(",", "").strip()
        try:
            client.monthly_limit_ngn = Decimal(raw)
        except Exception:  # noqa: BLE001
            flash("Invalid limit value.", "danger")
            return redirect(url_for("admin.clients"))
        audit("client.limit", "user", client.id, limit=raw)
    elif action == "toggle_active":
        client.is_active_user = not client.is_active_user
        audit("client.active", "user", client.id, active=client.is_active_user)
    else:
        abort(400, description="Unknown action.")

    db.session.commit()
    flash("Client updated.", "success")
    return redirect(url_for("admin.clients"))


@bp.post("/wallets/<int:wallet_id>/action")
@admin_required
def wallet_action(wallet_id: int):
    wallet = db.session.get(WalletAddress, wallet_id)
    if not wallet:
        abort(404)
    action = request.form.get("action")
    if action == "approve":
        wallet.whitelisted = True
        audit("wallet.whitelist", "wallet", wallet.address, user=wallet.user_id, approved=True)
        flash("Address whitelisted.", "success")
    elif action == "revoke":
        wallet.whitelisted = False
        audit("wallet.whitelist", "wallet", wallet.address, user=wallet.user_id, approved=False)
        flash("Whitelist revoked.", "info")
    else:
        abort(400, description="Unknown action.")
    db.session.commit()
    return redirect(request.referrer or url_for("admin.clients"))


@bp.get("/audit")
@admin_required
def audit_log():
    entity = request.args.get("entity", "").strip()
    query = AuditLog.query
    if entity:
        query = query.filter(AuditLog.entity == entity)
    entries = query.order_by(AuditLog.created_at.desc()).limit(300).all()
    entities = [row[0] for row in db.session.query(AuditLog.entity).distinct().order_by(AuditLog.entity)]
    return render_template(
        "admin/audit.html",
        page_title="Audit log",
        page_kicker="Every state change, append-only",
        entries=entries,
        entity=entity,
        entities=entities,
    )
