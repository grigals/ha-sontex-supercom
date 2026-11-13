"""Set up the integration from the Home Assistant UI."""

from __future__ import annotations

import ftplib
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DIRECTORY,
    CONF_LOOKBACK_DAYS,
    CONF_PULSE_MEDIUM,
    CONF_SCAN_HOURS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PORT,
    DEFAULT_PULSE_MEDIUM,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
    PULSE_AUTO,
    PULSE_GAS,
    PULSE_WATER,
)
from .ftp import count_readouts

_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    vol.Optional(CONF_DIRECTORY, default="/"): str,
})


class SontexConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            host = host.removeprefix("ftp://").rstrip("/")
            data = {**user_input, CONF_HOST: host}
            await self.async_set_unique_id(f"{host}{data[CONF_DIRECTORY]}".lower())
            self._abort_if_unique_id_configured()
            try:
                await self.hass.async_add_executor_job(
                    count_readouts, host, data[CONF_PORT], data[CONF_USERNAME],
                    data[CONF_PASSWORD], data[CONF_DIRECTORY])
            except ftplib.error_perm as err:
                errors["base"] = ("invalid_auth" if str(err).startswith("530")
                                  else "bad_directory")
            except (OSError, EOFError, ftplib.Error):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=f"Sontex ({host})", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_USER_SCHEMA, user_input or {}),
            errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SontexOptionsFlow()


class SontexOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_PULSE_MEDIUM,
                         default=options.get(CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM)):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[PULSE_AUTO, PULSE_WATER, PULSE_GAS],
                    translation_key=CONF_PULSE_MEDIUM)),
            vol.Required(CONF_SCAN_HOURS,
                         default=options.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)):
                vol.All(int, vol.Range(min=1, max=48)),
            vol.Required(CONF_LOOKBACK_DAYS,
                         default=options.get(CONF_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS)):
                vol.All(int, vol.Range(min=1, max=3650)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
