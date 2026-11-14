from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import (
    AGREED,
    AWAITING_DEPOSIT,
    CREATED,
    CRYPTO_CONFIRMED,
    CRYPTO_PENDING,
    DISPUTED,
    FIAT_METHODS,
    FIAT_SENT,
    NETWORKS,
    NEGOTIATION,
    OPEN_STATUSES,
    REJECTED,
    SETTLED,
    ChainCheck,
    Deal,
    Message,
    Rate,
    User,
    WalletAddress,
    network_asset,
)
from ..services import chain
from ..services.verification import run_check
from ..services.audit import audit
from ..services.deals import (
    can_transition,
    quote,
    role_for,
    transition,
    within_limit,
)

bp = Blueprint("portal", __name__, url_prefix="/portal")


def _decimal(raw: str | None, field: str, *, minimum: Decimal = Decimal("0")) -> Decimal:
    try:
        value = Decimal((raw or "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        abort(400, description=f"Invalid number for {field}.")
    if value <= minimum:
        abort(400, description=f"{field} must be greater than {minimum}.")
    return value


def _own_deal(deal_id: int) -> Deal:
    deal = db.session.get(Deal, deal_id)
    if not deal:
        abort(404)
    if not deal.can_view(current_user):
        abort(403, description="This deal belongs to another counterparty.")
    return deal


@bp.get("/")
@login_required
def home():
    return redirect(url_for("portal.overview"))


@bp.get("/overview")
@login_required
def overview():
    since = datetime.utcnow() - timedelta(days=30)
    my_deals = Deal.query.filter(
        (Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id)
    )

    settled_30d = (
        db.session.query(func.coalesce(func.sum(Deal.fiat_amount), 0))
        .filter(
            ((Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id)),
            Deal.status == SETTLED,
            Deal.settled_at >= since,
        )
        .scalar()
    )
    fees_30d = (
        db.session.query(func.coalesce(func.sum(Deal.fee_ngn), 0))
        .filter(
            Deal.initiator_id == current_user.id,
            Deal.status == SETTLED,
            Deal.settled_at >= since,
        )
        .scalar()
    )

    deals = my_deals.order_by(Deal.created_at.desc()).limit(8).all()
    open_deals = my_deals.filter(Deal.status.in_(OPEN_STATUSES)).count()
    awaiting_me = my_deals.filter(Deal.status.in_((AGREED,),)).count()

    return render_template(
        "portal/overview.html",
        page_title="Overview",
        page_kicker=current_user.company or "Counterparty desk",
        deals=deals,
        open_deals=open_deals,
        awaiting_me=awaiting_me,
        settled_30d=settled_30d,
        fees_30d=fees_30d,
        wallets=current_user.wallets,
    )


@bp.get("/deals")
@login_required
def deals():
    status = request.args.get("status", "").strip()
    query = Deal.query.filter(
        (Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id)
    )
    if status in OPEN_STATUSES + (SETTLED, REJECTED, DISPUTED):
        query = query.filter(Deal.status == status)
    items = query.order_by(Deal.created_at.desc()).limit(100).all()
    return render_template(
        "portal/deals.html",
        page_title="Deals",
        page_kicker="Your settlement book",
        deals=items,
        active_status=status,
        statuses=[
            (CREATED, "Draft"),
            (NEGOTIATION, "Negotiating"),
            (AGREED, "Agreed"),
            (AWAITING_DEPOSIT, "Awaiting crypto"),
            (CRYPTO_PENDING, "Unconfirmed"),
            (CRYPTO_CONFIRMED, "Confirmed"),
            (FIAT_SENT, "Fiat sent"),
            (SETTLED, "Settled"),
            (DISPUTED, "Disputed"),
        ],
    )


@bp.route("/deals/new", methods=["GET", "POST"])
@login_required
def deal_new():
    if current_user.is_admin:
        abort(403, description="Admins review deals; they do not initiate them.")

    if current_user.kyc_status != "verified":
        flash("Your KYC file is not verified yet. The desk must approve it before you can trade.", "warn")
        return redirect(url_for("portal.overview"))

    counterparties = (
        User.query.filter(User.role == "client", User.id != current_user.id, User.is_active_user.is_(True))
        .order_by(User.company)
        .all()
    )
    rates = {r.asset: r.ngn_rate for r in Rate.query.all()}

    if request.method == "POST":
        network = request.form.get("network", "")
        if network not in NETWORKS:
            abort(400, description="Unknown network.")
        amount = _decimal(request.form.get("amount_crypto"), "Amount")
        rate = _decimal(request.form.get("rate_ngn"), "Rate")
        fiat_method = request.form.get("fiat_method", "BANK_TRANSFER")
        if fiat_method not in FIAT_METHODS:
            abort(400, description="Unknown settlement method.")

        gross, fee, _net = quote(amount, rate)
        if not within_limit(current_user, gross):
            flash(
                f"Deal notional {gross:,.2f} NGN exceeds your remaining monthly limit "
                f"({current_user.monthly_limit_ngn:,.2f} NGN). Contact the desk to raise it.",
                "danger",
            )
            return redirect(url_for("portal.deal_new"))

        counterparty_id = request.form.get("counterparty_id") or None
        if counterparty_id:
            cp = db.session.get(User, int(counterparty_id))
            if not cp or cp.role != "client" or cp.id == current_user.id:
                abort(400, description="Invalid counterparty.")

        deal = Deal(
            initiator_id=current_user.id,
            counterparty_id=int(counterparty_id) if counterparty_id else None,
            network=network,
            amount_crypto=amount,
            rate_ngn=rate,
            fiat_amount=gross,
            fee_ngn=fee,
            fiat_method=fiat_method,
            payout_bank=(request.form.get("payout_bank") or "").strip()[:160] or None,
            payout_address=(request.form.get("payout_address") or "").strip()[:160] or None,
            notes=(request.form.get("notes") or "").strip()[:2000] or None,
            confirmations_required=NETWORKS[network]["required_conf"],
            status=CREATED,
        )
        db.session.add(deal)
        db.session.flush()  # get the id/ref for the audit trail
        audit("deal.create", "deal", deal.id, ref=deal.ref, fiat=str(gross), asset=deal.asset)
        db.session.add(
            Message(
                deal_id=deal.id,
                author_id=current_user.id,
                body=f"Deal opened: {amount:,.8f} {deal.asset} @ {rate:,.2f} NGN → {gross:,.2f} NGN.",
            )
        )
        db.session.commit()
        flash(f"Deal {deal.ref} created. Send it to your counterparty when ready.", "success")
        return redirect(url_for("portal.deal_detail", deal_id=deal.id))

    return render_template(
        "portal/deal_new.html",
        page_title="New deal",
        page_kicker="Structured settlement request",
        counterparties=counterparties,
        rates=rates,
        wallets=WalletAddress.query.filter_by(user_id=current_user.id, whitelisted=True).order_by(WalletAddress.created_at.desc()).all(),
    )


@bp.get("/deals/<int:deal_id>")
@login_required
def deal_detail(deal_id: int):
    deal = _own_deal(deal_id)
    actions = []
    for target, label, tone in (
        ("send", "Send to counterparty", "primary"),
        ("accept", "Accept terms", "primary"),
        ("submit_tx", "Submit txid", "primary"),
        ("dispute", "Raise dispute", "ghost"),
    ):
        if _action_allowed(deal, target):
            actions.append({"action": target, "label": label, "tone": tone})

    return render_template(
        "portal/deal_detail.html",
        page_title=deal.ref,
        page_kicker=f"{deal.asset} · {NETWORKS[deal.network]['label']}",
        deal=deal,
        actions=actions,
        role=role_for(deal, current_user),
        last_check=deal.checks[0] if deal.checks else None,
        check_history=deal.checks[:6],
    )


def _action_allowed(deal: Deal, action: str) -> bool:
    role = role_for(deal, current_user)
    if action == "send":
        return role == "owner" and deal.status == CREATED
    if action == "accept":
        return (
            deal.status in (CREATED, NEGOTIATION)
            and (role == "counterparty" or (role == "owner" and deal.counterparty_id is None))
        )
    if action == "submit_tx":
        return role in ("owner",) and deal.status in (AWAITING_DEPOSIT, CRYPTO_PENDING)
    if action == "dispute":
        return deal.status in OPEN_STATUSES and role in ("owner", "counterparty")
    return False


@bp.post("/deals/<int:deal_id>/action")
@login_required
def deal_action(deal_id: int):
    deal = _own_deal(deal_id)
    action = request.form.get("action", "")

    if action == "send":
        if not _action_allowed(deal, "send"):
            abort(403)
        transition(deal, NEGOTIATION, current_user, note="Terms sent to the counterparty.")

    elif action == "accept":
        if not _action_allowed(deal, "accept"):
            abort(403)
        transition(deal, AGREED, current_user, note="Terms accepted.")

    elif action == "submit_tx":
        if not _action_allowed(deal, "submit_tx"):
            abort(403)
        txid = (request.form.get("txid") or "").strip()
        result = run_check(deal, txid, actor=current_user)
        flash(
            f"Verification: {result.status} — {result.detail}",
            "success" if result.ok else "warn",
        )
        return redirect(url_for("portal.deal_detail", deal_id=deal.id))

    elif action == "dispute":
        reason = (request.form.get("reason") or "").strip()
        if len(reason) < 8:
            flash("Describe the issue in a few words (8+ characters).", "danger")
            return redirect(url_for("portal.deal_detail", deal_id=deal.id))
        deal.dispute_reason = reason[:2000]
        transition(deal, DISPUTED, current_user, note=f"Dispute opened: {reason[:200]}")
        flash("Dispute opened. The desk has been notified.", "warn")

    else:
        abort(400, description="Unknown action.")

    return redirect(url_for("portal.deal_detail", deal_id=deal.id))


@bp.post("/deals/<int:deal_id>/messages")
@login_required
def deal_message(deal_id: int):
    deal = _own_deal(deal_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Empty message.", "warn")
        return redirect(url_for("portal.deal_detail", deal_id=deal.id))

    db.session.add(Message(deal_id=deal.id, author_id=current_user.id, body=body[:4000]))
    audit("deal.message", "deal", deal.id, ref=deal.ref)
    db.session.commit()
    return redirect(url_for("portal.deal_detail", deal_id=deal.id) + "#thread")


@bp.get("/deals/<int:deal_id>/receipt")
@login_required
def deal_receipt(deal_id: int):
    deal = _own_deal(deal_id)
    if deal.status not in (FIAT_SENT, SETTLED, CRYPTO_CONFIRMED):
        flash("Receipts are issued once the crypto leg is confirmed.", "warn")
        return redirect(url_for("portal.deal_detail", deal_id=deal.id))
    return render_template(
        "portal/receipt.html",
        deal=deal,
        printable=True,
        page_title="Settlement receipt",
        page_kicker=f"Receipt · {deal.ref}",
    )


@bp.route("/wallets", methods=["GET", "POST"])
@login_required
def wallets():
    if request.method == "POST":
        network = request.form.get("network", "")
        address = (request.form.get("address") or "").strip()
        label = (request.form.get("label") or "").strip()[:80] or None
        if network not in NETWORKS:
            abort(400, description="Unknown network.")
        if len(address) < 20:
            abort(400, description="That address looks too short.")
        if WalletAddress.query.filter_by(user_id=current_user.id, network=network, address=address).first():
            flash("This address is already registered.", "warn")
            return redirect(url_for("portal.wallets"))

        wallet = WalletAddress(
            user_id=current_user.id, network=network, address=address, label=label, whitelisted=False
        )
        db.session.add(wallet)
        audit("wallet.request", "wallet", address, network=network)
        db.session.commit()
        flash("Address submitted — pending desk whitelisting.", "success")
        return redirect(url_for("portal.wallets"))

    return render_template(
        "portal/wallets.html",
        page_title="Wallets",
        page_kicker="Whitelisted settlement addresses",
        wallets=WalletAddress.query.filter_by(user_id=current_user.id).order_by(WalletAddress.created_at.desc()).all(),
    )


@bp.post("/wallets/<int:wallet_id>/delete")
@login_required
def wallet_delete(wallet_id: int):
    wallet = db.session.get(WalletAddress, wallet_id)
    if not wallet or wallet.user_id != current_user.id:
        abort(404)
    if wallet.whitelisted:
        flash("A whitelisted address must be revoked by the desk first.", "warn")
        return redirect(url_for("portal.wallets"))
    db.session.delete(wallet)
    audit("wallet.delete", "wallet", wallet.address)
    db.session.commit()
    flash("Address removed.", "info")
    return redirect(url_for("portal.wallets"))


@bp.get("/statements")
@login_required
def statements():
    deals = (
        Deal.query.filter(
            (Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id)
        )
        .filter(Deal.status.in_((SETTLED, FIAT_SENT)))
        .order_by(Deal.created_at.desc())
        .all()
    )
    by_asset: dict[str, dict] = {}
    for deal in deals:
        bucket = by_asset.setdefault(deal.asset, {"count": 0, "crypto": Decimal(0), "ngn": Decimal(0)})
        bucket["count"] += 1
        bucket["crypto"] += Decimal(deal.amount_crypto)
        bucket["ngn"] += Decimal(deal.fiat_amount)

    return render_template(
        "portal/statements.html",
        page_title="Statements",
        page_kicker="Settled activity",
        deals=deals,
        by_asset=by_asset,
        total_ngn=sum((b["ngn"] for b in by_asset.values()), Decimal(0)),
    )


@bp.get("/statements.csv")
@login_required
def statements_csv():
    deals = (
        Deal.query.filter(
            (Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id)
        )
        .order_by(Deal.created_at.desc())
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["ref", "created_utc", "asset", "network", "amount_crypto", "rate_ngn", "fiat_ngn",
         "fee_ngn", "status", "txid", "settled_utc"]
    )
    for d in deals:
        writer.writerow(
            [d.ref, d.created_at.isoformat(timespec="seconds"), d.asset, d.network,
             d.amount_crypto, d.rate_ngn, d.fiat_amount, d.fee_ngn, d.status, d.txid or "",
             d.settled_at.isoformat(timespec="seconds") if d.settled_at else ""]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=otc-statement-{current_user.id}.csv"},
    )


@bp.get("/receipts/<path:filename>")
@login_required
def receipt_file(filename: str):
    deal = Deal.query.filter(
        (Deal.initiator_id == current_user.id) | (Deal.counterparty_id == current_user.id),
        Deal.receipt_file == filename,
    ).first()
    if not (current_user.is_admin or deal):
        abort(404)
    from flask import current_app, send_from_directory

    return send_from_directory(current_app.config["UPLOAD_DIR"], filename)
