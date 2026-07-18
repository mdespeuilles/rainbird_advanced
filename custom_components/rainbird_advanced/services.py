"""Services for Rain Bird Advanced."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from pyrainbird.exceptions import RainbirdApiException
from pyrainbird.resources import RAINBIRD_COMMANDS

from .const import (
    ACK_RESPONSE,
    ATTR_ALLOW_UNSAFE,
    ATTR_COMMAND,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_MODE,
    ATTR_PARAMS,
    DOMAIN,
    MODE_RPC,
    MODE_SIP,
    RAW_COMMAND_MODES,
    RPC_READ_PREFIX,
    SERVICE_RAW_COMMAND,
)
from .models import RainbirdAdvConfigEntry

_LOGGER = logging.getLogger(__name__)

RAW_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_MODE, default=MODE_SIP): vol.In(RAW_COMMAND_MODES),
        vol.Optional(ATTR_PARAMS, default=list): vol.All(
            cv.ensure_list, [vol.Coerce(int)]
        ),
        vol.Optional(ATTR_ALLOW_UNSAFE, default=False): cv.boolean,
    }
)


def _sip_command_acts(command: str) -> bool:
    """Return True if a SIP command changes the controller rather than reads it."""
    return str(RAINBIRD_COMMANDS[command].get("response")) == ACK_RESPONSE


def _rpc_method_acts(method: str) -> bool:
    """Return True if a JSON-RPC method is not a plain getter."""
    return not method.startswith(RPC_READ_PREFIX)


def _guard(command: str, acts: bool, allow_unsafe: bool) -> None:
    """Refuse an acting command unless the caller opted in.

    Nothing is forbidden outright: this is an exploration tool and any command
    the controller understands remains reachable. The gate exists so that a
    typo cannot start watering the garden -- opening a valve should be a
    deliberate act, not an accident.
    """
    if acts and not allow_unsafe:
        raise ServiceValidationError(
            f"{command!r} acts on the controller (it can start or stop watering, "
            f"or change its settings) rather than just reading from it. Set "
            f"{ATTR_ALLOW_UNSAFE}: true if you really mean to send it."
        )


def _get_entry(hass: HomeAssistant, entry_id: str) -> RainbirdAdvConfigEntry:
    """Resolve a loaded config entry."""
    entry: RainbirdAdvConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            f"No Rain Bird Advanced config entry with id {entry_id}"
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            f"Rain Bird Advanced config entry {entry_id} is not loaded"
        )
    return entry


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register global services."""

    async def _async_raw_command(call: ServiceCall) -> ServiceResponse:
        """Send an arbitrary command to the controller.

        Admin-only: this reaches undocumented firmware commands and can open
        valves. It is an exploration tool, not an automation building block.
        """
        entry = _get_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        api = entry.runtime_data.api
        command = call.data[ATTR_COMMAND]
        mode = call.data[ATTR_MODE]
        params = call.data[ATTR_PARAMS]
        allow_unsafe = call.data[ATTR_ALLOW_UNSAFE]

        if mode == MODE_SIP:
            if command not in RAINBIRD_COMMANDS:
                raise ServiceValidationError(
                    f"Unknown SIP command {command!r}. Valid names come from "
                    "pyrainbird's RAINBIRD_COMMANDS, e.g. ModelAndVersionRequest."
                )
            _guard(command, _sip_command_acts(command), allow_unsafe)
        else:
            _guard(command, _rpc_method_acts(command), allow_unsafe)

        async def _run() -> dict[str, Any]:
            controller = api.controller
            if mode == MODE_RPC:
                # Public, but takes no parameters: only zero-arg JSON-RPC
                # methods such as getSettings or getWifiParams are reachable.
                return await controller.test_rpc_support(command)
            # Private API. Guarded by an exact pyrainbird pin and a signature
            # test, because it is the only route to name-based SIP commands
            # with encoding and response validation.
            return await controller._process_command(  # noqa: SLF001
                lambda resp: resp, command, *params
            )

        _LOGGER.debug(
            "raw_command: mode=%s command=%s params=%s", mode, command, params
        )
        try:
            # Through the same lock as the coordinators: an unserialized request
            # would break the one-connection-at-a-time guarantee.
            result = await api.execute(_run)
        except RainbirdApiException as err:
            raise HomeAssistantError(f"Rain Bird command failed: {err}") from err

        return {"result": result}

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RAW_COMMAND,
        _async_raw_command,
        schema=RAW_COMMAND_SCHEMA,
        # OPTIONAL rather than ONLY: setter commands reply with a bare ACK, and
        # forcing every caller to request a response for those is noise.
        supports_response=SupportsResponse.OPTIONAL,
    )
