"""Receipt storage for the fiat leg (POC: local disk)."""
from __future__ import annotations

import os
import secrets
from datetime import datetime

from flask import current_app

from ..config import Config


def save_receipt(file_storage) -> str | None:
    """Store an uploaded proof of payment. Returns the stored filename."""
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in Config.ALLOWED_RECEIPT_EXT:
        return None

    name = f"receipt-{datetime.utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(4)}.{ext}"
    path = os.path.join(current_app.config["UPLOAD_DIR"], name)
    file_storage.save(path)
    return name
