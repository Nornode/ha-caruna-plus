"""Coordinator unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.caruna_plus.api import CarunaAuthError, CarunaConnectionError, CarunaPlusClient
from custom_components.caruna_plus.auth import CarunaRateLimitError
from custom_components.caruna_plus.coordinator import CarunaPlusCoordinator


def _make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[CarunaPlusClient, CarunaPlusCoordinator]:
    """Build a coordinator with a mock client session — no real HTTP ever made."""
    client = CarunaPlusClient(MagicMock(), "u", "p")
    coord = CarunaPlusCoordinator(hass, entry, client, customer="12345678")
    return client, coord


async def test_safe_call_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    client, coord = _make_coordinator(hass, entry)

    async def _fail(*args, **kwargs):
        raise CarunaAuthError("bad password")

    client.async_get_assets = _fail  # type: ignore[method-assign]

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._safe_call(lambda: client.async_get_assets("12345678"))


async def test_safe_call_connection_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    client, coord = _make_coordinator(hass, entry)

    async def _fail(*args, **kwargs):
        raise CarunaConnectionError("network down")

    client.async_get_assets = _fail  # type: ignore[method-assign]

    with pytest.raises(UpdateFailed):
        await coord._safe_call(lambda: client.async_get_assets("12345678"))


async def test_safe_call_rate_limit_propagates_unmodified(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    client, coord = _make_coordinator(hass, entry)

    async def _fail(*args, **kwargs):
        raise CarunaRateLimitError(retry_after=60)

    client.async_get_assets = _fail  # type: ignore[method-assign]

    with pytest.raises(CarunaRateLimitError) as excinfo:
        await coord._safe_call(lambda: client.async_get_assets("12345678"))
    assert excinfo.value.retry_after == pytest.approx(60)
