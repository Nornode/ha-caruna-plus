"""Config flow for Caruna+."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CarunaAuthError,
    CarunaConnectionError,
    CarunaPlusClient,
)
from .auth import CarunaMFARequired
from .const import (
    CONF_CUSTOMER,
    CONF_ENABLE_HOURLY,
    CONF_UPDATE_INTERVAL,
    DATA_TOKEN,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    MIN_UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_MFA_SCHEMA = vol.Schema({vol.Required("code"): str})

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class CarunaPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup UI."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._client: CarunaPlusClient | None = None
        self._customers: list[str] = []
        self._mfa_prompt: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            result = await self._try_login()
            if result is not None:
                return result
            errors["base"] = "invalid_auth" if self._last_error == "auth" else self._last_error
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._client is not None:
            try:
                await self._client.async_login(mfa_code=user_input["code"])
            except CarunaAuthError:
                errors["base"] = "invalid_mfa"
            except CarunaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish_login()
        return self.async_show_form(
            step_id="mfa",
            data_schema=STEP_MFA_SCHEMA,
            description_placeholders={"prompt": self._mfa_prompt or ""},
            errors=errors,
        )

    async def async_step_select_customer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create_entry(user_input[CONF_CUSTOMER])
        schema = vol.Schema({vol.Required(CONF_CUSTOMER): vol.In(self._customers)})
        return self.async_show_form(step_id="select_customer", data_schema=schema)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self._reauth_entry is not None:
            self._username = self._reauth_entry.data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            result = await self._try_login()
            if result is not None:
                # Update the existing entry rather than creating a new one.
                assert self._reauth_entry is not None
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: self._password,
                        DATA_TOKEN: (self._client.token_store.to_dict() if self._client else None),
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            errors["base"] = "invalid_auth" if self._last_error == "auth" else self._last_error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"username": self._username or ""},
            errors=errors,
        )

    async def _try_login(self) -> ConfigFlowResult | None:
        """Attempt login. On success, either finish, pick a customer, or ask for MFA."""
        assert self._username and self._password
        session = async_get_clientsession(self.hass)
        self._client = CarunaPlusClient(
            session=session,
            username=self._username,
            password=self._password,
            loop_executor=self.hass.async_add_executor_job,
        )
        try:
            await self._client.async_login()
        except CarunaMFARequired as err:
            self._mfa_prompt = err.prompt
            return await self.async_step_mfa()
        except CarunaAuthError:
            self._last_error = "auth"
            return None
        except CarunaConnectionError:
            self._last_error = "cannot_connect"
            return None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during Caruna+ login")
            self._last_error = "unknown"
            return None
        return await self._finish_login()

    async def _finish_login(self) -> ConfigFlowResult:
        assert self._client is not None
        customers = await self._client.async_get_customers()
        self._customers = [c.number for c in customers]
        if self._reauth_entry is not None:
            # Reauth path — caller handles updating the entry.
            return self.async_abort(reason="reauth_successful")
        if len(self._customers) == 0:
            return self.async_abort(reason="no_customers")
        if len(self._customers) == 1:
            return await self._create_entry(self._customers[0])
        return await self.async_step_select_customer()

    async def _create_entry(self, customer: str) -> ConfigFlowResult:
        assert self._client is not None and self._username is not None and self._password is not None
        await self.async_set_unique_id(customer)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Caruna+ ({customer})",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_CUSTOMER: customer,
                DATA_TOKEN: self._client.token_store.to_dict(),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CarunaPlusOptionsFlow(config_entry)

    _last_error: str = "unknown"


class CarunaPlusOptionsFlow(OptionsFlow):
    """Options: update interval, hourly toggle."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
                ): vol.All(int, vol.Range(min=MIN_UPDATE_INTERVAL_MINUTES, max=1440)),
                vol.Optional(
                    CONF_ENABLE_HOURLY,
                    default=options.get(CONF_ENABLE_HOURLY, True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
