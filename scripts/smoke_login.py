#!/usr/bin/env python3
"""
Standalone Caruna+ auth smoke test.

Runs the exact same Wicket redirect chain the HA integration uses, but outside
Home Assistant so you can iterate in seconds. Prints each step as it happens.
On failure, dumps the offending response body to /tmp/caruna_smoke_stepN.html
so you can inspect what the server actually returned.

Usage:
    export CARUNA_USERNAME="you@example.com"
    export CARUNA_PASSWORD='your-password'
    python3 scripts/smoke_login.py

Dependencies:
    pip install aiohttp beautifulsoup4
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Make the custom_components package importable when run from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import aiohttp  # noqa: E402

from custom_components.caruna_plus.auth import (  # noqa: E402
    CarunaAPIError,
    CarunaAuthError,
    CarunaAuthenticator,
    CarunaConnectionError,
    CarunaMFARequired,
)

DUMP_DIR = Path("/tmp")


# ---- diagnostic wrapper ------------------------------------------------

STEP_LABELS = [
    "POST /api/authorization/login",
    "follow loginRedirectUrl",
    "fetch Wicket login form",
    "POST credentials",
    "follow post-credentials redirect",
    "fetch final auto-submit form",
    "POST token exchange",
]


class StepReporter:
    """Wraps aiohttp.ClientSession to print + dump each request/response."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self.step = 0

    def _next(self, label: str) -> int:
        self.step += 1
        return self.step

    async def _do(self, method: str, url: str, expected_step: int, **kwargs):
        label = STEP_LABELS[expected_step - 1] if expected_step <= len(STEP_LABELS) else url
        try:
            resp = await self._session.request(method, url, **kwargs)
        except aiohttp.ClientError as err:
            print(f"[step {expected_step}/7] {label:<42} NETWORK ERROR: {err}")
            raise
        body_text = await resp.text()
        ok = resp.status < 400
        marker = "OK " if ok else "FAIL"
        print(f"[step {expected_step}/7] {label:<42} {marker} (HTTP {resp.status})")
        if not ok:
            path = DUMP_DIR / f"caruna_smoke_step{expected_step}.html"
            path.write_text(body_text)
            print(f"                                             ↳ dumped body: {path}")
        return resp, body_text


# ---- monkey-patch the authenticator to report each hop -----------------

async def run() -> int:
    username = os.environ.get("CARUNA_USERNAME")
    password = os.environ.get("CARUNA_PASSWORD")
    if not username or not password:
        print("ERROR: set CARUNA_USERNAME and CARUNA_PASSWORD in the environment.")
        return 2

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    # Turn on our module's debug output; useful when a response parses but yields the wrong URL.
    logging.getLogger("custom_components.caruna_plus.auth").setLevel(logging.DEBUG)

    print("Caruna+ smoke test starting.")
    print("Any failure will dump the offending HTML into /tmp/caruna_smoke_stepN.html")
    print()

    async with aiohttp.ClientSession() as session:
        reporter = StepReporter(session)
        auth = _instrumented_authenticator(session, username, password, reporter)

        try:
            token = await auth.async_login()
        except CarunaMFARequired as err:
            print()
            print(f"MFA challenge detected at step {reporter.step}: {err.prompt}")
            print("The integration will show a code-entry form at this point.")
            print("Dumping the AJAX body for inspection:")
            path = DUMP_DIR / "caruna_smoke_mfa_body.html"
            path.write_text(err.state.get("ajax_body", ""))
            print(f"  {path}")
            return 3
        except CarunaAuthError as err:
            print()
            print(f"AUTH REJECTED: {err}")
            print("Diagnostic files written:")
            print("  /tmp/caruna_smoke_step3.html  — the IDP form we parsed (what form did we find?)")
            print("  /tmp/caruna_smoke_step4.html  — the credential POST response (what did the server say?)")
            print()
            print("Please run:")
            print("  head -150 /tmp/caruna_smoke_step3.html")
            print("  head -150 /tmp/caruna_smoke_step4.html")
            print("and paste the output.")
            return 4
        except CarunaConnectionError as err:
            print()
            print(f"CONNECTION ERROR: {err}")
            return 5
        except CarunaAPIError as err:
            print()
            print(f"API ERROR: {err}")
            print(f"Check /tmp/caruna_smoke_step{reporter.step}.html for the response body.")
            return 6

        print()
        # Redact all but the first 6 chars of the token.
        tok_display = (token[:6] + "..." + f"<{len(token)} chars>") if token else "<none>"
        print(f"Access token: {tok_display}")
        if auth.token_store.expires_at:
            print(f"Token expires at: {auth.token_store.expires_at.isoformat()}")
        customers = auth.token_store.customer_numbers
        print(f"Customer numbers: {customers}")

        # --- Bonus: fetch /assets to verify field names ---
        if customers:
            print()
            print("Fetching assets to verify API field names...")
            from custom_components.caruna_plus.api import CarunaPlusClient  # noqa: PLC0415
            import json  # noqa: PLC0415
            client = CarunaPlusClient(session, username, password,
                                      token_store=auth.token_store)
            try:
                customer = customers[0]
                token_value = await client.auth.async_ensure_token()
                hdrs = {"Authorization": f"Bearer {token_value}"}
                url = f"https://plus.caruna.fi/api/customers/{customer}/assets"
                async with session.get(url, headers=hdrs) as resp:
                    raw = await resp.json(content_type=None)
                dump = json.dumps(raw, indent=2, ensure_ascii=False)
                Path("/tmp/caruna_smoke_assets.json").write_text(dump)
                items = raw if isinstance(raw, list) else raw.get("assets", [raw])
                print(f"  {len(items)} asset(s) returned")
                if items:
                    print(f"  Keys in first asset: {list(items[0].keys())}")
                    print(f"  First asset (pretty):")
                    print("  " + json.dumps(items[0], indent=4, ensure_ascii=False)
                          .replace("\n", "\n  "))
            except Exception as err:  # noqa: BLE001
                print(f"  Assets fetch failed: {err}")

    print()
    print("Auth flow works. You can proceed to Phase C in docs/TESTING.md.")
    return 0


def _instrumented_authenticator(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    reporter: StepReporter,
) -> CarunaAuthenticator:
    """Patch the authenticator's private helpers to report each step.

    We wrap _get_text and the internal aiohttp calls so the console shows a
    clean 7-line story instead of raw debug logging.
    """
    auth = CarunaAuthenticator(session, username, password)

    # Monkey-patch _get_text to increment the reporter counter and dump on failure.
    original_get_text = auth._get_text

    async def counting_get_text(url: str) -> str:
        step = reporter._next(url)
        try:
            text = await original_get_text(url)
            label = STEP_LABELS[step - 1] if step <= len(STEP_LABELS) else "GET " + url
            print(f"[step {step}/7] {label:<42} OK  (→ {_short(url)})")
            return text
        except (CarunaConnectionError, CarunaAPIError) as err:
            label = STEP_LABELS[step - 1] if step <= len(STEP_LABELS) else "GET " + url
            print(f"[step {step}/7] {label:<42} FAIL ({err})")
            raise

    auth._get_text = counting_get_text  # type: ignore[assignment]

    # Wrap _login_impl so we can also see the terminating POSTs succeed.
    original_login = auth._login_impl

    async def wrapped_login(**kwargs):
        # Bump so step numbers match STEP_LABELS ordering:
        # login POST is step 1 (done internally by the impl, not via _get_text).
        reporter._next("initiate")
        print(f"[step 1/7] {STEP_LABELS[0]:<42} STARTED")
        result = await original_login(**kwargs)
        print(f"[step 7/7] {STEP_LABELS[6]:<42} OK")
        return result

    auth._login_impl = wrapped_login  # type: ignore[assignment]
    return auth


def _short(url: str, limit: int = 60) -> str:
    return url if len(url) <= limit else url[: limit - 3] + "..."


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
