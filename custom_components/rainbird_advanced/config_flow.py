"""Config flow for Rain Bird Advanced."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdDeviceBusyException,
)

from .api import async_create_api, async_create_device_session, async_probe_device
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_ZONE_FLOW_RATES,
    CONF_ZONES,
    DEFAULT_FLOW_RATE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TIMEOUT_SECONDS,
)
from .models import RainbirdAdvConfigEntry

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _async_validate(hass: Any, host: str, password: str) -> dict[str, Any]:
    """Connect to the device and return its identity.

    Raises a ValueError subclass keyed to a config-flow error string.
    """
    session = async_create_device_session(hass)
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            api = await async_create_api(session, host, password)
            probe = await async_probe_device(api)
    finally:
        await session.close()
    return probe


class RainbirdAdvConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rain Bird Advanced."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            password = user_input[CONF_PASSWORD]
            try:
                probe = await _async_validate(self.hass, host, password)
            except RainbirdAuthException:
                errors["base"] = "invalid_auth"
            except RainbirdDeviceBusyException:
                # create_controller only falls back on connection errors, so a
                # busy device surfaces here rather than being retried.
                errors["base"] = "device_busy"
            except RainbirdApiException, TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error connecting to Rain Bird device")
                errors["base"] = "unknown"
            else:
                mac_address = probe["mac_address"]
                if not mac_address:
                    # Without a MAC there is no stable unique id, and the
                    # official integration moved off serial numbers precisely
                    # because they are not always present.
                    return self.async_abort(reason="no_mac")

                await self.async_set_unique_id(format_mac(mac_address))
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PASSWORD: password}
                )
                self._async_abort_entries_match({CONF_HOST: host})

                model_info = probe["model_info"]
                return self.async_create_entry(
                    title=f"{model_info.model_name} Advanced",
                    data={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                        CONF_MAC: mac_address,
                        CONF_SERIAL_NUMBER: probe["serial_number"],
                        # Cached so the options form can render its per-zone
                        # fields without talking to the device.
                        CONF_ZONES: probe["zones"],
                    },
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await _async_validate(self.hass, entry.data[CONF_HOST], password)
            except RainbirdAuthException:
                errors["base"] = "invalid_auth"
            except RainbirdDeviceBusyException:
                errors["base"] = "device_busy"
            except RainbirdApiException, TimeoutError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: RainbirdAdvConfigEntry,
    ) -> RainbirdAdvOptionsFlow:
        """Return the options flow."""
        # Zero-arg: config_entry is a read-only property on OptionsFlow, and
        # assigning it raises since HA 2025.12.
        return RainbirdAdvOptionsFlow()


class RainbirdAdvOptionsFlow(OptionsFlow):
    """Handle Rain Bird Advanced options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            flow_rates = {
                str(zone): user_input.pop(f"flow_rate_{zone}", DEFAULT_FLOW_RATE)
                for zone in self._zones
            }
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_ZONE_FLOW_RATES: flow_rates,
                }
            )

        options = self.config_entry.options
        current_rates = options.get(CONF_ZONE_FLOW_RATES, {})

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=5,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            )
        }
        # One field per zone rather than a single global rate: drip lines and
        # rotor heads differ by an order of magnitude, so one number would be
        # confidently wrong for every zone but one.
        for zone in self._zones:
            schema[
                vol.Optional(
                    f"flow_rate_{zone}",
                    default=float(current_rates.get(str(zone), DEFAULT_FLOW_RATE)),
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=1000,
                    step=0.1,
                    unit_of_measurement="L/min",
                    mode=NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    @property
    def _zones(self) -> list[int]:
        """Return the zones available on this controller."""
        return self.config_entry.data.get(CONF_ZONES, [])
