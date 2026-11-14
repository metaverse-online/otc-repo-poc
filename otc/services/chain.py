"""On-chain verification engine (POC).

Design:
  * one small provider per network family,
  * a single normalised ``ChainCheck`` result object,
  * an explicit ``DEMO`` provider so the whole flow is testable offline.

The demo provider is deterministic: the same txid always yields the same
confirmations, and the result is always flagged ``simulated=True`` so the UI
can label it. Real providers are used only when ``CHAIN_DEMO=false`` and the
required API key / endpoint is configured.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import requests

from ..config import Config
from ..models import NETWORKS

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

STATUS_CONFIRMED = "confirmed"
STATUS_PENDING = "pending"
STATUS_NOT_FOUND = "not_found"
STATUS_MISMATCH = "mismatch"
STATUS_ERROR = "error"


@dataclass
class ChainResult:
    status: str
    confirmations: int = 0
    required: int = 0
    amount: Decimal | None = None
    from_address: str | None = None
    to_address: str | None = None
    provider: str = "unknown"
    simulated: bool = False
    detail: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_CONFIRMED

    def as_json(self) -> str:
        payload = {
            "status": self.status,
            "confirmations": self.confirmations,
            "required": self.required,
            "amount": str(self.amount) if self.amount is not None else None,
            "from": self.from_address,
            "to": self.to_address,
            "provider": self.provider,
            "simulated": self.simulated,
            "detail": self.detail,
            "raw": self.raw,
        }
        return json.dumps(payload, indent=2)[:4000]


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
class DemoProvider:
    """Deterministic fake chain. NEVER used when CHAIN_DEMO=false."""

    name = "demo-simulator"

    def check(self, network: str, txid: str, expected: Decimal | None, to_address: str | None, required: int) -> ChainResult:
        digest = hashlib.sha256(f"{network}:{txid}".encode()).hexdigest()
        n = int(digest[:8], 16)

        confirmations = 1 + n % (required + 3)
        amount = expected or Decimal("0")
        from_address = _demo_address(network, "src", digest)

        if txid.lower().startswith("0000"):
            return ChainResult(
                status=STATUS_NOT_FOUND,
                required=required,
                provider=self.name,
                simulated=True,
                detail="Simulated: transaction not indexed by the provider.",
                raw={"txid": txid, "error": "not_found"},
            )

        if to_address and n % 11 == 0:
            return ChainResult(
                status=STATUS_MISMATCH,
                confirmations=confirmations,
                required=required,
                amount=amount,
                from_address=from_address,
                to_address=_demo_address(network, "dst", digest),
                provider=self.name,
                simulated=True,
                detail="Simulated: destination address does not match the deal deposit address.",
                raw={"txid": txid},
            )

        status = STATUS_CONFIRMED if confirmations >= required else STATUS_PENDING
        return ChainResult(
            status=status,
            confirmations=confirmations,
            required=required,
            amount=amount,
            from_address=from_address,
            to_address=to_address or _demo_address(network, "dst", digest),
            provider=self.name,
            simulated=True,
            detail=f"Simulated result ({confirmations}/{required} confirmations).",
            raw={"txid": txid, "simulated": True, "digest": digest[:16]},
        )


class BitcoinProvider:
    """blockstream.info public API — no key required, read-only."""

    name = "blockstream"

    def check(self, network: str, txid: str, expected: Decimal | None, to_address: str | None, required: int) -> ChainResult:
        base = Config.BLOCKSTREAM_URL.rstrip("/")
        try:
            tx = requests.get(f"{base}/tx/{txid}", timeout=Config.HTTP_TIMEOUT)
            if tx.status_code == 404:
                return ChainResult(status=STATUS_NOT_FOUND, required=required, provider=self.name,
                                   detail="Transaction not found on Bitcoin mainnet.")
            tx.raise_for_status()
            body = tx.json()

            tip = requests.get(f"{base}/blocks/tip/height", timeout=Config.HTTP_TIMEOUT)
            tip.raise_for_status()
            tip_height = int(tip.text.strip())

            confirmed = bool(body.get("status", {}).get("confirmed"))
            block_height = body.get("status", {}).get("block_height") or 0
            confirmations = (tip_height - block_height + 1) if confirmed else 0

            value_sats = 0
            matched_to = None
            for out in body.get("vout", []):
                addr = out.get("scriptpubkey_address")
                if to_address and addr and addr == to_address:
                    value_sats += int(out.get("value", 0))
                    matched_to = addr

            from_address = None
            for vin in body.get("vin", []):
                prev = vin.get("prevout") or {}
                if prev.get("scriptpubkey_address"):
                    from_address = prev["scriptpubkey_address"]
                    break

            amount = Decimal(value_sats) / Decimal(10**8) if value_sats else None

            if to_address and matched_to is None:
                status = STATUS_MISMATCH
                detail = "No output paying the deal deposit address."
            elif expected is not None and amount is not None and amount < expected * Decimal("0.995"):
                status = STATUS_MISMATCH
                detail = f"Underpayment: {amount} BTC < {expected} BTC."
            elif confirmations < required:
                status = STATUS_PENDING
                detail = f"{confirmations}/{required} confirmations."
            else:
                status = STATUS_CONFIRMED
                detail = f"{confirmations}/{required} confirmations."

            return ChainResult(
                status=status, confirmations=confirmations, required=required, amount=amount,
                from_address=from_address, to_address=matched_to or to_address,
                provider=self.name, detail=detail,
                raw={"txid": txid, "block_height": block_height, "tip": tip_height},
            )
        except Exception as exc:  # noqa: BLE001 - POC: surface, never crash a deal page
            return ChainResult(status=STATUS_ERROR, required=required, provider=self.name,
                               detail=f"Provider error: {exc.__class__.__name__}: {exc}")


class EvmProvider:
    """USDT ERC-20 / BEP-20 via Etherscan v2 (requires ETHERSCAN_API_KEY)."""

    name = "etherscan-v2"
    CHAINS = {"USDT-ERC20": 1, "USDT-BEP20": 56}

    def check(self, network: str, txid: str, expected: Decimal | None, to_address: str | None, required: int) -> ChainResult:
        chain_id = self.CHAINS[network]
        try:
            resp = requests.get(
                "https://api.etherscan.io/v2/api",
                params={
                    "chainid": chain_id,
                    "module": "proxy",
                    "action": "eth_getTransactionReceipt",
                    "txhash": txid,
                    "apikey": Config.ETHERSCAN_API_KEY,
                },
                timeout=Config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            receipt = payload.get("result")
            if not receipt:
                return ChainResult(status=STATUS_NOT_FOUND, required=required, provider=self.name,
                                   detail="Transaction not found.")

            block_number = int(receipt.get("blockNumber", "0x0"), 16)
            latest = requests.get(
                "https://api.etherscan.io/v2/api",
                params={"chainid": chain_id, "module": "proxy", "action": "eth_blockNumber",
                        "apikey": Config.ETHERSCAN_API_KEY},
                timeout=Config.HTTP_TIMEOUT,
            ).json()
            head = int(latest.get("result", "0x0"), 16)
            confirmations = max(head - block_number + 1, 0)

            amount = Decimal(0)
            matched_to = None
            for log in receipt.get("logs", []):
                topics = log.get("topics") or []
                if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
                    continue
                to_addr = "0x" + topics[2][-40:]
                if to_address and to_addr.lower() != to_address.lower():
                    continue
                matched_to = to_addr
                amount += Decimal(int(log.get("data", "0x0"), 16)) / Decimal(10**6)

            if to_address and matched_to is None:
                status, detail = STATUS_MISMATCH, "No USDT transfer to the deal deposit address."
            elif expected is not None and amount < expected * Decimal("0.995"):
                status, detail = STATUS_MISMATCH, f"Underpayment: {amount} USDT < {expected} USDT."
            elif confirmations < required:
                status, detail = STATUS_PENDING, f"{confirmations}/{required} confirmations."
            else:
                status, detail = STATUS_CONFIRMED, f"{confirmations}/{required} confirmations."

            return ChainResult(
                status=status, confirmations=confirmations, required=required, amount=amount,
                to_address=matched_to or to_address, provider=self.name, detail=detail,
                raw={"txid": txid, "block": block_number, "head": head},
            )
        except Exception as exc:  # noqa: BLE001
            return ChainResult(status=STATUS_ERROR, required=required, provider=self.name,
                               detail=f"Provider error: {exc.__class__.__name__}: {exc}")


class TronProvider:
    """TRC-20 USDT via TronGrid (requires TRON_API_KEY)."""

    name = "trongrid"
    USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

    def check(self, network: str, txid: str, expected: Decimal | None, to_address: str | None, required: int) -> ChainResult:
        headers = {"TRON-PRO-API-KEY": Config.TRON_API_KEY}
        try:
            info = requests.post(
                "https://api.trongrid.io/wallet/gettransactioninfobyid",
                json={"value": txid}, headers=headers, timeout=Config.HTTP_TIMEOUT,
            )
            info.raise_for_status()
            body = info.json()
            if not body or "blockNumber" not in body:
                return ChainResult(status=STATUS_NOT_FOUND, required=required, provider=self.name,
                                   detail="Transaction not found.")

            now = requests.get("https://api.trongrid.io/wallet/getnowblock", headers=headers,
                               timeout=Config.HTTP_TIMEOUT).json()
            head = int(now.get("block_header", {}).get("raw_data", {}).get("number", 0))
            confirmations = max(head - int(body["blockNumber"]) + 1, 0)

            amount = Decimal(0)
            matched_to = None
            for log in body.get("log", []):
                if not log.get("topics") or len(log["topics"]) < 3:
                    continue
                if log["topics"][0].lower() != TRANSFER_TOPIC.lstrip("0x"):
                    continue
                to_hex = "41" + log["topics"][2][-40:]
                to_b58 = _tron_hex_to_base58(to_hex)
                if to_address and to_b58 != to_address:
                    continue
                matched_to = to_b58
                amount += Decimal(int(log.get("data", "0"), 16)) / Decimal(10**6)

            if to_address and matched_to is None:
                status, detail = STATUS_MISMATCH, "No USDT TRC-20 transfer to the deal deposit address."
            elif expected is not None and amount < expected * Decimal("0.995"):
                status, detail = STATUS_MISMATCH, f"Underpayment: {amount} USDT < {expected} USDT."
            elif confirmations < required:
                status, detail = STATUS_PENDING, f"{confirmations}/{required} confirmations."
            else:
                status, detail = STATUS_CONFIRMED, f"{confirmations}/{required} confirmations."

            return ChainResult(
                status=status, confirmations=confirmations, required=required, amount=amount,
                to_address=matched_to or to_address, provider=self.name, detail=detail,
                raw={"txid": txid, "block": body.get("blockNumber")},
            )
        except Exception as exc:  # noqa: BLE001
            return ChainResult(status=STATUS_ERROR, required=required, provider=self.name,
                               detail=f"Provider error: {exc.__class__.__name__}: {exc}")


# --------------------------------------------------------------------------
# public entrypoint
# --------------------------------------------------------------------------
def verify_transfer(network: str, txid: str, expected: Decimal | None, to_address: str | None,
                    required: int | None = None) -> ChainResult:
    meta = NETWORKS.get(network)
    if not meta:
        return ChainResult(status=STATUS_ERROR, detail=f"Unsupported network {network!r}.")

    txid = (txid or "").strip()
    required = int(required or meta["required_conf"])
    if len(txid) < 16:
        return ChainResult(status=STATUS_ERROR, required=required,
                           detail="Transaction hash looks too short (need a full txid).")

    if Config.CHAIN_DEMO:
        return DemoProvider().check(network, txid, expected, to_address, required)

    if network == "BTC":
        return BitcoinProvider().check(network, txid, expected, to_address, required)
    if network in ("USDT-ERC20", "USDT-BEP20"):
        if not Config.ETHERSCAN_API_KEY:
            return ChainResult(status=STATUS_ERROR, required=required, provider="etherscan-v2",
                               detail="ETHERSCAN_API_KEY is not configured.")
        return EvmProvider().check(network, txid, expected, to_address, required)
    if network == "USDT-TRC20":
        if not Config.TRON_API_KEY:
            return ChainResult(status=STATUS_ERROR, required=required, provider="trongrid",
                               detail="TRON_API_KEY is not configured.")
        return TronProvider().check(network, txid, expected, to_address, required)

    return ChainResult(status=STATUS_ERROR, required=required, detail="No provider for this network.")


def _demo_address(network: str, kind: str, digest: str) -> str:
    if network == "BTC":
        body = _base58(digest + kind, 38)
        return ("bc1q" + body.lower())[:42]
    if network == "USDT-TRC20":
        return "T" + _base58(digest + kind, 33)
    return "0x" + hashlib.sha256((digest + kind).encode()).hexdigest()[:40]


def _base58(seed: str, length: int) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    out = []
    digest = hashlib.sha512(seed.encode()).digest()
    while len(out) < length:
        out.extend(alphabet[b % 58] for b in digest)
        digest = hashlib.sha512(digest).digest()
    return "".join(out[:length])


def _tron_hex_to_base58(hex_addr: str) -> str:  # pragma: no cover - only used with a live TRON key
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num = int(hex_addr, 16)
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = alphabet[rem] + out
    return out
