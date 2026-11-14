"""HTTP smoke test for the POC.

Run against a live instance:
    python tests/smoke_test.py [base_url] [sqlite_path]

It exercises login, RBAC, deal creation, on-chain verification, the fiat
release guard, and the receipt page. It mutates the demo database.
"""
from __future__ import annotations

import re
import sqlite3
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5055"
DB = sys.argv[2] if len(sys.argv) > 2 else "var/otc.sqlite3"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
FAILS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILS.append(label)


def token(html: str) -> str:
    match = TOKEN_RE.search(html)
    if not match:
        raise RuntimeError("no CSRF token in page")
    return match.group(1)


def login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    page = s.get(f"{BASE}/login", timeout=10)
    r = s.post(
        f"{BASE}/login",
        data={"email": email, "password": password, "csrf_token": token(page.text)},
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code == 302, f"login failed for {email}: {r.status_code}"
    return s


def deal_ids() -> dict[str, int]:
    con = sqlite3.connect(DB)
    rows = con.execute("select id, ref, status from deals").fetchall()
    con.close()
    return {ref: (i, st) for i, ref, st in rows}


def main() -> int:
    ids = deal_ids()
    settled_ref = next(r for r, (_, st) in ids.items() if st == "SETTLED")
    confirmed_ref = next((r for r, (_, st) in ids.items() if st == "CRYPTO_CONFIRMED"), None)
    awaiting_ref = next((r for r, (_, st) in ids.items() if st == "AWAITING_DEPOSIT"), None)

    # --- client portal ---------------------------------------------------
    client = login("trading@northwind.test", "Client#2026")
    for path in ("/portal/overview", "/portal/deals", "/portal/wallets", "/portal/statements"):
        r = client.get(BASE + path, timeout=10)
        check(f"GET {path}", r.status_code == 200, str(r.status_code))

    page = client.get(f"{BASE}/portal/deals/new", timeout=10)
    check("GET /portal/deals/new", page.status_code == 200, str(page.status_code))
    created = client.post(
        f"{BASE}/portal/deals/new",
        data={
            "csrf_token": token(page.text),
            "network": "USDT-TRC20",
            "amount_crypto": "1500",
            "rate_ngn": "1542",
            "counterparty_id": "",
            "fiat_method": "NIP",
            "payout_bank": "Test Bank · 0001112223 · Smoke Test Ltd",
            "notes": "smoke test deal",
        },
        allow_redirects=False,
        timeout=10,
    )
    check("POST create deal", created.status_code == 302, str(created.status_code))
    detail_html = client.get(BASE + created.headers.get("Location", "/portal/deals"), timeout=10).text
    check("deal page renders terms", "Smoke test deal" in detail_html or "smoke test deal" in detail_html)

    # RBAC: unrelated client must not read another counterparty's deal
    outsider = login("desk@acme.test", "Client#2026")
    other_id = ids[settled_ref][0]
    r = outsider.get(f"{BASE}/portal/deals/{other_id}", timeout=10)
    check("RBAC blocks unrelated deal", r.status_code == 403, str(r.status_code))
    r = outsider.get(f"{BASE}/api/deals/{other_id}/status", timeout=10)
    check("API blocks unrelated deal", r.status_code == 403, str(r.status_code))
    r = outsider.get(f"{BASE}/admin/", timeout=10)
    check("client blocked from admin", r.status_code == 403, str(r.status_code))

    # --- admin desk ------------------------------------------------------
    admin = login("admin@otcdesk.test", "Admin#2026")
    for path in ("/admin/", "/admin/queue", "/admin/verify", "/admin/clients", "/admin/audit"):
        r = admin.get(BASE + path, timeout=10)
        check(f"GET {path}", r.status_code == 200, str(r.status_code))

    # verification of the awaiting-deposit deal
    if awaiting_ref:
        deal_id = ids[awaiting_ref][0]
        page = admin.get(f"{BASE}/admin/verify", timeout=10)
        r = admin.post(
            f"{BASE}/admin/verify",
            data={
                "csrf_token": token(page.text),
                "deal_id": str(deal_id),
                "txid": "aa11bb22cc33dd44ee55ff6677889900aabbccddeeff00112233445566778899",
            },
            allow_redirects=False,
            timeout=15,
        )
        check("POST verification run", r.status_code == 302, str(r.status_code))
        _, status = deal_ids()[awaiting_ref]
        check("deal advanced after check", status in ("CRYPTO_CONFIRMED", "CRYPTO_PENDING"), status)

    # fiat release guard: refused on a deal that is not CRYPTO_CONFIRMED
    admin_page = admin.get(f"{BASE}/admin/queue", timeout=10)
    r = admin.post(
        f"{BASE}/admin/deals/{ids[settled_ref][0]}/action",
        data={"action": "release", "fiat_reference": "TEST/1", "csrf_token": token(admin_page.text)},
        allow_redirects=False,
        timeout=10,
    )
    check("release blocked on settled deal", r.status_code in (403, 409), str(r.status_code))

    if confirmed_ref:
        deal_id, _ = ids[confirmed_ref]
        page = admin.get(f"{BASE}/portal/deals/{deal_id}", timeout=10)
        check("admin reads deal page", page.status_code == 200, str(page.status_code))
        r = admin.post(
            f"{BASE}/admin/deals/{deal_id}/action",
            data={
                "action": "release",
                "fiat_reference": "SMOKE/NIP/0001",
                "next": f"/portal/deals/{deal_id}",
                "csrf_token": token(page.text),
            },
            allow_redirects=False,
            timeout=10,
        )
        check("POST release fiat", r.status_code == 302, str(r.status_code))
        _, status = deal_ids()[confirmed_ref]
        check("deal is FIAT_SENT", status == "FIAT_SENT", status)

        r = admin.get(f"{BASE}/portal/deals/{deal_id}/receipt", timeout=10)
        check("GET receipt", r.status_code == 200, str(r.status_code))

        page = admin.get(f"{BASE}/portal/deals/{deal_id}", timeout=10)
        r = admin.post(
            f"{BASE}/admin/deals/{deal_id}/action",
            data={"action": "settle", "next": f"/portal/deals/{deal_id}", "csrf_token": token(page.text)},
            allow_redirects=False,
            timeout=10,
        )
        check("POST settle", r.status_code == 302, str(r.status_code))
        _, status = deal_ids()[confirmed_ref]
        check("deal is SETTLED", status == "SETTLED", status)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s): {', '.join(FAILS)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
