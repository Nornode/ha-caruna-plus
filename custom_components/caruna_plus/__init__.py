"""The Caruna+ integration.

Note on the try/except below: submodules `auth`, `api`, `models`, and `const`
are pure Python with no Home Assistant dependencies, so they should be usable
from tooling like `scripts/smoke_login.py` that runs outside HA. Importing any
submodule triggers this __init__.py, so we guard the HA-only imports.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.config_entries import ConfigEntry
except ImportError:
    # Standalone use (no `homeassistant` in the venv). The submodules that
    # don't touch HA remain fully importable.
    pass
else:
    from typing import Any

    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .api import CarunaAuthError, CarunaConnectionError, CarunaPlusClient
    from .const import CONF_CUSTOMER, DATA_TOKEN, DOMAIN
    from .coordinator import CarunaPlusCoordinator
    from .models import TokenStore

    PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

    async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Set up Caruna+ from a config entry."""
        session = async_get_clientsession(hass)
        token_store = TokenStore.from_dict(entry.data.get(DATA_TOKEN))

        client = CarunaPlusClient(
            session=session,
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            token_store=token_store,
            loop_executor=hass.async_add_executor_job,
        )

        customer = entry.data.get(CONF_CUSTOMER)
        coordinator = CarunaPlusCoordinator(hass, entry, client, customer=customer)

        try:
            await coordinator.async_config_entry_first_refresh()
        except CarunaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CarunaConnectionError as err:
            raise ConfigEntryNotReady(str(err)) from err

        _persist_token(hass, entry, client.token_store.to_dict())

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        entry.async_on_unload(entry.add_update_listener(_async_update_listener))

        return True

    async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Unload a config entry."""
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unload_ok:
            coordinator: CarunaPlusCoordinator | None = hass.data.get(DOMAIN, {}).pop(
                entry.entry_id, None
            )
            if coordinator is not None:
                _persist_token(hass, entry, coordinator.client.token_store.to_dict())
        return unload_ok

    async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Reload when options change (interval, hourly toggle, etc.)."""
        await hass.config_entries.async_reload(entry.entry_id)

    def _persist_token(
        hass: HomeAssistant, entry: ConfigEntry, token_dict: dict[str, Any]
    ) -> None:
        """Save the current token store into entry.data without triggering a reload."""
        if entry.data.get(DATA_TOKEN) == token_dict:
            return
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, DATA_TOKEN: token_dict},
        )
