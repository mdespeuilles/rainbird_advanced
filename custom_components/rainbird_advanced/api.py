"""Single choke point for all Rain Bird device access.

The LNK2 WiFi module accepts one connection at a time, so every request from
this integration is serialized through one lock and retried with backoff when
the device reports it is busy.

Nothing outside this module should call the pyrainbird controller directly.
"""

from __future__ import annotations

import asyncio
import inspect
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

# Hard per-request ceiling. pyrainbird only installs its own 10s timeout when
# min_delay is set (6.5.0+); on 6.3.x there is none, so aiohttp's 5-minute
# default would apply and a request that never gets answered -- e.g. while the
# official integration holds the device's single connection -- could hang
# startup for minutes.
REQUEST_TIMEOUT = 15.0


def async_create_device_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Create a session that opens at most one connection to the device.

    Deliberately not async_create_clientsession(): that shares Home Assistant's
    connector pool, which would discard the connection limit.
    """
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=CONNECTION_LIMIT),
        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
    )


async def async_create_api(
    session: aiohttp.ClientSession, host: str, password: str
) -> RainbirdApi:
    """Connect to a controller and wrap it.

    min_delay was only added to create_controller in pyrainbird 6.5.0. Home
    Assistant loads a single pyrainbird for the whole process, so while the
    official rainbird integration is installed its older pin (6.3.x) wins and
    min_delay is rejected. We degrade gracefully: our own lock is what actually
    serializes access, so losing the library's inter-request pacing is
    harmless.
    """
    if "min_delay" in inspect.signature(create_controller).parameters:
        controller = await create_controller(
            session, host, password, min_delay=MIN_DELAY
        )
    else:
        controller = await create_controller(session, host, password)
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

    async def irrigate_zone(self, zone: int, minutes: int) -> None:
        """Start a single zone for the given number of minutes."""
        await self.execute(lambda: self._controller.irrigate_zone(zone, minutes))

    async def stop_irrigation(self) -> None:
        """Stop all irrigation."""
        await self.execute(self._controller.stop_irrigation)

    async def set_rain_delay(self, days: int) -> None:
        """Set the rain delay in days."""
        await self.execute(lambda: self._controller.set_rain_delay(days))

    async def run_program(self, program: int) -> None:
        """Manually run a full program by 0-based index."""
        await self.execute(lambda: self._controller.set_program(program))

    async def advance_zone(self, steps: int = 1) -> None:
        """Advance irrigation to a later zone."""
        await self.execute(lambda: self._controller.advance_zone(steps))

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
