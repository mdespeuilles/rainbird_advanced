"""Typed data structures for the Rain Bird Advanced integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from pyrainbird.data import ControllerState, ModelAndVersion, Program
from pyrainbird.timeline import ProgramTimeline

if TYPE_CHECKING:
    from .api import RainbirdApi
    from .coordinator import (
        RainbirdAdvScheduleCoordinator,
        RainbirdAdvStateCoordinator,
    )
    from .run_tracker import ZoneRunTracker


@dataclass(frozen=True, slots=True)
class RainbirdAdvState:
    """Fast-polled controller state."""

    active_zones: frozenset[int]
    """1-based zone numbers currently irrigating."""

    controller_state: ControllerState
    """Raw combined controller state from the device."""

    fetched_at: datetime
    """When this observation was made, on Home Assistant's clock.

    Load-bearing: the run tracker keys off this to tell a fresh observation
    from a stale one replayed after a tolerated failure.
    """


@dataclass(frozen=True, slots=True)
class RainbirdAdvScheduleData:
    """Slow-polled data that rarely changes."""

    timeline: ProgramTimeline | None
    """Program timeline, built in Home Assistant's timezone."""

    programs: list[Program]
    """The raw programs, so per-program detail sensors can describe them."""


@dataclass(slots=True)
class ZoneRun:
    """A completed irrigation run for a single zone."""

    started_at: datetime
    ended_at: datetime
    duration_s: int
    volume_l: float | None = None
    unreliable: bool = False
    """True when the measured duration should not be trusted.

    Set when the run was already in progress the first time we looked and
    nothing was persisted to say when it began, or when a poll gap was long
    enough to hide a stop and a restart. Note the error can go either way: an
    unobserved start makes the duration too short, an unobserved stop/restart
    makes it too long. Hence "unreliable" rather than "truncated".
    """

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable copy.

        Callers may run this in an executor thread, so it must not touch hass
        and must not share mutable state with the tracker.
        """
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_s": self.duration_s,
            "volume_l": self.volume_l,
            "unreliable": self.unreliable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ZoneRun:
        """Restore from persisted storage."""
        return cls(
            started_at=datetime.fromisoformat(str(data["started_at"])),
            ended_at=datetime.fromisoformat(str(data["ended_at"])),
            duration_s=int(data["duration_s"]),  # type: ignore[arg-type]
            volume_l=(
                float(data["volume_l"]) if data.get("volume_l") is not None else None
            ),
            unreliable=bool(data.get("unreliable", False)),
        )


@dataclass
class RainbirdAdvData:
    """Runtime data stored on the config entry."""

    api: RainbirdApi
    model_info: ModelAndVersion
    mac_address: str
    host: str
    zones: list[int]
    coordinator: RainbirdAdvStateCoordinator
    schedule_coordinator: RainbirdAdvScheduleCoordinator
    tracker: ZoneRunTracker
    zone_durations: dict[int, int]
    """Per-zone run duration in minutes, the setpoint a zone switch uses.

    A local setpoint, not read from the device: the duration number entity
    writes here and the switch reads it, so turning a switch on runs the zone
    for the duration currently shown on its number.
    """


type RainbirdAdvConfigEntry = ConfigEntry[RainbirdAdvData]
