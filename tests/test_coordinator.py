"""Coordinator smoke test — requires pytest-homeassistant-custom-component."""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.mark.asyncio
async def test_coordinator_auth_error_bubbles_as_config_entry_auth_failed(
    hass, monkeypatch
) -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.caruna_plus.api import CarunaAuthError, CarunaPlusClient
    from custom_components.caruna_plus.coordinator import CarunaPlusCoordinator

    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)

    client = CarunaPlusClient(hass.data, "u", "p")  # type: ignore[arg-type]

    async def _fail(*args, **kwargs):
        raise CarunaAuthError("bad password")

    monkeypatch.setattr(client, "async_get_assets", _fail)

    coord = CarunaPlusCoordinator(hass, entry, client, customer="12345678")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._safe_call(lambda: client.async_get_assets("12345678"))


@pytest.mark.asyncio
async def test_coordinator_connection_error_raises_update_failed(
    hass, monkeypatch
) -> None:
    """CarunaConnectionError from a sub-fetcher raises UpdateFailed, not ConfigEntryAuthFailed."""
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.caruna_plus.api import CarunaConnectionError, CarunaPlusClient
    from custom_components.caruna_plus.coordinator import CarunaPlusCoordinator

    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    client = CarunaPlusClient(hass.data, "u", "p")  # type: ignore[arg-type]

    async def _fail(*args, **kwargs):
        raise CarunaConnectionError("network down")

    monkeypatch.setattr(client, "async_get_assets", _fail)

    coord = CarunaPlusCoordinator(hass, entry, client, customer="12345678")
    with pytest.raises(UpdateFailed):
        await coord._safe_call(lambda: client.async_get_assets("12345678"))


@pytest.mark.asyncio
async def test_coordinator_rate_limit_propagates_unmodified(
    hass, monkeypatch
) -> None:
    """CarunaRateLimitError propagates unchanged so the coordinator can honour Retry-After."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.caruna_plus.auth import CarunaRateLimitError
    from custom_components.caruna_plus.api import CarunaPlusClient
    from custom_components.caruna_plus.coordinator import CarunaPlusCoordinator

    entry = MockConfigEntry(domain="caruna_plus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    client = CarunaPlusClient(hass.data, "u", "p")  # type: ignore[arg-type]

    async def _fail(*args, **kwargs):
        raise CarunaRateLimitError(retry_after=60)

    monkeypatch.setattr(client, "async_get_assets", _fail)

    coord = CarunaPlusCoordinator(hass, entry, client, customer="12345678")
    with pytest.raises(CarunaRateLimitError) as excinfo:
        await coord._safe_call(lambda: client.async_get_assets("12345678"))
    assert excinfo.value.retry_after == pytest.approx(60)
