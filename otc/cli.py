"""CLI: `flask seed` builds a believable demo book."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import click
from flask import Flask
from flask.cli import with_appcontext

from .extensions import db
from .models import (
    AGREED,
    AWAITING_DEPOSIT,
    CRYPTO_CONFIRMED,
    CRYPTO_PENDING,
    DISPUTED,
    FIAT_SENT,
    NEGOTIATION,
    SETTLED,
    AuditLog,
    ChainCheck,
    Deal,
    Message,
    PlatformWallet,
    Rate,
    User,
    WalletAddress,
)


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_command)
    app.cli.add_command(reset_command)


@click.command("seed")
@click.option("--reset", is_flag=True, help="Drop every table before seeding.")
@with_appcontext
def seed_command(reset: bool) -> None:
    """Create tables and load demo users, wallets, rates and deals."""
    if reset:
        db.drop_all()
    db.create_all()

    if User.query.count():
        click.echo("Database already seeded — use --reset to rebuild.")
        return

    now = datetime.utcnow()

    # --- people ----------------------------------------------------------
    admin = User(
        email="admin@otcdesk.test", full_name="Desk Operator", company="OTC Desk",
        role="admin", kyc_status="verified", twofa_enabled=False,
        monthly_limit_ngn=Decimal("0"),
    )
    admin.set_password("Admin#2026")

    ops = User(
        email="ops@otcdesk.test", full_name="Settlement Ops", company="OTC Desk",
        role="admin", kyc_status="verified", monthly_limit_ngn=Decimal("0"),
    )
    ops.set_password("Ops#2026")

    client_a = User(
        email="trading@northwind.test", full_name="Adaeze Nwosu", company="Northwind Trading",
        phone="+234 801 000 0001", role="client", kyc_status="verified",
        monthly_limit_ngn=Decimal("900000000"),
    )
    client_a.set_password("Client#2026")

    client_b = User(
        email="treasury@helios.test", full_name="Ibrahim Sule", company="Helios Capital",
        phone="+234 802 000 0002", role="client", kyc_status="verified",
        monthly_limit_ngn=Decimal("450000000"),
    )
    client_b.set_password("Client#2026")

    client_pending = User(
        email="desk@acme.test", full_name="Fatima Bello", company="Acme Markets",
        phone="+234 803 000 0003", role="client", kyc_status="pending",
        monthly_limit_ngn=Decimal("120000000"),
    )
    client_pending.set_password("Client#2026")

    db.session.add_all([admin, ops, client_a, client_b, client_pending])
    db.session.flush()

    # --- rates -----------------------------------------------------------
    db.session.add_all([
        Rate(asset="BTC", ngn_rate=Decimal("152450000.00")),
        Rate(asset="USDT", ngn_rate=Decimal("1542.00")),
    ])

    # --- desk settlement wallets ----------------------------------------
    db.session.add_all([
        PlatformWallet(network="BTC", address="bc1qdesk9r4m2x7v8p3n6t5k0j2h9w4s8c7d6e5f3g", label="Desk treasury · BTC"),
        PlatformWallet(network="USDT-TRC20", address="TDesk9Rm2X7V8P3N6T5K0J2H9W4S8C7D6E", label="Desk treasury · TRC-20"),
        PlatformWallet(network="USDT-ERC20", address="0x9d3f8a2c7b1e4f605a8c3d2e1b0a9f8e7d6c5b4a", label="Desk treasury · ERC-20"),
        PlatformWallet(network="USDT-BEP20", address="0x4c7b1e9f2a6d8035b4c7e1f9a2d6b8c0e3f5a7d9", label="Desk treasury · BEP-20"),
    ])

    # --- client wallets --------------------------------------------------
    db.session.add_all([
        WalletAddress(user_id=client_a.id, network="BTC", address="bc1q7x9m2v4p6n8r0t2w4y6z8a0c2e4g6j8k0l2m4", label="Treasury cold", whitelisted=True),
        WalletAddress(user_id=client_a.id, network="USDT-TRC20", address="TQ7xm2v4p6n8r2t4w6y8z0a2c4e6g8", label="Ops hot wallet", whitelisted=True),
        WalletAddress(user_id=client_b.id, network="USDT-ERC20", address="0x1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d", label="Settlement wallet", whitelisted=True),
        WalletAddress(user_id=client_b.id, network="USDT-BEP20", address="0x9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c", label="Alt rail", whitelisted=False),
    ])
    db.session.flush()

    counter = {"n": 1041}

    def ref() -> str:
        counter["n"] += 1
        return f"OTC-2026-{counter['n']}"

    def make_deal(**kw) -> Deal:
        deal = Deal(ref=ref(), **kw)
        db.session.add(deal)
        db.session.flush()
        return deal

    # 1. settled deal with full history
    d1 = make_deal(
        initiator_id=client_a.id, counterparty_id=client_b.id, network="USDT-TRC20",
        amount_crypto=Decimal("25000"), rate_ngn=Decimal("1542"),
        fiat_amount=Decimal("38550000.00"), fee_ngn=Decimal("231300.00"),
        status=SETTLED, fiat_method="BANK_TRANSFER",
        deposit_address="TDesk9Rm2X7V8P3N6T5K0J2H9W4S8C7D6E",
        payout_bank="GTBank · 0123456789 · Northwind Trading Ltd",
        txid="8f4c2d1e9b7a6354d2f8e91c0b7a6d5e4f3c2b1a09f8e7d6c5b4a39281706f5e",
        confirmations_required=19, confirmations_seen=26,
        fiat_reference="GTB/NIP/8841209",
        created_at=now - timedelta(days=9), updated_at=now - timedelta(days=8),
        settled_at=now - timedelta(days=8),
        notes="Block sale, 25k USDT, NIP out same day.",
    )
    db.session.add(ChainCheck(
        deal_id=d1.id, txid=d1.txid, network=d1.network, status="confirmed",
        confirmations=26, required_conf=19, amount=Decimal("25000"),
        from_address="TQ7xm2v4p6n8r2t4w6y8z0a2c4e6g8", to_address=d1.deposit_address,
        provider="demo-simulator", simulated=True, detail="Simulated result (26/19 confirmations).",
        checked_at=now - timedelta(days=9, hours=-1),
    ))

    # 2. crypto confirmed — waiting for the desk to release NGN
    d2 = make_deal(
        initiator_id=client_b.id, counterparty_id=client_a.id, network="BTC",
        amount_crypto=Decimal("0.75"), rate_ngn=Decimal("152450000"),
        fiat_amount=Decimal("114337500.00"), fee_ngn=Decimal("686025.00"),
        status=CRYPTO_CONFIRMED, fiat_method="RTGS",
        deposit_address="bc1qdesk9r4m2x7v8p3n6t5k0j2h9w4s8c7d6e5f3g",
        payout_bank="Zenith Bank · 1098765432 · Helios Capital",
        txid="3a7b9c1d5e2f8046a8c0e2f4b6d8a0c2e4f6a8b0d2c4e6f8a0b2c4d6e8f0a2b4",
        confirmations_required=2, confirmations_seen=5,
        created_at=now - timedelta(days=1, hours=5), updated_at=now - timedelta(hours=3),
        notes="0.75 BTC, RTGS before 15:00 UTC.",
    )
    db.session.add(ChainCheck(
        deal_id=d2.id, txid=d2.txid, network=d2.network, status="confirmed",
        confirmations=5, required_conf=2, amount=Decimal("0.75"),
        from_address="bc1qsrc7k2m4n6p8q0r2s4t6v8w0x2y4z6", to_address=d2.deposit_address,
        provider="demo-simulator", simulated=True, detail="Simulated result (5/2 confirmations).",
        checked_at=now - timedelta(hours=3),
    ))

    # 3. awaiting crypto deposit
    d3 = make_deal(
        initiator_id=client_a.id, network="USDT-ERC20",
        amount_crypto=Decimal("40000"), rate_ngn=Decimal("1542"),
        fiat_amount=Decimal("61680000.00"), fee_ngn=Decimal("370080.00"),
        status=AWAITING_DEPOSIT, fiat_method="NIP",
        deposit_address="0x9d3f8a2c7b1e4f605a8c3d2e1b0a9f8e7d6c5b4a",
        payout_bank="Access Bank · 0099887766 · Northwind Trading Ltd",
        confirmations_required=12,
        created_at=now - timedelta(hours=20), updated_at=now - timedelta(hours=2),
        notes="40k USDT ERC-20, desk principal on the other side.",
    )

    # 4. negotiating
    d4 = make_deal(
        initiator_id=client_b.id, counterparty_id=client_a.id, network="USDT-BEP20",
        amount_crypto=Decimal("12500"), rate_ngn=Decimal("1548.5"),
        fiat_amount=Decimal("19356250.00"), fee_ngn=Decimal("116137.50"),
        status=NEGOTIATION, fiat_method="BANK_TRANSFER",
        confirmations_required=15,
        created_at=now - timedelta(hours=9), updated_at=now - timedelta(hours=6),
        notes="Asking for 1549.5 — will split the difference at 1548.5?",
    )

    # 5. dispute
    d5 = make_deal(
        initiator_id=client_a.id, counterparty_id=client_b.id, network="USDT-TRC20",
        amount_crypto=Decimal("6000"), rate_ngn=Decimal("1542"),
        fiat_amount=Decimal("9252000.00"), fee_ngn=Decimal("55512.00"),
        status=DISPUTED, fiat_method="NIP",
        deposit_address="TDesk9Rm2X7V8P3N6T5K0J2H9W4S8C7D6E",
        payout_bank="First Bank · 2030405060 · Northwind Trading Ltd",
        txid="b1d3f5a7c9e1046b2d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f2a4c6e8",
        confirmations_required=19, confirmations_seen=19,
        dispute_reason="Sender used a different address than the one whitelisted for the deal.",
        created_at=now - timedelta(days=3), updated_at=now - timedelta(days=2),
    )
    db.session.add(ChainCheck(
        deal_id=d5.id, txid=d5.txid, network=d5.network, status="mismatch",
        confirmations=19, required_conf=19, amount=Decimal("6000"),
        from_address="TUnknownSender4n6p8q0r2s4t6v8w", to_address="TDifferent7k2m4n6p8q0r2s4t6",
        provider="demo-simulator", simulated=True,
        detail="Simulated: destination address does not match the deal deposit address.",
        checked_at=now - timedelta(days=2),
    ))

    # 6. fiat sent, awaiting close
    d6 = make_deal(
        initiator_id=client_b.id, network="USDT-TRC20",
        amount_crypto=Decimal("8000"), rate_ngn=Decimal("1540"),
        fiat_amount=Decimal("12320000.00"), fee_ngn=Decimal("73920.00"),
        status=FIAT_SENT, fiat_method="BANK_TRANSFER",
        deposit_address="TDesk9Rm2X7V8P3N6T5K0J2H9W4S8C7D6E",
        payout_bank="UBA · 5566778899 · Helios Capital",
        txid="c2e4a6f8b0d2146a8c0e2b4d6f8a0c2e4b6d8f0a2c4e6b8d0f2a4c6e8b0d2146",
        confirmations_required=19, confirmations_seen=21,
        fiat_reference="UBA/RTGS/551204",
        created_at=now - timedelta(days=2), updated_at=now - timedelta(hours=11),
    )
    db.session.add(ChainCheck(
        deal_id=d6.id, txid=d6.txid, network=d6.network, status="confirmed",
        confirmations=21, required_conf=19, amount=Decimal("8000"),
        to_address=d6.deposit_address, provider="demo-simulator", simulated=True,
        detail="Simulated result (21/19 confirmations).",
        checked_at=now - timedelta(days=2, hours=-2),
    ))

    # --- conversation threads -------------------------------------------
    db.session.add_all([
        Message(deal_id=d1.id, author_id=client_a.id, body="Deal opened: 25,000 USDT @ 1,542 NGN → 38,550,000 NGN.", created_at=now - timedelta(days=9)),
        Message(deal_id=d1.id, author_id=client_b.id, body="Accepted. Sending from our TRC-20 ops wallet now.", created_at=now - timedelta(days=9, hours=-2)),
        Message(deal_id=d1.id, author_id=client_b.id, body="Txid sent, please confirm at 19 blocks.", created_at=now - timedelta(days=9, hours=-3)),
        Message(deal_id=d1.id, author_id=admin.id, body="26/19 confirmations. Releasing NGN — ref GTB/NIP/8841209.", created_at=now - timedelta(days=8)),
        Message(deal_id=d2.id, author_id=client_b.id, body="0.75 BTC sent. RTGS window closes at 15:00 UTC.", created_at=now - timedelta(hours=4)),
        Message(deal_id=d4.id, author_id=client_b.id, body="Can you do 1,549.5 on 12.5k?", created_at=now - timedelta(hours=9)),
        Message(deal_id=d4.id, author_id=client_a.id, body="1548.5 and I cover the NIP fee.", created_at=now - timedelta(hours=7)),
        Message(deal_id=d5.id, author_id=client_a.id, body="The sender wallet differs from the whitelisted one — holding the fiat leg.", created_at=now - timedelta(days=2)),
    ])

    # --- audit trail -----------------------------------------------------
    for actor, action, entity, eid, days in (
        (admin, "auth.login", "user", admin.id, 9),
        (client_a, "deal.create", "deal", d1.id, 9),
        (client_b, "deal.transition", "deal", d1.id, 9),
        (admin, "chain.check", "deal", d1.id, 9),
        (admin, "deal.transition", "deal", d1.id, 8),
        (ops, "auth.login", "user", ops.id, 3),
        (admin, "client.kyc", "user", client_pending.id, 2),
    ):
        db.session.add(AuditLog(
            actor_id=actor.id, actor_email=actor.email, action=action, entity=entity,
            entity_id=str(eid), ip="127.0.0.1",
            created_at=now - timedelta(days=days),
        ))

    db.session.commit()

    click.echo("Seeded:")
    click.echo("  admin   admin@otcdesk.test        / Admin#2026")
    click.echo("  admin   ops@otcdesk.test          / Ops#2026")
    click.echo("  client  trading@northwind.test / Client#2026 (verified)")
    click.echo("  client  treasury@helios.test    / Client#2026 (verified)")
    click.echo("  client  desk@acme.test   / Client#2026 (KYC pending)")
    click.echo("  6 deals across the lifecycle, 4 desk wallets, 2 rates.")


@click.command("reset-db")
@click.option("--yes", is_flag=True, help="Confirm the destructive reset.")
@with_appcontext
def reset_command(yes: bool) -> None:
    """Drop everything (dev only)."""
    if not yes:
        raise click.ClickException("Pass --yes to confirm.")
    db.drop_all()
    db.create_all()
    click.echo("Database reset.")
