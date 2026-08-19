"""Auth-flow tests. Each Wicket step is exercised via aioresponses."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.caruna_plus.auth import (
    CarunaAuthenticator,
    CarunaAuthError,
    CarunaConnectionError,
    CarunaMFARequired,
)
from custom_components.caruna_plus.const import (
    AUTH_BASE_URL,
    EP_LOGIN,
    EP_TOKEN,
)
from custom_components.caruna_plus.models import TokenStore

IDP_URL = f"{AUTH_BASE_URL}/portal/idp?flow=1"


@pytest.mark.asyncio
async def test_full_login_happy_path(
    wicket_landing_html,
    wicket_login_form_html,
    wicket_ajax_success_body,
    wicket_meta_refresh_html,
    wicket_final_form_html,
    token_response_json,
) -> None:
    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, payload={"loginRedirectUrl": f"{AUTH_BASE_URL}/landing"})
        mocked.get(f"{AUTH_BASE_URL}/landing", body=wicket_landing_html)
        mocked.get(IDP_URL, body=wicket_login_form_html)
        mocked.post(re.compile(r"https://authentication2\.caruna\.fi/portal.*"), body=wicket_ajax_success_body)
        mocked.get(
            re.compile(r"https://authentication2\.caruna\.fi/portal/idp\?flow=1&step=complete"),
            body=wicket_meta_refresh_html,
        )
        mocked.get(
            re.compile(r"https://plus\.caruna\.fi/api/authorization/callback\?code=XYZ.*"),
            body=wicket_final_form_html,
        )
        mocked.post(
            "https://plus.caruna.fi/api/authorization/callback",
            status=302,
            headers={"Location": "https://plus.caruna.fi/?code=XYZ&state=abc"},
        )
        mocked.post(EP_TOKEN, payload=token_response_json)

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "user@example.com", "hunter2")
            token = await auth.async_login()
            assert token == token_response_json["token"]
            assert auth.token_store.customer_numbers == ["12345678"]
            assert auth.token_store.expires_at is not None
            assert auth.token_store.expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_login_bad_credentials(wicket_landing_html, wicket_login_form_html) -> None:
    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, payload={"loginRedirectUrl": f"{AUTH_BASE_URL}/landing"})
        mocked.get(f"{AUTH_BASE_URL}/landing", body=wicket_landing_html)
        mocked.get(IDP_URL, body=wicket_login_form_html)
        mocked.post(re.compile(r"https://authentication2\.caruna\.fi/portal.*"), status=401)

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "user@example.com", "wrong")
            with pytest.raises(CarunaAuthError):
                await auth.async_login()


@pytest.mark.asyncio
async def test_login_detects_mfa_challenge(
    wicket_landing_html, wicket_login_form_html, wicket_ajax_mfa_body
) -> None:
    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, payload={"loginRedirectUrl": f"{AUTH_BASE_URL}/landing"})
        mocked.get(f"{AUTH_BASE_URL}/landing", body=wicket_landing_html)
        mocked.get(IDP_URL, body=wicket_login_form_html)
        mocked.post(re.compile(r"https://authentication2\.caruna\.fi/portal.*"), body=wicket_ajax_mfa_body)

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "user@example.com", "hunter2")
            with pytest.raises(CarunaMFARequired) as excinfo:
                await auth.async_login()
            assert "verification" in excinfo.value.prompt.lower()


@pytest.mark.asyncio
async def test_token_store_preemptive_refresh_bypasses_cache() -> None:
    """A fresh token store returns cached token without re-authenticating."""
    store = TokenStore(
        access_token="cached-token",
        expires_at=datetime.now(UTC).replace(year=datetime.now(UTC).year + 1),
        customer_numbers=["9999"],
    )
    async with aiohttp.ClientSession() as session:
        auth = CarunaAuthenticator(session, "u", "p", token_store=store)
        token = await auth.async_ensure_token()
        assert token == "cached-token"


@pytest.mark.asyncio
async def test_concurrent_logins_share_lock(
    wicket_landing_html,
    wicket_login_form_html,
    wicket_ajax_success_body,
    wicket_meta_refresh_html,
    wicket_final_form_html,
    token_response_json,
) -> None:
    """Two concurrent login() calls result in a single Wicket chain execution."""
    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, payload={"loginRedirectUrl": f"{AUTH_BASE_URL}/landing"})
        mocked.get(f"{AUTH_BASE_URL}/landing", body=wicket_landing_html)
        mocked.get(IDP_URL, body=wicket_login_form_html)
        mocked.post(re.compile(r"https://authentication2\.caruna\.fi/portal.*"), body=wicket_ajax_success_body)
        mocked.get(
            re.compile(r"https://authentication2\.caruna\.fi/portal/idp\?flow=1&step=complete"),
            body=wicket_meta_refresh_html,
        )
        mocked.get(
            re.compile(r"https://plus\.caruna\.fi/api/authorization/callback\?code=XYZ.*"),
            body=wicket_final_form_html,
        )
        mocked.post(
            "https://plus.caruna.fi/api/authorization/callback",
            status=302,
            headers={"Location": "https://plus.caruna.fi/?code=XYZ&state=abc"},
        )
        mocked.post(EP_TOKEN, payload=token_response_json)

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "u", "p")
            token_a, token_b = await asyncio.gather(auth.async_login(), auth.async_login())
            assert token_a == token_b == token_response_json["token"]


@pytest.mark.asyncio
async def test_expired_token_triggers_full_relogin(
    wicket_landing_html,
    wicket_login_form_html,
    wicket_ajax_success_body,
    wicket_meta_refresh_html,
    wicket_final_form_html,
    token_response_json,
) -> None:
    """async_ensure_token with an expired store runs the full Wicket chain."""
    expired_store = TokenStore(
        access_token="stale-token",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        customer_numbers=["9999"],
    )

    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, payload={"loginRedirectUrl": f"{AUTH_BASE_URL}/landing"})
        mocked.get(f"{AUTH_BASE_URL}/landing", body=wicket_landing_html)
        mocked.get(IDP_URL, body=wicket_login_form_html)
        mocked.post(re.compile(r"https://authentication2\.caruna\.fi/portal.*"), body=wicket_ajax_success_body)
        mocked.get(
            re.compile(r"https://authentication2\.caruna\.fi/portal/idp\?flow=1&step=complete"),
            body=wicket_meta_refresh_html,
        )
        mocked.get(
            re.compile(r"https://plus\.caruna\.fi/api/authorization/callback\?code=XYZ.*"),
            body=wicket_final_form_html,
        )
        mocked.post(
            "https://plus.caruna.fi/api/authorization/callback",
            status=302,
            headers={"Location": "https://plus.caruna.fi/?code=XYZ&state=abc"},
        )
        mocked.post(EP_TOKEN, payload=token_response_json)

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "u", "p", token_store=expired_store)
            token = await auth.async_ensure_token()

        assert token == token_response_json["token"]
        assert auth.token_store.access_token == token_response_json["token"]


@pytest.mark.asyncio
async def test_login_step1_network_error() -> None:
    """A network error on the very first POST raises CarunaConnectionError."""
    with aioresponses() as mocked:
        mocked.post(EP_LOGIN, exception=aiohttp.ClientConnectionError("refused"))

        async with aiohttp.ClientSession() as session:
            auth = CarunaAuthenticator(session, "u", "p")
            with pytest.raises(CarunaConnectionError):
                await auth.async_login()
