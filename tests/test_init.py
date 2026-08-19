"""Integration lifecycle tests (async_setup_entry / async_unload_entry)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.caruna_plus.coordinator import CarunaPlusCoordinator, CarunaPlusData


async def test_setup_entry_and_unload(hass: HomeAssistant, mock_config_entry) -> None:
    """Full setup + unload lifecycle with a mocked coordinator data fetch."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.caruna_plus.CarunaPlusClient") as mock_cls,
        patch.object(
            CarunaPlusCoordinator,
            "_async_update_data",
            AsyncMock(return_value=CarunaPlusData()),
        ),
    ):
        mock_cls.return_value.token_store.to_dict.return_value = {}
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_entry_auth_failed(hass: HomeAssistant, mock_config_entry) -> None:
    """ConfigEntryAuthFailed during first refresh → entry state is SETUP_ERROR."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.caruna_plus.CarunaPlusClient"),
        patch.object(
            CarunaPlusCoordinator,
            "_async_update_data",
            AsyncMock(side_effect=ConfigEntryAuthFailed("bad password")),
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_not_ready(hass: HomeAssistant, mock_config_entry) -> None:
    """UpdateFailed during first refresh → HA retries (SETUP_RETRY)."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch("custom_components.caruna_plus.CarunaPlusClient"),
        patch.object(
            CarunaPlusCoordinator,
            "_async_update_data",
            AsyncMock(side_effect=UpdateFailed("network down")),
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
