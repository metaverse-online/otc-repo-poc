"""Small display helpers shared by templates and API payloads."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation


def fmt_ngn(value, compact: bool = False) -> str:
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        amount = Decimal(0)
    if compact and abs(amount) >= 1_000_000:
        for divisor, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
            if abs(amount) >= divisor:
                return f"₦{amount / divisor:,.1f}{suffix}"
    return f"₦{amount:,.2f}"


def fmt_crypto(value, asset: str = "") -> str:
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        amount = Decimal(0)
    decimals = 8 if asset.upper() == "BTC" else 2
    text = f"{amount:,.{decimals}f}"
    if decimals == 8 and "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {asset}".strip()


def fmt_dt(value: datetime | None, fmt: str = "%d %b %Y · %H:%M") -> str:
    if not value:
        return "—"
    return value.strftime(fmt)


def timeago(value: datetime | None) -> str:
    if not value:
        return "—"
    delta = datetime.utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}d ago"
    return value.strftime("%d %b %Y")
