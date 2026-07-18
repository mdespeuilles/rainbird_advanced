"""Describe a program's schedule: when it runs, which zones, and for how long.

All of this comes straight from the stored schedule (get_schedule); nothing here
is inferred.
"""

from __future__ import annotations

import datetime

from pyrainbird.const import DayOfWeek, ProgramFrequency
from pyrainbird.data import Program
from pyrainbird.timeline import ProgramTimeline

# Monday-first ordering for display; DayOfWeek itself is Sunday=0.
_DAY_ORDER = [
    DayOfWeek.MONDAY,
    DayOfWeek.TUESDAY,
    DayOfWeek.WEDNESDAY,
    DayOfWeek.THURSDAY,
    DayOfWeek.FRIDAY,
    DayOfWeek.SATURDAY,
    DayOfWeek.SUNDAY,
]
_DAY_NAMES = {
    DayOfWeek.MONDAY: "Monday",
    DayOfWeek.TUESDAY: "Tuesday",
    DayOfWeek.WEDNESDAY: "Wednesday",
    DayOfWeek.THURSDAY: "Thursday",
    DayOfWeek.FRIDAY: "Friday",
    DayOfWeek.SATURDAY: "Saturday",
    DayOfWeek.SUNDAY: "Sunday",
}

# How far ahead to look for the next run. The recurrence is unbounded, so the
# query must be given an end.
NEXT_RUN_LOOKAHEAD = datetime.timedelta(days=60)


def frequency_text(program: Program) -> str:
    """Return a human description of how often the program runs."""
    if program.frequency == ProgramFrequency.CUSTOM:
        days = [d for d in _DAY_ORDER if d in program.days_of_week]
        if not days:
            return "No days selected"
        if len(days) == 7:
            return "Every day"
        return ", ".join(_DAY_NAMES[d] for d in days)
    if program.frequency == ProgramFrequency.CYCLIC:
        period = program.period or 0
        if period <= 1:
            return "Every day"
        return f"Every {period} days"
    if program.frequency == ProgramFrequency.ODD:
        return "Odd days of the month"
    if program.frequency == ProgramFrequency.EVEN:
        return "Even days of the month"
    return str(program.frequency)


def start_times(program: Program) -> list[str]:
    """Return the program's start times as HH:MM strings, in order."""
    return [t.strftime("%H:%M") for t in program.starts]


def zone_steps(program: Program) -> list[dict[str, int]]:
    """Return the zones the program waters, in order, with per-zone minutes."""
    return [
        {
            "zone": step.zone,
            "duration_minutes": int(step.duration.total_seconds() // 60),
        }
        for step in program.durations
    ]


def total_minutes(program: Program) -> int:
    """Return the program's total run time in minutes."""
    return sum(int(step.duration.total_seconds() // 60) for step in program.durations)


def next_run(
    timeline: ProgramTimeline | None,
    program_index: int,
    now: datetime.datetime,
) -> datetime.datetime | None:
    """Return when the program next starts, or None.

    The window is bounded because the recurrence never ends. Program-level
    events carry no zone (zone is None), which is how they are told apart from
    per-zone events on the timeline.
    """
    if timeline is None:
        return None
    end = now + NEXT_RUN_LOOKAHEAD
    starts = [
        event.start
        for event in timeline.overlapping(now, end)
        if event.program_id.program == program_index
        and event.program_id.zone is None
        and event.start >= now
    ]
    return min(starts) if starts else None
