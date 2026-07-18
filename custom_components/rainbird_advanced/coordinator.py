"""Data update coordinators for Rain Bird Advanced.

Split in two on purpose: the fast coordinator issues two requests every 30s,
while get_schedule() alone is roughly twenty sequential requests. Keeping the
schedule on an hourly cycle is what makes the fast one affordable on a device
that answers one connection at a time.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdDeviceNackError,
)

from .api import RainbirdApi
from .const import (
    DEBOUNCER_COOLDOWN,
    DOMAIN,
    FAILURE_TOLERANCE,
    SCHEDULE_UPDATE_INTERVAL,
)
from .models import (
    RainbirdAdvConfigEntry,
    RainbirdAdvScheduleData,
    RainbirdAdvState,
)
from .run_tracker import ZoneRunTracker

_LOGGER = logging.getLogger(__name__)


class RainbirdAdvStateCoordinator(DataUpdateCoordinator[RainbirdAdvState]):
    """Poll fast-changing controller state."""

    config_entry: RainbirdAdvConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: RainbirdAdvConfigEntry,
        api: RainbirdApi,
        tracker: ZoneRunTracker,
        scan_interval: timedelta,
    ) -> None:
        """Initialize the state coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} state",
            config_entry=config_entry,
            update_interval=scan_interval,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=DEBOUNCER_COOLDOWN, immediate=False
            ),
        )
        self._api = api
        self._tracker = tracker
        self._failures = 0

    async def _async_update_data(self) -> RainbirdAdvState:
        """Fetch active zones and controller state."""
        try:
            data = await self._api.execute(self._fetch)
        except RainbirdAuthException as err:
            raise ConfigEntryAuthFailed(
                "Rain Bird device rejected the password"
            ) from err
        except (RainbirdApiException, TimeoutError) as err:
            return self._tolerate(err)

        self._failures = 0
        if self._tracker.observe(set(data.active_zones), data.fetched_at):
            self._tracker.async_schedule_save()
        return data

    async def _fetch(self) -> RainbirdAdvState:
        """Read the device. Runs while holding the API lock.

        Zone states come from get_zone_states() rather than
        ControllerState.active_station: the latter is a single int whose
        multi-zone meaning is undefined (pyrainbird carries a TODO on it),
        while get_zone_states() returns an authoritative bitmask for one
        request. active_station is kept as a diagnostic cross-check.
        """
        controller = self._api.controller
        zone_states = await controller.get_zone_states()
        controller_state = await controller.get_combined_controller_state()
        return RainbirdAdvState(
            active_zones=frozenset(zone_states.active_set),
            controller_state=controller_state,
            fetched_at=dt_util.utcnow(),
        )

    def _tolerate(self, err: Exception) -> RainbirdAdvState:
        """Absorb a transient failure without flapping entities.

        The device reports busy whenever the official integration or the Rain
        Bird app is talking to it, which means it is healthy, just occupied.
        Dropping every entity to unavailable for 30s each time we lose that
        race would make them useless.

        Returning the previous object rather than a copy is deliberate: its
        fetched_at is unchanged, so the tracker recognizes the replay and does
        not read it as "every zone stopped".
        """
        self._failures += 1
        if self._failures <= FAILURE_TOLERANCE and self.data is not None:
            _LOGGER.debug(
                "Tolerating Rain Bird failure %d/%d, keeping last known state: %s",
                self._failures,
                FAILURE_TOLERANCE,
                err,
            )
            return self.data

        raise UpdateFailed(f"Error communicating with Rain Bird device: {err}") from err


class RainbirdAdvScheduleCoordinator(DataUpdateCoordinator[RainbirdAdvScheduleData]):
    """Poll slow-changing data: the program schedule and the global disable flag."""

    config_entry: RainbirdAdvConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: RainbirdAdvConfigEntry,
        api: RainbirdApi,
    ) -> None:
        """Initialize the schedule coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} schedule",
            config_entry=config_entry,
            update_interval=SCHEDULE_UPDATE_INTERVAL,
        )
        self._api = api

    async def _async_update_data(self) -> RainbirdAdvScheduleData:
        """Fetch the schedule and the global disable flag."""
        try:
            return await self._api.execute(self._fetch)
        except RainbirdAuthException as err:
            raise ConfigEntryAuthFailed(
                "Rain Bird device rejected the password"
            ) from err
        except (RainbirdApiException, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching Rain Bird schedule: {err}") from err

    async def _fetch(self) -> RainbirdAdvScheduleData:
        """Read the schedule. Runs while holding the API lock."""
        controller = self._api.controller

        timeline = None
        try:
            schedule = await controller.get_schedule()
        except RainbirdDeviceNackError as err:
            _LOGGER.debug("Device does not support schedule retrieval: %s", err)
        else:
            # Never Schedule.timeline: it passes datetime.now().tzinfo, which is
            # None for a naive now(), yielding a naive timeline that raises when
            # compared against an aware instant.
            timeline = schedule.timeline_tz(dt_util.DEFAULT_TIME_ZONE)

        global_disable = False
        try:
            mask = await controller.get_weather_adjustment_mask()
        except RainbirdApiException as err:
            _LOGGER.debug("Could not fetch weather adjustment mask: %s", err)
        else:
            global_disable = mask.global_disable

        return RainbirdAdvScheduleData(
            timeline=timeline,
            global_disable=global_disable,
        )
