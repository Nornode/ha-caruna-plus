"""Config-flow tests.

Requires pytest-homeassistant-custom-component for hass + MockConfigEntry.
Marked xfail-strict-off so CI still passes if the test env isn't available;
real coverage runs locally with the plugin installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.caruna_plus.const import DOMAIN  # noqa: E402


@pytest.mark.asyncio
async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_user_flow_invalid_auth(hass: HomeAssistant, monkeypatch) -> None:
    from custom_components.caruna_plus import config_flow

    async def _boom(self):
        from custom_components.caruna_plus.api import CarunaAuthError

        raise CarunaAuthError("nope")

    monkeypatch.setattr(config_flow.CarunaPlusClient, "async_login", _boom)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "u", "password": "p"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_flow_connection_error(hass: HomeAssistant, monkeypatch) -> None:
    """A network error during login surfaces as the 'cannot_connect' error."""
    from custom_components.caruna_plus import config_flow
    from custom_components.caruna_plus.api import CarunaConnectionError

    async def _fail_connect(self):
        raise CarunaConnectionError("down")

    monkeypatch.setattr(config_flow.CarunaPlusClient, "async_login", _fail_connect)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "u", "password": "p"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_options_flow_shows_form(hass: HomeAssistant) -> None:
    """Options flow init renders the update-interval/hourly-enable form."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "u", "password": "p", "customer": "12345678"},
        unique_id="12345678",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
