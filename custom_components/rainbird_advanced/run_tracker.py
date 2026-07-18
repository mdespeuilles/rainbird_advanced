"""Track and persist per-zone irrigation run history.

Run history is derived by diffing the set of active zones between polls. The
device offers no usable event log, so a run shorter than the poll interval is
invisible. Durations are always bounded by observations and never extrapolated:
when the data cannot support a trustworthy duration, the run is dropped or
flagged rather than guessed at.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.helpers.storage import Store

from .const import GAP_TOLERANCE_FACTOR, MAX_PLAUSIBLE_RUN_HOURS
from .models import ZoneRun

_LOGGER = logging.getLogger(__name__)

SAVE_DELAY = 15


class ZoneRunTracker:
    """Derive per-zone run history from successive active-zone observations."""

    def __init__(
        self,
        store: Store[dict[str, Any]],
        scan_interval: timedelta,
        flow_rates: dict[int, float],
    ) -> None:
        """Initialize the tracker."""
        self._store = store
        self._max_gap = scan_interval * GAP_TOLERANCE_FACTOR
        self._max_run = timedelta(hours=MAX_PLAUSIBLE_RUN_HOURS)
        self._flow_rates = dict(flow_rates)

        self._last_runs: dict[int, ZoneRun] = {}
        self._totals: dict[int, float] = {}
        self._active_since: dict[int, datetime] = {}
        self._unreliable: set[int] = set()
        """Zones whose in-flight run has a start time we cannot vouch for."""
        self._last_observed: datetime | None = None
        self._primed = False

    async def async_load(self) -> None:
        """Restore history from disk."""
        data = await self._store.async_load()
        if not data:
            return

        for zone_str, run in data.get("runs", {}).items():
            try:
                self._last_runs[int(zone_str)] = ZoneRun.from_dict(run)
            except (ValueError, KeyError, TypeError) as err:
                _LOGGER.warning(
                    "Discarding unreadable run for zone %s: %s", zone_str, err
                )

        for zone_str, total in data.get("totals", {}).items():
            try:
                self._totals[int(zone_str)] = float(total)
            except ValueError, TypeError:
                _LOGGER.warning("Discarding unreadable total for zone %s", zone_str)

        # Runs that were still in flight when we last shut down. Keeping their
        # real start time is what lets a restart mid-run report an accurate
        # duration instead of a truncated one.
        for zone_str, run in data.get("active_runs", {}).items():
            try:
                self._active_since[int(zone_str)] = datetime.fromisoformat(
                    str(run["started_at"])
                )
            except (ValueError, KeyError, TypeError) as err:
                _LOGGER.warning(
                    "Discarding unreadable in-flight run for zone %s: %s", zone_str, err
                )

    def update_flow_rates(self, flow_rates: dict[int, float]) -> None:
        """Apply reconfigured flow rates to future runs."""
        self._flow_rates = dict(flow_rates)

    @property
    def last_runs(self) -> dict[int, ZoneRun]:
        """Return the most recent completed run per zone."""
        return self._last_runs

    @property
    def totals(self) -> dict[int, float]:
        """Return cumulative estimated volume per zone, in liters."""
        return self._totals

    def active_since(self, zone: int) -> datetime | None:
        """Return when the current run of a zone started."""
        return self._active_since.get(zone)

    def observe(self, active_zones: set[int], now: datetime) -> bool:
        """Record an observation. Return True if persisted state changed.

        Runs synchronously inside the coordinator update and performs no I/O.
        """
        dirty = False

        if not self._primed:
            dirty = self._reconcile_restored(active_zones, now)
        elif self._gap_detected(now):
            # A stop and a restart could both have happened unseen, so any run
            # we still think is in flight may not be the one we started timing.
            self._unreliable |= set(self._active_since)

        started = active_zones - set(self._active_since)
        stopped = set(self._active_since) - active_zones

        for zone in started:
            self._active_since[zone] = now
            if not self._primed:
                # Already irrigating the first time we looked, with nothing
                # persisted to tell us when it began.
                self._unreliable.add(zone)
            dirty = True

        for zone in stopped:
            self._record_stop(zone, now)
            dirty = True

        self._last_observed = now
        self._primed = True
        return dirty

    def _reconcile_restored(self, active_zones: set[int], now: datetime) -> bool:
        """Match in-flight runs restored from disk against reality.

        Called once, on the first observation after a restart or reload.
        """
        dirty = False
        for zone, started_at in list(self._active_since.items()):
            if zone not in active_zones:
                # The zone stopped while we were down. We have no idea when, and
                # inventing an end time would silently corrupt the history.
                _LOGGER.debug(
                    "Zone %d stopped while unavailable; discarding its run", zone
                )
                del self._active_since[zone]
                dirty = True
            elif now - started_at > self._max_run:
                # Still watering, but the restored start is too old to believe --
                # most likely a long outage, and this is a different run.
                _LOGGER.debug(
                    "Zone %d run started %s ago, beyond the plausible limit; "
                    "restarting timing",
                    zone,
                    now - started_at,
                )
                self._active_since[zone] = now
                self._unreliable.add(zone)
                dirty = True
        return dirty

    def _gap_detected(self, now: datetime) -> bool:
        """Return True if too much time passed since the last observation."""
        if self._last_observed is None:
            return False
        return (now - self._last_observed) > self._max_gap

    def _record_stop(self, zone: int, now: datetime) -> None:
        """Close out a run that has just ended."""
        started_at = self._active_since.pop(zone)
        unreliable = zone in self._unreliable
        self._unreliable.discard(zone)

        duration = now - started_at
        if duration > self._max_run:
            _LOGGER.debug("Discarding implausible %s run for zone %d", duration, zone)
            return

        duration_s = max(0, int(duration.total_seconds()))
        volume_l = self._volume_for(zone, duration_s)

        self._last_runs[zone] = ZoneRun(
            started_at=started_at,
            ended_at=now,
            duration_s=duration_s,
            volume_l=volume_l,
            unreliable=unreliable,
        )
        if volume_l is not None:
            self._totals[zone] = round(self._totals.get(zone, 0.0) + volume_l, 2)

        _LOGGER.debug(
            "Zone %d ran for %ds (volume=%s, unreliable=%s)",
            zone,
            duration_s,
            volume_l,
            unreliable,
        )

    def _volume_for(self, zone: int, duration_s: int) -> float | None:
        """Estimate volume from the configured flow rate."""
        flow_rate = self._flow_rates.get(zone, 0.0)
        if not flow_rate:
            return None
        return round(duration_s / 60 * flow_rate, 2)

    def async_schedule_save(self) -> None:
        """Persist history, debounced.

        Only called when a run starts or ends, never on an unchanged poll.
        """
        snapshot = self._snapshot()
        self._store.async_delay_save(lambda: snapshot, SAVE_DELAY)

    async def async_save_now(self) -> None:
        """Persist history immediately, e.g. on unload."""
        await self._store.async_save(self._snapshot())

    def _snapshot(self) -> dict[str, Any]:
        """Build a JSON-serializable copy of the history.

        Built here, on the event loop, and closed over by value. Home Assistant
        serializes delayed saves in an executor thread, so the callback must
        never touch hass or read mutable tracker state at save time.

        Zone keys are strings: JSON object keys cannot be integers, and letting
        them round-trip as ints would break lookups after a reload.
        """
        return {
            "runs": {str(zone): run.as_dict() for zone, run in self._last_runs.items()},
            "totals": {str(zone): total for zone, total in self._totals.items()},
            "active_runs": {
                str(zone): {"started_at": started_at.isoformat()}
                for zone, started_at in self._active_since.items()
            },
        }
