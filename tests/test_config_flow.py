"""Config-flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.caruna_plus import config_flow
from custom_components.caruna_plus.api import CarunaConnectionError
from custom_components.caruna_plus.auth import CarunaAuthError
from custom_components.caruna_plus.const import (
    CONF_CUSTOMER,
    CONF_ENABLE_HOURLY,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.caruna_plus.models import Customer, TokenStore


def _mock_client(customers: list[Customer] | None = None) -> MagicMock:
    """Build a mock CarunaPlusClient that logs in successfully."""
    client = MagicMock()
    client.async_login = AsyncMock()
    client.async_get_customers = AsyncMock(
        return_value=customers or [Customer(number="12345678", name="Test User")]
    )
    client.token_store = TokenStore(access_token="tok", customer_numbers=["12345678"])
    return client


async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_success_creates_entry(hass: HomeAssistant) -> None:
    """Happy path: valid credentials → CREATE_ENTRY with expected data keys."""
    with patch.object(config_flow, "CarunaPlusClient", return_value=_mock_client()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u@test.fi", CONF_PASSWORD: "secret"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CUSTOMER] == "12345678"
    assert result["data"][CONF_USERNAME] == "u@test.fi"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    async def _boom(self, mfa_code=None):
        raise CarunaAuthError("bad password")

    with patch.object(config_flow.CarunaPlusClient, "async_login", _boom):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u", CONF_PASSWORD: "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_connection_error(hass: HomeAssistant) -> None:
    async def _fail(self, mfa_code=None):
        raise CarunaConnectionError("network down")

    with patch.object(config_flow.CarunaPlusClient, "async_login", _fail):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u", CONF_PASSWORD: "p"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, mock_config_entry) -> None:
    """Attempting to set up the same customer number twice → ABORT already_configured."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(config_flow, "CarunaPlusClient", return_value=_mock_client()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u@test.fi", CONF_PASSWORD: "secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_no_customers_aborts(hass: HomeAssistant) -> None:
    """Login succeeds but account has no customer numbers → ABORT."""
    with patch.object(config_flow, "CarunaPlusClient", return_value=_mock_client(customers=[])):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "u@test.fi", CONF_PASSWORD: "secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_customers"


async def test_options_flow_shows_form(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves_options(hass: HomeAssistant, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_UPDATE_INTERVAL: 30, CONF_ENABLE_HOURLY: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_UPDATE_INTERVAL] == 30
    assert mock_config_entry.options[CONF_ENABLE_HOURLY] is False
