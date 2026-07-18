"""Single choke point for all Rain Bird device access.

The LNK2 WiFi module accepts one connection at a time, so every request from
this integration is serialized through one lock and retried with backoff when
the device reports it is busy.

Nothing outside this module should call the pyrainbird controller directly.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from pyrainbird.async_client import AsyncRainbirdController, create_controller
from pyrainbird.exceptions import RainbirdDeviceBusyException

from .const import BUSY_BACKOFF, BUSY_JITTER, MIN_DELAY

_LOGGER = logging.getLogger(__name__)

CONNECTION_LIMIT = 1


def async_create_device_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create a session that opens at most one connection to the device.

    Deliberately not async_create_clientsession(): that shares Home Assistant's
    connector pool, which would discard the connection limit.
    """
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=CONNECTION_LIMIT))


async def async_create_api(
    session: aiohttp.ClientSession, host: str, password: str
) -> RainbirdApi:
    """Connect to a controller and wrap it."""
    controller = await create_controller(session, host, password, min_delay=MIN_DELAY)
    return RainbirdApi(controller)


class RainbirdApi:
    """Serialize and pace access to one controller."""

    def __init__(self, controller: AsyncRainbirdController) -> None:
        """Initialize the API wrapper."""
        self._controller = controller
        self._lock = asyncio.Lock()

    @property
    def controller(self) -> AsyncRainbirdController:
        """Return the underlying controller.

        Only safe to call from inside an execute() callback, which holds the
        lock.
        """
        return self._controller

    async def execute[T](self, func: Callable[[], Awaitable[T]]) -> T:
        """Run a logical operation against the device, serialized and retried.

        The lock spans the whole operation rather than a single request, so a
        multi-request call such as get_schedule() can never be interleaved with
        a poll.
        """
        async with self._lock:
            return await self._with_backoff(func)

    async def _with_backoff[T](self, func: Callable[[], Awaitable[T]]) -> T:
        """Retry an operation while the device reports it is busy.

        Only RainbirdDeviceBusyException is retried. Auth failures, NACKs and
        coding errors are permanent and fail immediately.
        """
        for delay in (*BUSY_BACKOFF, None):
            try:
                return await func()
            except RainbirdDeviceBusyException:
                if delay is None:
                    raise
                # Jitter matters: our 30s poll and the official integration's
                # 60s poll would otherwise phase-align and collide on every
                # retry, forever.
                await asyncio.sleep(delay + random.uniform(0, BUSY_JITTER))
        raise AssertionError("unreachable")

    async def async_close(self) -> None:
        """Release resources."""

    def __repr__(self) -> str:
        """Return a debug representation."""
        return f"<RainbirdApi {self._controller!r}>"


async def async_probe_device(api: RainbirdApi) -> dict[str, Any]:
    """Fetch the identity of a controller.

    get_model_and_version() must be awaited before max_zones/max_programs are
    meaningful: they read a model attribute that this call populates as a side
    effect.
    """

    async def _probe() -> dict[str, Any]:
        controller = api.controller
        model_info = await controller.get_model_and_version()
        available = await controller.get_available_stations()
        wifi_params = await controller.get_wifi_params()
        serial_number = await controller.get_serial_number()
        return {
            "model_info": model_info,
            "mac_address": wifi_params.mac_address,
            "serial_number": serial_number,
            "zones": sorted(available.active_set),
        }

    return await api.execute(_probe)
