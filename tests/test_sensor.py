"""Tests for the Rain Bird Advanced sensors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pyrainbird.data import ControllerState, States, WeatherAdjustmentMask
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .test_coordinator import setup_integration

ACTIVE_ZONE = "sensor.esp_tm2_advanced_active_zone"
ACTIVE_PROGRAM = "sensor.esp_tm2_advanced_active_program"
CONTROLLER_MODE = "sensor.esp_tm2_advanced_controller_mode"
ZONE1_DURATION = "sensor.esp_tm2_advanced_zone_1_last_run_duration"
ZONE1_AT = "sensor.esp_tm2_advanced_zone_1_last_run_at"
ZONE1_VOLUME = "sensor.esp_tm2_advanced_zone_1_estimated_volume"
ZONE1_TOTAL = "sensor.esp_tm2_advanced_zone_1_total_volume"


async def test_active_zone_idle(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """No zone active reports unknown with an empty zone list."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_ZONE)
    assert state.state == "unknown"
    assert state.attributes["active_zones"] == []


async def test_active_zone_reports_watering_zone(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The active zone comes from the zone bitmask."""
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_ZONE)
    assert state.state == "3"
    assert state.attributes["active_zones"] == [3]


async def test_active_zone_reports_multiple_zones(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Several zones can be open at once; the attribute carries them all."""
    # 0x05 -> zones 1 and 3
    mock_create_controller.get_zone_states.return_value = States("0500")
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_ZONE)
    assert state.state == "1"
    assert state.attributes["active_zones"] == [1, 3]


async def test_active_program_idle(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Nothing watering means no program and nothing inferred."""
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_PROGRAM)
    assert state.state == "unknown"
    assert state.attributes["is_inferred"] is False


async def test_active_program_inferred_from_schedule(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Watering inside a scheduled window is attributed to that program.

    Run in a non-UTC zone because the schedule stores a wall-clock time and
    only lines up if the timeline is built in Home Assistant's timezone. Note
    this test alone does not prove the timezone is handled correctly -- see
    test_timeline_is_built_in_home_assistant_timezone for that.
    """
    await hass.config.async_set_time_zone("Europe/Paris")
    # 06:10 in Paris, the middle of PGM A's 06:00-06:30 window.
    freezer.move_to("2026-07-17 04:10:00+00:00")
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_PROGRAM)
    assert state.state == "PGM A"
    assert state.attributes["is_inferred"] is True
    assert state.attributes["inference_basis"] == "timeline"


async def test_active_program_manual_outside_schedule(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Watering outside every window means someone started it by hand."""
    await hass.config.async_set_time_zone("Europe/Paris")
    # 20:00 in Paris, nowhere near the 06:00 window.
    freezer.move_to("2026-07-17 18:00:00+00:00")
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)

    assert hass.states.get(ACTIVE_PROGRAM).state == "manual"


async def test_active_program_without_schedule(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """With no schedule to consult, watering is reported as manual."""
    from pyrainbird.exceptions import RainbirdDeviceNackError

    mock_create_controller.get_schedule.side_effect = RainbirdDeviceNackError()
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)

    state = hass.states.get(ACTIVE_PROGRAM)
    assert state.state == "manual"
    assert state.attributes["inference_basis"] == "no_schedule"


async def test_controller_mode_idle(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """An idle controller reports idle."""
    await setup_integration(hass, config_entry)
    assert hass.states.get(CONTROLLER_MODE).state == "idle"


async def test_controller_mode_watering(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """An open zone means watering."""
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)
    assert hass.states.get(CONTROLLER_MODE).state == "watering"


async def test_controller_mode_rain_delayed(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A rain delay outranks idle."""
    mock_create_controller.get_combined_controller_state.return_value = ControllerState(
        delay_setting=2,
        sensor_state=0,
        irrigation_state=0,
        seasonal_adjust=100,
        remaining_runtime=0,
        active_station=0,
        device_time=__import__("datetime").datetime(2026, 7, 17, 12, 0, 0),
    )
    await setup_integration(hass, config_entry)
    assert hass.states.get(CONTROLLER_MODE).state == "rain_delayed"


async def test_controller_mode_disabled(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A globally disabled controller outranks everything."""
    mock_create_controller.get_weather_adjustment_mask.return_value = (
        WeatherAdjustmentMask(
            num_programs=3, program_opt_out_mask="07", global_disable=True
        )
    )
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)
    assert hass.states.get(CONTROLLER_MODE).state == "disabled"


async def test_controller_mode_exposes_device_clock(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The device clock is surfaced so drift is diagnosable."""
    await setup_integration(hass, config_entry)
    assert hass.states.get(CONTROLLER_MODE).attributes["device_time"] == (
        "2026-07-17T12:00:00"
    )


async def test_zone_history_sensors_start_empty(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """With no run observed yet, history is unknown rather than zero."""
    await setup_integration(hass, config_entry)

    assert hass.states.get(ZONE1_DURATION).state == "unknown"
    assert hass.states.get(ZONE1_AT).state == "unknown"
    assert hass.states.get(ZONE1_VOLUME).state == "unknown"


async def test_zone_history_populates_after_a_run(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A full run drives duration, timestamp and both volume sensors.

    Zone 1 is configured at 10 L/min, so two minutes should read 20 L.
    """
    freezer.move_to("2026-07-17 06:00:00+00:00")
    await setup_integration(hass, config_entry)

    # Zone 1 starts.
    mock_create_controller.get_zone_states.return_value = States("0100")
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ZONE1_DURATION).state == "unknown"

    # Keep to the poll cadence so gap detection stays quiet.
    for _ in range(4):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    # Zone 1 stops, 120s after it started.
    mock_create_controller.get_zone_states.return_value = States("0000")
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ZONE1_DURATION).state == "150"
    assert hass.states.get(ZONE1_DURATION).attributes["unreliable"] is False
    assert hass.states.get(ZONE1_AT).state == "2026-07-17T06:00:31+00:00"
    assert float(hass.states.get(ZONE1_VOLUME).state) == 25.0
    assert float(hass.states.get(ZONE1_TOTAL).state) == 25.0


async def test_zone_without_flow_rate_has_no_volume(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Zone 2 has no configured rate, so its volume stays unknown."""
    freezer.move_to("2026-07-17 06:00:00+00:00")
    await setup_integration(hass, config_entry)

    mock_create_controller.get_zone_states.return_value = States("0200")
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_create_controller.get_zone_states.return_value = States("0000")
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.esp_tm2_advanced_zone_2_last_run_duration").state
        == "30"
    )
    assert hass.states.get("sensor.esp_tm2_advanced_zone_2_estimated_volume").state == (
        "unknown"
    )


async def test_entities_share_the_official_integration_device(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Entities attach to the device by MAC so they merge with core's card."""
    from homeassistant.helpers import device_registry as dr

    await setup_integration(hass, config_entry)

    registry = dr.async_get(hass)
    device = registry.async_get_device(
        connections={(dr.CONNECTION_NETWORK_MAC, "44:2c:05:00:11:22")}
    )
    assert device is not None
    assert device.manufacturer == "Rain Bird"
    assert device.model == "ESP-TM2"


async def test_timeline_is_built_in_home_assistant_timezone(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The program timeline must be anchored to Home Assistant's timezone.

    pyrainbird's Schedule.timeline property passes datetime.now().tzinfo, which
    is None for a naive now(), so it yields naive events anchored to whatever
    timezone the host machine happens to use. That is wrong whenever the host
    and Home Assistant disagree, and it fails silently -- the events still
    compare, just against the wrong wall clock. This asserts we build the
    timeline ourselves with timeline_tz(DEFAULT_TIME_ZONE).
    """

    from homeassistant.util import dt as dt_util

    await hass.config.async_set_time_zone("Europe/Paris")
    await setup_integration(hass, config_entry)

    timeline = config_entry.runtime_data.schedule_coordinator.data.timeline
    assert timeline is not None

    event = next(iter(timeline))
    assert event.start.tzinfo is not None, "timeline events must be timezone-aware"
    assert event.start.utcoffset() == dt_util.now().utcoffset()
