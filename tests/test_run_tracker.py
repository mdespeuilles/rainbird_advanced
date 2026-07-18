"""Tests for the zone run tracker.

This is where the correctness of the history sensors lives: every duration the
integration reports comes from the transition logic exercised here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.rainbird_advanced.const import DOMAIN, STORAGE_VERSION
from custom_components.rainbird_advanced.run_tracker import ZoneRunTracker

SCAN = timedelta(seconds=30)
T0 = datetime(2026, 7, 17, 6, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return a real Store backed by the test storage fixture."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.test_entry")


def make_tracker(store: Store, rates: dict[int, float] | None = None) -> ZoneRunTracker:
    """Return a tracker under test."""
    return ZoneRunTracker(store, SCAN, rates or {1: 10.0})


async def test_start_then_stop_records_run(store: Store) -> None:
    """A zone that starts and stops yields a run with a measured duration."""
    tracker = make_tracker(store)

    # Prime with an idle observation: a zone seen active on the very first poll
    # could have started at any earlier time, which is a different case.
    tracker.observe(set(), T0 - timedelta(seconds=30))

    assert tracker.observe({1}, T0) is True
    assert tracker.last_runs == {}

    # Keep to the real 30s cadence: a longer stride would trip gap detection.
    for offset in (30, 60, 90):
        tracker.observe({1}, T0 + timedelta(seconds=offset))
    assert tracker.observe(set(), T0 + timedelta(seconds=120)) is True

    run = tracker.last_runs[1]
    assert run.duration_s == 120
    assert run.started_at == T0
    assert run.unreliable is False
    # 120s at 10 L/min
    assert run.volume_l == 20.0
    assert tracker.totals[1] == 20.0


async def test_unchanged_poll_is_not_a_transition(store: Store) -> None:
    """Repeated identical observations must not close or open runs."""
    tracker = make_tracker(store)
    tracker.observe({1}, T0)

    assert tracker.observe({1}, T0 + timedelta(seconds=30)) is False
    assert tracker.observe({1}, T0 + timedelta(seconds=60)) is False
    assert tracker.last_runs == {}


async def test_run_shorter_than_poll_interval_is_invisible(store: Store) -> None:
    """A run entirely between two polls cannot be seen.

    Asserted deliberately: this is a known limitation of polling a device with
    no event log, and it is documented in the README. If this test ever starts
    failing, the tracker has begun inventing data.
    """
    tracker = make_tracker(store)

    tracker.observe(set(), T0)
    # Zone 1 ran for 10s somewhere in here, entirely unobserved.
    tracker.observe(set(), T0 + timedelta(seconds=30))

    assert tracker.last_runs == {}
    assert tracker.totals == {}


async def test_no_flow_rate_yields_no_volume(store: Store) -> None:
    """A zone without a configured flow rate reports duration but no volume."""
    tracker = make_tracker(store, rates={})

    tracker.observe({2}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=60))

    run = tracker.last_runs[2]
    assert run.duration_s == 60
    assert run.volume_l is None
    assert 2 not in tracker.totals


async def test_totals_accumulate_across_runs(store: Store) -> None:
    """The cumulative volume sums successive runs."""
    tracker = make_tracker(store)

    tracker.observe({1}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=60))
    tracker.observe({1}, T0 + timedelta(seconds=120))
    tracker.observe(set(), T0 + timedelta(seconds=180))

    assert tracker.totals[1] == 20.0


async def test_active_on_first_observation_is_unreliable(store: Store) -> None:
    """A zone already watering at startup has an unknowable start time."""
    tracker = make_tracker(store)

    tracker.observe({1}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=60))

    run = tracker.last_runs[1]
    assert run.unreliable is True
    assert run.duration_s == 60


async def test_poll_gap_marks_run_unreliable(store: Store) -> None:
    """A gap long enough to hide a stop/restart taints the run."""
    tracker = make_tracker(store)

    tracker.observe(set(), T0)
    tracker.observe({1}, T0 + timedelta(seconds=30))
    # Nothing for 5 minutes: the zone may have stopped and restarted unseen.
    tracker.observe({1}, T0 + timedelta(seconds=330))
    tracker.observe(set(), T0 + timedelta(seconds=360))

    assert tracker.last_runs[1].unreliable is True


async def test_implausible_run_is_discarded(store: Store) -> None:
    """A multi-day 'run' is stale state, not irrigation."""
    tracker = make_tracker(store)

    tracker.observe(set(), T0)
    tracker.observe({1}, T0 + timedelta(seconds=30))
    tracker.observe(set(), T0 + timedelta(days=2))

    assert 1 not in tracker.last_runs


async def test_sequential_program_one_stop_one_start(store: Store) -> None:
    """Zone 1 stopping as zone 2 starts is two transitions in one poll."""
    tracker = make_tracker(store, rates={1: 10.0, 2: 20.0})

    tracker.observe(set(), T0 - timedelta(seconds=30))
    tracker.observe({1}, T0)
    tracker.observe({2}, T0 + timedelta(seconds=60))

    assert tracker.last_runs[1].duration_s == 60
    assert 2 not in tracker.last_runs
    assert tracker.active_since(2) == T0 + timedelta(seconds=60)


async def test_restart_mid_run_keeps_true_start(store: Store) -> None:
    """A restart while watering must not truncate the run.

    The in-flight start time is persisted, so the reloaded tracker can report
    the real duration instead of only the part it witnessed.
    """
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    await tracker.async_save_now()

    reloaded = make_tracker(store)
    await reloaded.async_load()

    # Still watering 3 minutes later, seen by the fresh tracker.
    reloaded.observe({1}, T0 + timedelta(seconds=180))
    reloaded.observe(set(), T0 + timedelta(seconds=240))

    run = reloaded.last_runs[1]
    assert run.started_at == T0
    assert run.duration_s == 240
    assert run.unreliable is False


async def test_restart_after_run_ended_discards_it(store: Store) -> None:
    """If the zone stopped while we were down, we cannot date the end."""
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    await tracker.async_save_now()

    reloaded = make_tracker(store)
    await reloaded.async_load()
    reloaded.observe(set(), T0 + timedelta(seconds=180))

    # Better no data than a fabricated end time.
    assert 1 not in reloaded.last_runs
    assert reloaded.active_since(1) is None


async def test_restart_after_long_outage_restarts_timing(store: Store) -> None:
    """A restored start beyond the plausible limit is not believed."""
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    await tracker.async_save_now()

    reloaded = make_tracker(store)
    await reloaded.async_load()

    # Back 2 days later with zone 1 watering: a different run.
    later = T0 + timedelta(days=2)
    reloaded.observe({1}, later)
    reloaded.observe(set(), later + timedelta(seconds=60))

    run = reloaded.last_runs[1]
    assert run.duration_s == 60
    assert run.unreliable is True


async def test_history_survives_reload(store: Store) -> None:
    """Completed runs and totals round-trip through storage."""
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=120))
    await tracker.async_save_now()

    reloaded = make_tracker(store)
    await reloaded.async_load()

    assert reloaded.last_runs[1].duration_s == 120
    assert reloaded.last_runs[1].started_at == T0
    assert reloaded.totals[1] == 20.0


async def test_snapshot_uses_string_zone_keys(store: Store) -> None:
    """JSON object keys are strings; ints would not survive a round-trip."""
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=60))

    snapshot = tracker._snapshot()
    assert all(isinstance(k, str) for k in snapshot["runs"])
    assert all(isinstance(k, str) for k in snapshot["totals"])


async def test_snapshot_does_not_alias_tracker_state(store: Store) -> None:
    """The saved snapshot must not change under a later observation.

    Home Assistant serializes delayed saves in an executor thread, so a
    snapshot that referenced live tracker state could be written torn.
    """
    tracker = make_tracker(store)
    tracker.observe({1}, T0)
    tracker.observe(set(), T0 + timedelta(seconds=60))

    snapshot = tracker._snapshot()

    tracker.observe({1}, T0 + timedelta(seconds=120))
    tracker.observe(set(), T0 + timedelta(seconds=300))

    assert snapshot["runs"]["1"]["duration_s"] == 60
    assert snapshot["totals"]["1"] == 10.0


async def test_load_tolerates_corrupt_entries(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Unreadable persisted data is dropped, not fatal."""
    key = f"{DOMAIN}.corrupt"
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "key": key,
        "data": {
            "runs": {"1": {"started_at": "not-a-date"}},
            "totals": {"2": "abc"},
            "active_runs": {"3": {}},
        },
    }
    tracker = ZoneRunTracker(Store(hass, STORAGE_VERSION, key), SCAN, {})
    await tracker.async_load()

    assert tracker.last_runs == {}
    assert tracker.totals == {}
    assert tracker.active_since(3) is None
