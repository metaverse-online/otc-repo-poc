"""Domain model for the private OTC settlement desk (POC scope)."""
from __future__ import annotations

import secrets
from datetime import datetime
from decimal import Decimal

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLE_CLIENT = "client"
ROLE_ADMIN = "admin"

# --- deal lifecycle -------------------------------------------------------
CREATED = "CREATED"
NEGOTIATION = "NEGOTIATION"
AGREED = "AGREED"
AWAITING_DEPOSIT = "AWAITING_DEPOSIT"
CRYPTO_PENDING = "CRYPTO_PENDING"
CRYPTO_CONFIRMED = "CRYPTO_CONFIRMED"
FIAT_SENT = "FIAT_SENT"
SETTLED = "SETTLED"
DISPUTED = "DISPUTED"
REJECTED = "REJECTED"

STATUS_META: dict[str, dict[str, str]] = {
    CREATED: {"label": "Draft terms", "tone": "neutral", "step": 0},
    NEGOTIATION: {"label": "Negotiating", "tone": "info", "step": 1},
    AGREED: {"label": "Terms agreed", "tone": "info", "step": 2},
    AWAITING_DEPOSIT: {"label": "Awaiting crypto", "tone": "warn", "step": 3},
    CRYPTO_PENDING: {"label": "Crypto unconfirmed", "tone": "warn", "step": 4},
    CRYPTO_CONFIRMED: {"label": "Crypto confirmed", "tone": "success", "step": 5},
    FIAT_SENT: {"label": "Fiat sent", "tone": "success", "step": 6},
    SETTLED: {"label": "Settled", "tone": "settled", "step": 7},
    DISPUTED: {"label": "Disputed", "tone": "danger", "step": -1},
    REJECTED: {"label": "Rejected", "tone": "danger", "step": -1},
}

OPEN_STATUSES = (CREATED, NEGOTIATION, AGREED, AWAITING_DEPOSIT, CRYPTO_PENDING, CRYPTO_CONFIRMED, FIAT_SENT)
ACTIONABLE_BY_ADMIN = (AGREED, CRYPTO_CONFIRMED, DISPUTED)

NETWORKS: dict[str, dict] = {
    "BTC": {
        "label": "Bitcoin mainnet",
        "asset": "BTC",
        "short": "BTC",
        "required_conf": 2,
        "explorer": "https://blockstream.info/tx/",
        "address_hint": "bc1… / 1… / 3…",
    },
    "USDT-TRC20": {
        "label": "USDT · TRC-20",
        "asset": "USDT",
        "short": "TRC-20",
        "required_conf": 19,
        "explorer": "https://tronscan.org/#/transaction/",
        "address_hint": "T…",
    },
    "USDT-ERC20": {
        "label": "USDT · ERC-20",
        "asset": "USDT",
        "short": "ERC-20",
        "required_conf": 12,
        "explorer": "https://etherscan.io/tx/",
        "address_hint": "0x…",
    },
    "USDT-BEP20": {
        "label": "USDT · BEP-20",
        "asset": "USDT",
        "short": "BEP-20",
        "required_conf": 15,
        "explorer": "https://bscscan.com/tx/",
        "address_hint": "0x…",
    },
}

FIAT_METHODS = {
    "BANK_TRANSFER": "Bank transfer (NGN)",
    "NIP": "NIP instant transfer",
    "RTGS": "RTGS / same-day",
}

FIAT_METHOD_DEFAULT = "BANK_TRANSFER"


def network_asset(network: str) -> str:
    return NETWORKS.get(network, {}).get("asset", "BTC")


def new_ref() -> str:
    return f"OTC-{datetime.utcnow():%Y%m}-{secrets.token_hex(3).upper()}"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    company = db.Column(db.String(160))
    phone = db.Column(db.String(40))
    role = db.Column(db.String(20), nullable=False, default=ROLE_CLIENT)

    kyc_status = db.Column(db.String(20), nullable=False, default="pending")  # pending|verified|rejected
    risk_flag = db.Column(db.Boolean, nullable=False, default=False)
    monthly_limit_ngn = db.Column(db.Numeric(20, 2), nullable=False, default=Decimal("250000000"))
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)
    twofa_enabled = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)

    wallets = db.relationship("WalletAddress", back_populates="owner", cascade="all, delete-orphan")
    deals = db.relationship(
        "Deal", foreign_keys="Deal.initiator_id", back_populates="initiator", lazy="dynamic"
    )

    # flask-login needs `is_active`
    @property
    def is_active(self) -> bool:  # type: ignore[override]
        return bool(self.is_active_user)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email} ({self.role})>"


class WalletAddress(db.Model):
    __tablename__ = "wallet_addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    network = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(160), nullable=False)
    label = db.Column(db.String(80))
    whitelisted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    owner = db.relationship("User", back_populates="wallets")


class PlatformWallet(db.Model):
    """Desk settlement address per network (where counterparties deposit)."""

    __tablename__ = "platform_wallets"

    id = db.Column(db.Integer, primary_key=True)
    network = db.Column(db.String(20), unique=True, nullable=False)
    address = db.Column(db.String(160), nullable=False, default="")
    label = db.Column(db.String(80), default="Desk treasury")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Rate(db.Model):
    """Desk reference rate per asset. Editable from the admin overview."""

    __tablename__ = "rates"

    id = db.Column(db.Integer, primary_key=True)
    asset = db.Column(db.String(10), unique=True, nullable=False)
    ngn_rate = db.Column(db.Numeric(20, 2), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Deal(db.Model):
    __tablename__ = "deals"

    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(24), unique=True, nullable=False, default=new_ref, index=True)

    initiator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    counterparty_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)

    side = db.Column(db.String(12), nullable=False, default="SELL_CRYPTO")  # POC: crypto in, NGN out
    network = db.Column(db.String(20), nullable=False, default="USDT-TRC20")
    amount_crypto = db.Column(db.Numeric(28, 8), nullable=False)
    rate_ngn = db.Column(db.Numeric(20, 2), nullable=False)
    fiat_amount = db.Column(db.Numeric(20, 2), nullable=False)
    fee_ngn = db.Column(db.Numeric(20, 2), nullable=False, default=Decimal("0"))

    fiat_method = db.Column(db.String(20), nullable=False, default=FIAT_METHOD_DEFAULT)
    fiat_reference = db.Column(db.String(80))
    receipt_file = db.Column(db.String(255))

    status = db.Column(db.String(24), nullable=False, default=CREATED, index=True)
    deposit_address = db.Column(db.String(160))
    payout_address = db.Column(db.String(160))
    payout_bank = db.Column(db.String(160))

    txid = db.Column(db.String(120), index=True)
    confirmations_required = db.Column(db.Integer, nullable=False, default=2)
    confirmations_seen = db.Column(db.Integer, nullable=False, default=0)

    notes = db.Column(db.Text)
    dispute_reason = db.Column(db.Text)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    settled_at = db.Column(db.DateTime)

    initiator = db.relationship("User", foreign_keys=[initiator_id], back_populates="deals")
    counterparty = db.relationship("User", foreign_keys=[counterparty_id])
    messages = db.relationship(
        "Message", back_populates="deal", cascade="all, delete-orphan", order_by="Message.created_at"
    )
    checks = db.relationship(
        "ChainCheck", back_populates="deal", cascade="all, delete-orphan", order_by="ChainCheck.checked_at.desc()"
    )

    # -- helpers -----------------------------------------------------------
    @property
    def asset(self) -> str:
        return network_asset(self.network)

    @property
    def status_label(self) -> str:
        return STATUS_META.get(self.status, {}).get("label", self.status.title())

    @property
    def status_tone(self) -> str:
        return STATUS_META.get(self.status, {}).get("tone", "neutral")

    @property
    def explorer_url(self) -> str | None:
        if not self.txid:
            return None
        base = NETWORKS.get(self.network, {}).get("explorer")
        return f"{base}{self.txid}" if base else None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def participants(self) -> list[User]:
        people = [self.initiator]
        if self.counterparty:
            people.append(self.counterparty)
        return people

    def counterparty_label(self) -> str:
        if not self.counterparty:
            return "Desk principal"
        return self.counterparty.company or self.counterparty.full_name

    def can_view(self, user: User) -> bool:
        return bool(user.is_admin or user.id in {p.id for p in self.participants})


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    deal = db.relationship("Deal", back_populates="messages")
    author = db.relationship("User")


class ChainCheck(db.Model):
    """One on-chain verification attempt (append-only history)."""

    __tablename__ = "chain_checks"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False, index=True)
    txid = db.Column(db.String(120), nullable=False)
    network = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(24), nullable=False)  # confirmed|pending|not_found|mismatch|error
    confirmations = db.Column(db.Integer, nullable=False, default=0)
    required_conf = db.Column(db.Integer, nullable=False, default=0)
    amount = db.Column(db.Numeric(28, 8))
    from_address = db.Column(db.String(160))
    to_address = db.Column(db.String(160))
    provider = db.Column(db.String(40))
    simulated = db.Column(db.Boolean, nullable=False, default=False)
    detail = db.Column(db.Text)
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    deal = db.relationship("Deal", back_populates="checks")

    @property
    def ok(self) -> bool:
        return self.status == "confirmed"


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    actor_email = db.Column(db.String(255))
    action = db.Column(db.String(80), nullable=False, index=True)
    entity = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(40))
    meta = db.Column(db.Text)
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    actor = db.relationship("User")
