"""Calendar platform: upcoming scheduled waterings."""

from __future__ import annotations

import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyrainbird.timeline import ProgramEvent, ProgramTimeline

from .entity import RainbirdAdvScheduleEntity
from .models import RainbirdAdvConfigEntry, RainbirdAdvData

PARALLEL_UPDATES = 0

# The recurrence is unbounded, so every timeline query MUST be given an end.
# start_after() would iterate forever and eventually raise past year 9999.
NEXT_EVENT_LOOKAHEAD = datetime.timedelta(days=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calendar."""
    async_add_entities([RainbirdCalendar(entry.runtime_data)])


def _to_calendar_event(event: ProgramEvent) -> CalendarEvent:
    """Convert a pyrainbird program event to a Home Assistant calendar event."""
    return CalendarEvent(
        summary=event.program_id.name,
        start=event.start,
        end=event.end,
        rrule=event.rrule_str,
    )


class RainbirdCalendar(RainbirdAdvScheduleEntity, CalendarEntity):
    """Upcoming scheduled waterings, from the controller's programs."""

    _attr_translation_key = "schedule"

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the calendar."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_calendar"

    @property
    def _timeline(self) -> ProgramTimeline | None:
        if not (schedule := self.coordinator.data):
            return None
        return schedule.timeline

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next (or currently active) scheduled watering."""
        if (timeline := self._timeline) is None:
            return None

        now = dt_util.now()
        # A bounded window: the recurrence never ends, so an open-ended query
        # would loop until it overflowed the datetime range.
        window = timeline.overlapping(now, now + NEXT_EVENT_LOOKAHEAD)
        upcoming = sorted(window, key=lambda event: event.start)
        if not upcoming:
            return None
        return _to_calendar_event(upcoming[0])

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> list[CalendarEvent]:
        """Return events in the window Home Assistant asks for."""
        if (timeline := self._timeline) is None:
            return []
        return [
            _to_calendar_event(event)
            for event in timeline.overlapping(start_date, end_date)
        ]
