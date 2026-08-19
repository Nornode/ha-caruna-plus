#!/usr/bin/env python3
"""
Probe all Caruna+ API endpoints and dump raw JSON responses.

Authenticates, fetches assets, then calls energy / invoices / prices endpoints.
Each response is written to /tmp/caruna_probe_<name>.json for inspection.

Usage:
    export CARUNA_USERNAME="you@example.com"
    export CARUNA_PASSWORD='your-password'
    python3 scripts/probe_endpoints.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import aiohttp  # noqa: E402

from custom_components.caruna_plus.api import _decompress_invoices  # noqa: E402
from custom_components.caruna_plus.auth import CarunaAuthenticator  # noqa: E402
from custom_components.caruna_plus.const import BASE_URL, EP_ASSETS, EP_ENERGY  # noqa: E402

DUMP_DIR = Path("/tmp")


def _dump(name: str, data: object) -> Path:
    path = DUMP_DIR / f"caruna_probe_{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


async def _get_raw(session: aiohttp.ClientSession, url: str, token: str, params: dict | None = None):
    """Return (status, body_or_None). Never raises on non-JSON."""
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers, params=params) as resp:
        text = await resp.text()
        try:
            body = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            body = {"_raw_text": text[:300]}
        return resp.status, body


def _list_rows(body: object) -> list:
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("values", "data", "invoices", "items", "results"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def _show_sample(rows: list, label: str = "first row") -> None:
    if not rows:
        return
    first = rows[0]
    if isinstance(first, dict):
        print(f"  Keys in {label}: {list(first.keys())}")
        print(f"  {label}: {json.dumps(first, ensure_ascii=False)}")
    else:
        print(f"  {label}: {first!r}")


async def probe(session: aiohttp.ClientSession, username: str, password: str) -> int:
    auth = CarunaAuthenticator(session, username, password)

    print("Logging in...")
    token = await auth.async_login()
    customers = auth.token_store.customer_numbers
    print(f"  Token: {token[:6]}... ({len(token)} chars)")
    print(f"  Customer numbers: {customers}")

    if not customers:
        print("ERROR: no customer numbers in token")
        return 1

    customer = str(customers[0])
    today = date.today()
    yesterday = today - timedelta(days=1)

    # ---- assets ----
    print("\n[1] GET assets")
    status, body = await _get_raw(session, EP_ASSETS.format(customer=customer), token)
    items = body if isinstance(body, list) else (body or {}).get("assets", [])
    path = _dump("assets", body)
    print(f"  HTTP {status} — {len(items)} item(s) — {path}")
    if not items:
        print("  No assets; aborting.")
        return 1

    first = items[0]
    mp = str(first.get("assetId") or first.get("meteringPointId") or first.get("id"))
    contract_id = first.get("contractId") or first.get("id")
    print(f"  metering_point={mp}  contractId={contract_id}")
    print(f"  Asset keys: {list(first.keys())}")

    # ---- energy: daily (confirmed working) ----
    energy_url = EP_ENERGY.format(customer=customer, mp=mp)
    params = {
        "year": str(yesterday.year), "month": str(yesterday.month),
        "day": str(yesterday.day), "timespan": "daily",
    }
    print(f"\n[2] GET energy daily (yesterday={yesterday})")
    status, body = await _get_raw(session, energy_url, token, params=params)
    rows = _list_rows(body)
    _dump("energy_daily", body)
    print(f"  HTTP {status} — {len(rows)} row(s)")
    _show_sample(rows)

    # ---- energy: try timespan=hourly with yesterday ----
    params_h = {**params, "timespan": "hourly"}
    print(f"\n[3] GET energy hourly (yesterday={yesterday})")
    status, body = await _get_raw(session, energy_url, token, params=params_h)
    rows = _list_rows(body)
    _dump("energy_hourly_yesterday", body)
    print(f"  HTTP {status} — {len(rows)} row(s)")
    if status >= 400:
        print(f"  Error body: {json.dumps(body, ensure_ascii=False)}")

    # ---- energy: try timespan=hourly with today ----
    params_ht = {"year": str(today.year), "month": str(today.month), "day": str(today.day), "timespan": "hourly"}
    print(f"\n[4] GET energy hourly (today={today})")
    status, body = await _get_raw(session, energy_url, token, params=params_ht)
    rows = _list_rows(body)
    _dump("energy_hourly_today", body)
    print(f"  HTTP {status} — {len(rows)} row(s)")
    if status >= 400:
        print(f"  Error body: {json.dumps(body, ensure_ascii=False)}")
    elif rows:
        _show_sample(rows)

    # ---- energy: try timespan=monthly (month-level aggregates) ----
    params_m = {"year": str(today.year), "month": str(today.month), "timespan": "monthly"}
    print(f"\n[5] GET energy monthly (year={today.year} month={today.month})")
    status, body = await _get_raw(session, energy_url, token, params=params_m)
    rows = _list_rows(body)
    _dump("energy_monthly", body)
    print(f"  HTTP {status} — {len(rows)} row(s)")
    if status >= 400:
        print(f"  Error body: {json.dumps(body, ensure_ascii=False)}")
    elif rows:
        _show_sample(rows)

    # ---- invoices: confirmed params from browser DevTools ----
    print("\n[6] GET invoices (status=open and status=paid)")
    all_invoices: list = []
    for inv_status in ("open", "paid"):
        url = BASE_URL + f"/api/customers/{customer}/invoices"
        status_code, body = await _get_raw(session, url, token, params={"status": inv_status})
        _dump(f"invoices_{inv_status}", body)
        if status_code >= 400:
            print(f"  status={inv_status!r}: HTTP {status_code} — Error: {json.dumps(body, ensure_ascii=False)}")
            continue
        # Decode compressed format if present (server returns compressedInvoices columnar structure)
        if isinstance(body, dict) and "compressedInvoices" in body:
            rows = _decompress_invoices(body, inv_status)
            print(f"  status={inv_status!r}: HTTP {status_code} — {len(rows)} invoice(s) (decoded from compressedInvoices)")  # noqa: E501
        else:
            rows = _list_rows(body)
            print(f"  status={inv_status!r}: HTTP {status_code} — {len(rows)} invoice(s)")
        if rows:
            _show_sample(rows, f"first {inv_status} invoice")
            all_invoices.extend(rows)
    print(f"  Total across both calls: {len(all_invoices)} invoice(s)")
    if all_invoices:
        print(f"  All invoice keys seen: {sorted({k for inv in all_invoices for k in inv})}")

    # ---- prices: try multiple candidate paths ----
    prices_candidates = [
        f"/api/customers/{customer}/assets/{mp}/prices",
        f"/api/customers/{customer}/assets/{mp}/pricelist",
        f"/api/customers/{customer}/contracts/{contract_id}/prices",
        f"/api/customers/{customer}/assets/{mp}/tariff",
        f"/api/customers/{customer}/pricelist",
    ]
    print("\n[7] Probing prices endpoint candidates...")
    for path_candidate in prices_candidates:
        url = BASE_URL + path_candidate
        status, body = await _get_raw(session, url, token)
        msg = ""
        if isinstance(body, dict) and body.get("msg"):
            msg = f"  msg={body['msg']!r}"
        marker = "✓" if status == 200 else "✗"
        print(f"  {marker} {status}  {path_candidate}{msg}")
        if status == 200:
            _dump(f"prices_{path_candidate.replace('/', '_')}", body)
            if isinstance(body, dict):
                print(f"    Keys: {list(body.keys())}")
                print(f"    Body: {json.dumps(body, ensure_ascii=False)}")

    print("\nDone. Successful responses saved to /tmp/caruna_probe_*.json")
    return 0


async def main() -> int:
    username = os.environ.get("CARUNA_USERNAME")
    password = os.environ.get("CARUNA_PASSWORD")
    if not username or not password:
        print("ERROR: set CARUNA_USERNAME and CARUNA_PASSWORD in the environment.")
        return 2

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("custom_components.caruna_plus.auth").setLevel(logging.DEBUG)

    async with aiohttp.ClientSession() as session:
        return await probe(session, username, password)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
