"""Infer which program is running.

The controller reports which zones are open, never which program opened them.
The best available answer is to cross-reference the active zones against the
stored schedule, which is a deduction rather than a reading -- hence the
is_inferred attribute on the sensor.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pyrainbird.timeline import ProgramTimeline

from .const import (
    INFERENCE_BASIS_NO_SCHEDULE,
    INFERENCE_BASIS_TIMELINE,
    PROGRAM_MANUAL,
)

_LOGGER = logging.getLogger(__name__)


def program_name(program: int) -> str:
    """Return the display name for a 0-based program index.

    Built here rather than using ProgramId.name, which renders
    "PGM A: Zone 3" when the event carries a zone.
    """
    return f"PGM {chr(ord('A') + program)}"


def infer_active_program(
    timeline: ProgramTimeline | None,
    active_zones: frozenset[int],
    now: datetime,
) -> tuple[str | None, str | None]:
    """Return (program name, inference basis) for the current watering.

    Returns (None, None) when nothing is irrigating.

    `now` must be timezone-aware and in the same zone the timeline was built
    with, otherwise the underlying comparison raises on naive/aware mixing.
    """
    if not active_zones:
        return None, None

    if timeline is None:
        # Something is watering but we have no schedule to attribute it to.
        return PROGRAM_MANUAL, INFERENCE_BASIS_NO_SCHEDULE

    try:
        for event in timeline.at_instant(now):
            program_id = event.program_id
            if program_id.zone is None or program_id.zone in active_zones:
                return program_name(program_id.program), INFERENCE_BASIS_TIMELINE
    except (TypeError, ValueError) as err:
        _LOGGER.debug("Could not evaluate program timeline: %s", err)
        return PROGRAM_MANUAL, INFERENCE_BASIS_NO_SCHEDULE

    # Watering outside every scheduled window: someone started it by hand.
    return PROGRAM_MANUAL, INFERENCE_BASIS_TIMELINE
