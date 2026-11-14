"""Runtime configuration (POC).

Everything is env-driven so the same image can run in demo or with real
blockchain providers. Defaults are safe for a throw-away POC deployment.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("OTC_DATA_DIR", BASE_DIR / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- core -------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'otc.sqlite3'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- session / cookies ------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # 4 MB receipt uploads

    # --- product rules ----------------------------------------------------
    FEE_BPS = int(os.environ.get("FEE_BPS", "60"))  # 0.60 % desk fee
    BRAND = os.environ.get("OTC_BRAND", "OTC Desk")
    ENV_NAME = os.environ.get("OTC_ENV_NAME", "POC")

    # --- blockchain verification -----------------------------------------
    # CHAIN_DEMO=true keeps the verifier fully deterministic and offline.
    # Set to false and provide API keys to hit real providers.
    CHAIN_DEMO = _bool("CHAIN_DEMO", True)
    HTTP_TIMEOUT = float(os.environ.get("CHAIN_HTTP_TIMEOUT", "8"))
    BLOCKSTREAM_URL = os.environ.get("BLOCKSTREAM_URL", "https://blockstream.info/api")
    ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
    TRON_API_KEY = os.environ.get("TRON_API_KEY", "")

    # --- uploads ----------------------------------------------------------
    UPLOAD_DIR = str(UPLOAD_DIR)
    ALLOWED_RECEIPT_EXT = {"png", "jpg", "jpeg", "webp", "pdf"}
