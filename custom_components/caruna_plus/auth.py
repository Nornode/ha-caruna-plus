"""
Caruna+ authentication.

Implements the Wicket/OAuth2 redirect chain used by plus.caruna.fi:

    1. POST /api/authorization/login              → JSON with loginRedirectUrl
    2. GET  loginRedirectUrl                      → HTML meta-refresh to IDP
    3. GET  IDP (authentication2.caruna.fi)       → Wicket HTML form
    4. POST credentials via Wicket AJAX           → CDATA redirect to next URL
    5. GET  next URL (meta-refresh)               → ngpostResponder auto-submit form
    6. POST form to commonauth                    → 302 → oauth2/authorize?sessionDataKey=…
    7. GET  oauth2/authorize (no follow)          → 302 to plus.caruna.fi with code+state
       POST code+state (form-encoded) to /token  → bearer token

MFA is detected by inspecting step 4's response for a challenge form.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .const import (
    AUTH_BASE_URL,
    BASE_URL,
    EP_LOGIN,
    EP_TOKEN,
    LOGIN_TIMEOUT_SECONDS,
    TOKEN_REFRESH_MARGIN_SECONDS,
    WICKET_COMPONENT_PATH,
    WICKET_FOCUS_ELEMENT,
    WICKET_LOGIN_BUTTON,
    WICKET_PASSWORD_FIELD,
    WICKET_USERNAME_FIELD,
)
from .models import TokenStore

_LOGGER = logging.getLogger(__name__)


def _dump_debug(path: str, body: str) -> None:
    """Write body to path for post-mortem inspection. Silent on errors."""
    try:
        import pathlib  # noqa: PLC0415
        pathlib.Path(path).write_text(body, encoding="utf-8", errors="replace")
    except OSError:
        pass


_META_REFRESH_RE = re.compile(
    r"""url\s*=\s*['"]?([^'">\s]+)""",
    re.IGNORECASE,
)
_CDATA_URL_RE = re.compile(r"CDATA\[.*?redirect.*?['\"]([^'\"]+)['\"]", re.IGNORECASE | re.DOTALL)


class CarunaError(Exception):
    """Base error for the Caruna+ client."""


class CarunaAuthError(CarunaError):
    """Credentials rejected or account locked. Triggers reauth."""


class CarunaMFARequired(CarunaError):
    """Login reached an MFA challenge. Config flow shows async_step_mfa."""

    def __init__(self, prompt: str, state: dict[str, Any]) -> None:
        super().__init__(prompt)
        self.prompt = prompt
        self.state = state


class CarunaConnectionError(CarunaError):
    """Network / 5xx. Triggers UpdateFailed."""


class CarunaRateLimitError(CarunaError):
    """429. Coordinator honours Retry-After."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Rate limited")
        self.retry_after = retry_after


class CarunaAPIError(CarunaError):
    """Unexpected 4xx."""


class CarunaAuthenticator:
    """Handles login, token storage, preemptive + reactive refresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        token_store: TokenStore | None = None,
        loop_executor: Any = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._store = token_store or TokenStore()
        self._lock = asyncio.Lock()
        self._loop_executor = loop_executor  # hass.async_add_executor_job or None

        # Populated from the login response's `info['user']` block so callers
        # can pick a customer without re-decoding the JWT.
        self.user_info: dict[str, Any] = {}

    @property
    def token_store(self) -> TokenStore:
        return self._store

    async def _parse_html(self, text: str) -> BeautifulSoup:
        """Run bs4 in the executor — it's sync CPU work."""
        if self._loop_executor is not None:
            return await self._loop_executor(BeautifulSoup, text, "html.parser")
        # In tests we run synchronously.
        return BeautifulSoup(text, "html.parser")

    async def async_ensure_token(self) -> str:
        """Return a valid bearer token, refreshing preemptively if needed."""
        if not self._store.is_expired(TOKEN_REFRESH_MARGIN_SECONDS):
            assert self._store.access_token is not None
            return self._store.access_token
        return await self.async_login()

    async def async_login(self, mfa_code: str | None = None, mfa_state: dict[str, Any] | None = None) -> str:
        """Perform (or resume) the full Wicket login chain.

        Guarded by an asyncio.Lock so concurrent callers reuse a single login.
        """
        async with self._lock:
            # Double-check inside the lock — a peer may have completed login
            # while we were waiting.
            if mfa_code is None and not self._store.is_expired(TOKEN_REFRESH_MARGIN_SECONDS):
                assert self._store.access_token is not None
                return self._store.access_token
            try:
                return await asyncio.wait_for(
                    self._login_impl(mfa_code=mfa_code, mfa_state=mfa_state),
                    timeout=LOGIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as err:
                raise CarunaConnectionError("Login timed out") from err

    async def _login_impl(self, *, mfa_code: str | None, mfa_state: dict[str, Any] | None) -> str:
        # Step 1: initiate login
        payload = {
            "redirectAfterLogin": f"{BASE_URL}/",
            "language": "fi",
        }
        try:
            async with self._session.post(EP_LOGIN, json=payload) as resp:
                if resp.status >= 500:
                    raise CarunaConnectionError(f"Login init failed: {resp.status}")
                if resp.status >= 400:
                    raise CarunaAPIError(f"Login init HTTP {resp.status}")
                # Caruna's server sends JSON with Content-Type: text/html, so
                # we must bypass aiohttp's content-type check.
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"Login init network error: {err}") from err

        redirect_url = data.get("loginRedirectUrl")
        if not redirect_url:
            raise CarunaAPIError("Login response missing loginRedirectUrl")

        # Step 2: follow initial redirect → landing page with meta-refresh
        landing_html = await self._get_text(redirect_url)
        idp_url = self._extract_meta_refresh(landing_html, base=redirect_url)
        if not idp_url:
            raise CarunaAPIError("Missing meta-refresh to IDP")

        # Step 3: fetch IDP login form
        form_html = await self._get_text(idp_url)
        soup = await self._parse_html(form_html)
        form = soup.find("form")
        if form is None:
            raise CarunaAPIError("IDP login form not found")

        hidden_fields = {
            inp.get("name"): inp.get("value", "")
            for inp in form.find_all("input", {"type": "hidden"})
            if inp.get("name")
        }
        action = form.get("action") or ""
        form_base_url = urljoin(idp_url, action)

        # Build the Wicket AJAX callback URL.
        # Form action format:   ?{page}-{version}.-{formPath}       (no behavior index)
        # AJAX callback format: ?{page}-{version}.0-{formPath}-{button}  (index=0)
        # Insert the "0" before the "-" that follows the dot in the version segment.
        ajax_base = re.sub(r"(\d+\.)(-)", r"\g<1>0\2", form_base_url)
        submit_input = form.find("input", {"type": "submit", "name": True})
        btn_component_id = submit_input.get("name") if submit_input else WICKET_LOGIN_BUTTON
        submit_url = f"{ajax_base}-{btn_component_id}"

        # For the focused-element header, use the visual button's HTML id.
        login_btn = soup.find(id=re.compile(r"loginWithUserID\d*$"))
        btn_focused_id = login_btn.get("id") if login_btn else btn_component_id
        _LOGGER.debug("Step 4 AJAX URL: %s (focused: %s)", submit_url, btn_focused_id)

        # Step 4: submit credentials via Wicket AJAX.
        # We POST to the form action + button component id (the AJAX callback URL).
        form_data = {
            **hidden_fields,
            WICKET_USERNAME_FIELD: self._username,
            WICKET_PASSWORD_FIELD: self._password,
        }
        ajax_headers = {
            "Wicket-Ajax": "true",
            "Wicket-Ajax-BaseURL": ".",
            "Wicket-FocusedElementId": btn_focused_id,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": AUTH_BASE_URL,
            "Referer": idp_url,
        }
        try:
            async with self._session.post(submit_url, data=form_data, headers=ajax_headers) as resp:
                _LOGGER.debug(
                    "Step 4 AJAX response: status=%s final_url=%s",
                    resp.status,
                    resp.url,
                )
                if resp.status == 401 or resp.status == 403:
                    raise CarunaAuthError("Invalid credentials")
                if resp.status >= 500:
                    raise CarunaConnectionError(f"Credential submit failed: {resp.status}")
                if resp.status >= 400:
                    raise CarunaAPIError(f"Credential submit HTTP {resp.status}")
                ajax_body = await resp.text()
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"Credential submit network error: {err}") from err

        # If Wicket returned errorGenericPage via AJAX, fall back to a plain form
        # POST.  This happens when the session state doesn't survive to the AJAX
        # handler — a plain POST is more forgiving about page-instance matching.
        if "errorGenericPage" in ajax_body:
            _LOGGER.debug("AJAX returned errorGenericPage — retrying as plain form POST")
            plain_form_data = {
                **hidden_fields,
                WICKET_USERNAME_FIELD: self._username,
                WICKET_PASSWORD_FIELD: self._password,
                WICKET_LOGIN_BUTTON: "",  # hidden submit button field
            }
            try:
                async with self._session.post(
                    form_base_url,
                    data=plain_form_data,
                    allow_redirects=True,
                ) as resp:
                    _LOGGER.debug(
                        "Step 4 plain POST response: status=%s final_url=%s",
                        resp.status,
                        resp.url,
                    )
                    if resp.status == 401 or resp.status == 403:
                        raise CarunaAuthError("Invalid credentials (plain POST)")
                    if resp.status >= 500:
                        raise CarunaConnectionError(f"Plain POST failed: {resp.status}")
                    if resp.status >= 400:
                        raise CarunaAPIError(f"Plain POST HTTP {resp.status}")
                    plain_body = await resp.text()
                    # If we followed redirects and ended up outside the IDP, the
                    # credentials were accepted.
                    if AUTH_BASE_URL not in str(resp.url):
                        ajax_body = plain_body
                        # Re-check below with the new body
                    else:
                        # Still on IDP — credentials rejected or another issue.
                        raise CarunaAuthError(
                            "Login rejected by plain form POST too — check credentials or account lock"
                        )
            except aiohttp.ClientError as err:
                raise CarunaConnectionError(f"Plain POST network error: {err}") from err

        # MFA challenge detection — heuristic; refine once we've seen a real one.
        if self._looks_like_mfa_challenge(ajax_body) and mfa_code is None:
            raise CarunaMFARequired(
                prompt="Enter the verification code sent by Caruna",
                state={"ajax_body": ajax_body, "submit_url": submit_url},
            )

        next_url = self._extract_cdata_url(ajax_body)
        if not next_url:
            # Some Wicket responses return the URL inside a component-replace,
            # which contains an escaped meta-refresh; fall back to that path.
            fallback = self._extract_meta_refresh(ajax_body, base=submit_url)
            if not fallback:
                raise CarunaAuthError("Login rejected — no post-credentials redirect")
            next_url = fallback
        # Wicket sometimes returns relative redirect URLs — make them absolute.
        if next_url and not next_url.startswith("http"):
            next_url = urljoin(submit_url, next_url)
        # errorGenericPage is Wicket's "something went wrong" page, not a success path.
        if "errorGenericPage" in next_url:
            raise CarunaAuthError("Wicket returned errorGenericPage — invalid credentials or session expired")

        # Step 5–6: follow meta-refresh + final auto-submit form
        meta_html = await self._get_text(next_url)
        auto_url = self._extract_meta_refresh(meta_html, base=next_url)
        if not auto_url:
            raise CarunaAPIError("Missing meta-refresh after credentials")

        final_html = await self._get_text(auto_url)
        final_soup = await self._parse_html(final_html)
        final_form = final_soup.find("form")
        if final_form is None:
            raise CarunaAPIError("Final auto-submit form not found")
        final_action = urljoin(auto_url, final_form.get("action") or "")
        final_fields = {
            inp.get("name"): inp.get("value", "")
            for inp in final_form.find_all("input")
            if inp.get("name")
        }
        # POST the SAML/OAuth form to the commonauth endpoint.
        # It will redirect through authentication2.caruna.fi/oauth2/authorize
        # back to plus.caruna.fi with an authorization code.
        try:
            async with self._session.post(final_action, data=final_fields, allow_redirects=False) as resp:
                location = resp.headers.get("Location")
                _LOGGER.debug("Step 6 POST status=%s location_present=%s", resp.status, bool(location))
                if not location:
                    body = await resp.text()
                    _dump_debug("/tmp/caruna_smoke_step6_body.html", body)
                    location = self._extract_meta_refresh(body, base=final_action) or ""
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"Final form submit network error: {err}") from err

        if not location:
            raise CarunaAPIError("No OAuth redirect URL from commonauth")

        # Make location absolute.
        if not location.startswith("http"):
            location = urljoin(final_action, location)

        # Step 7: walk the OAuth2 redirect chain one hop at a time, stopping the
        # moment we see plus.caruna.fi in the Location.  We must NOT actually GET
        # the callback page (openid-login-return) — loading it appears to consume
        # the server-side OAuth2 state and causes EP_TOKEN to return 400.
        current_url = location
        url_params: dict[str, str] = {}
        for _hop in range(8):
            try:
                async with self._session.get(current_url, allow_redirects=False) as resp:
                    hop_location = resp.headers.get("Location", "")
                    _LOGGER.debug(
                        "Step 7 hop: url=%s status=%s loc=%s",
                        current_url,
                        resp.status,
                        hop_location,
                    )
                    if resp.status not in (301, 302, 303, 307, 308):
                        # Unexpected non-redirect — dump and bail
                        body = await resp.text()
                        _dump_debug("/tmp/caruna_smoke_step7_nonredirect.html", body)
                        raise CarunaAPIError(
                            f"OAuth redirect chain: unexpected status {resp.status}"
                        )
            except aiohttp.ClientError as err:
                raise CarunaConnectionError(
                    f"OAuth redirect chain network error: {err}"
                ) from err

            if not hop_location.startswith("http"):
                hop_location = urljoin(current_url, hop_location)

            if BASE_URL in hop_location:
                # This redirect is back to plus.caruna.fi — extract code + state
                # from the URL WITHOUT following the redirect so the server-side
                # OAuth2 state is still intact when we POST to EP_TOKEN.
                parsed_cb = urlparse(hop_location)
                url_params = {k: v[0] for k, v in parse_qs(parsed_cb.query).items()}
                _LOGGER.debug("Step 7 callback: url=%s params=%s", hop_location, list(url_params.keys()))
                break

            current_url = hop_location
        else:
            raise CarunaAPIError("OAuth redirect chain exceeded max hops")

        if not url_params:
            raise CarunaAPIError("No auth code in plus.caruna.fi callback URL")

        # Exchange the authorization code for a Caruna Plus token.
        # EP_TOKEN expects form-encoded data, not JSON.
        try:
            async with self._session.post(EP_TOKEN, data=url_params) as resp:
                _LOGGER.debug("Step 7 EP_TOKEN status=%s", resp.status)
                if resp.status == 401 or resp.status == 403:
                    raise CarunaAuthError("Token exchange rejected")
                if resp.status >= 500:
                    raise CarunaConnectionError(f"Token exchange failed: {resp.status}")
                if resp.status >= 400:
                    body = await resp.text()
                    _dump_debug("/tmp/caruna_smoke_step7_400.html", body)
                    raise CarunaAPIError(f"Token exchange HTTP {resp.status}")
                token_data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"Token exchange network error: {err}") from err

        access_token = token_data.get("token") or token_data.get("access_token")
        if not access_token:
            raise CarunaAuthError("Token exchange returned no token")

        # Prefer server-provided expiry; fall back to 55 minutes.
        expires_in = token_data.get("expiresIn") or token_data.get("expires_in") or 3300
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

        user_info = token_data.get("user") or {}
        customers = list(user_info.get("ownCustomerNumbers") or [])

        self._store.access_token = access_token
        self._store.expires_at = expires_at
        if customers:
            self._store.customer_numbers = customers
        self.user_info = user_info

        _LOGGER.debug("Caruna+ login OK; %d customer(s); token expires %s", len(customers), expires_at.isoformat())
        return access_token

    async def async_invalidate_token(self) -> None:
        """Mark the stored token as expired so the next call re-authenticates."""
        self._store.access_token = None
        self._store.expires_at = None

    async def _get_text(self, url: str) -> str:
        try:
            async with self._session.get(url) as resp:
                if resp.status >= 500:
                    raise CarunaConnectionError(f"GET {url} → {resp.status}")
                if resp.status >= 400:
                    raise CarunaAPIError(f"GET {url} → {resp.status}")
                return await resp.text()
        except aiohttp.ClientError as err:
            raise CarunaConnectionError(f"GET {url} network error: {err}") from err

    def _extract_meta_refresh(self, html: str, *, base: str) -> str | None:
        # Locate `<meta http-equiv="refresh" content="0;URL=...">`.
        soup = BeautifulSoup(html, "html.parser")
        meta = soup.find("meta", attrs={"http-equiv": lambda v: v and v.lower() == "refresh"})
        if meta is None:
            return None
        content = meta.get("content", "")
        match = _META_REFRESH_RE.search(content)
        if not match:
            return None
        return urljoin(base, match.group(1))

    def _extract_cdata_url(self, body: str) -> str | None:
        # Most common Wicket format: <redirect><![CDATA[https://...]]></redirect>
        # The URL is the raw CDATA content with no surrounding quotes.
        direct = re.search(r"<!\[CDATA\[(https?://[^\]]+)\]\]>", body, re.IGNORECASE)
        if direct:
            return direct.group(1).strip()
        # Older format: CDATA block with redirect URL inside quotes.
        match = _CDATA_URL_RE.search(body)
        if match:
            return match.group(1)
        # Final fallback: parse <redirect> element. Use html.parser — no lxml needed.
        soup = BeautifulSoup(body, "html.parser")
        redirect_el = soup.find("redirect")
        if redirect_el is not None and redirect_el.text:
            return redirect_el.text.strip()
        return None

    def _looks_like_mfa_challenge(self, body: str) -> bool:
        lowered = body.lower()
        needles = ("mfa", "one-time", "verification code", "vahvistuskoodi", "sms")
        return any(needle in lowered for needle in needles)
