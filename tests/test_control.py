"""Tests for the control entities: switch, number, button, and services."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pyrainbird.data import States
from pyrainbird.exceptions import RainbirdApiException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_coordinator import setup_integration

ZONE1_SWITCH = "switch.esp_tm2_advanced_zone_1"
ZONE1_DURATION = "number.esp_tm2_advanced_zone_1_run_duration"
RAIN_DELAY = "number.esp_tm2_advanced_rain_delay"
RAIN_SENSOR = "binary_sensor.esp_tm2_advanced_rain_sensor"
STOP_BUTTON = "button.esp_tm2_advanced_stop_watering"
CALENDAR = "calendar.esp_tm2_advanced_watering_schedule"


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def test_all_control_entities_created(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A full replacement exposes control entities, not just sensors."""
    await setup_integration(hass, config_entry)

    assert hass.states.get(ZONE1_SWITCH) is not None
    assert hass.states.get(ZONE1_DURATION) is not None
    assert hass.states.get(RAIN_DELAY) is not None
    assert hass.states.get(RAIN_SENSOR) is not None
    assert hass.states.get(STOP_BUTTON) is not None
    assert hass.states.get(CALENDAR) is not None
    # One "run program" button per program the model supports (ESP-TM2 has 3).
    assert hass.states.get("button.esp_tm2_advanced_run_program_a") is not None
    assert hass.states.get("button.esp_tm2_advanced_run_program_c") is not None
    assert hass.states.get("button.esp_tm2_advanced_run_program_d") is None


async def test_switch_reflects_active_zone(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The switch is on exactly when the device reports its zone active."""
    mock_create_controller.get_zone_states.return_value = States("0100")
    await setup_integration(hass, config_entry)

    assert hass.states.get(ZONE1_SWITCH).state == "on"
    assert hass.states.get("switch.esp_tm2_advanced_zone_2").state == "off"


async def test_turning_switch_on_uses_zone_duration(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Turning a zone on runs it for the minutes shown on its duration number."""
    await setup_integration(hass, config_entry)

    # Set zone 1's duration to 12 minutes.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": ZONE1_DURATION, "value": 12},
        blocking=True,
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ZONE1_SWITCH}, blocking=True
    )

    mock_create_controller.irrigate_zone.assert_awaited_once_with(1, 12)


async def test_turning_switch_off_stops_irrigation(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Turning a zone off stops irrigation."""
    mock_create_controller.get_zone_states.return_value = States("0100")
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ZONE1_SWITCH}, blocking=True
    )

    mock_create_controller.stop_irrigation.assert_awaited_once()


async def test_zone_duration_default_and_persistence(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The duration number defaults sensibly and is a local setpoint."""
    await setup_integration(hass, config_entry)

    assert float(hass.states.get(ZONE1_DURATION).state) == 6

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": ZONE1_DURATION, "value": 20},
        blocking=True,
    )
    assert float(hass.states.get(ZONE1_DURATION).state) == 20
    # The setpoint is local: no device call.
    mock_create_controller.irrigate_zone.assert_not_called()


async def test_start_irrigation_service_overrides_duration(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The entity service runs a zone for an explicit duration."""
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        "rainbird_advanced",
        "start_irrigation",
        {"entity_id": ZONE1_SWITCH, "duration": 30},
        blocking=True,
    )

    mock_create_controller.irrigate_zone.assert_awaited_once_with(1, 30)


async def test_rain_delay_number_reads_and_writes(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The rain delay number reflects and sets the device value."""
    import datetime

    from pyrainbird.data import ControllerState

    mock_create_controller.get_combined_controller_state.return_value = ControllerState(
        delay_setting=3,
        sensor_state=0,
        irrigation_state=0,
        seasonal_adjust=100,
        remaining_runtime=0,
        active_station=0,
        device_time=datetime.datetime(2026, 7, 17, 12, 0, 0),
    )
    await setup_integration(hass, config_entry)

    assert float(hass.states.get(RAIN_DELAY).state) == 3

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": RAIN_DELAY, "value": 5},
        blocking=True,
    )
    mock_create_controller.set_rain_delay.assert_awaited_once_with(5)


async def test_rain_sensor_binary(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The rain sensor reflects the controller state."""
    import datetime

    from pyrainbird.data import ControllerState

    mock_create_controller.get_combined_controller_state.return_value = ControllerState(
        delay_setting=0,
        sensor_state=1,
        irrigation_state=0,
        seasonal_adjust=100,
        remaining_runtime=0,
        active_station=0,
        device_time=datetime.datetime(2026, 7, 17, 12, 0, 0),
    )
    await setup_integration(hass, config_entry)

    assert hass.states.get(RAIN_SENSOR).state == "on"


async def test_stop_button_stops_all(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The stop button stops all irrigation."""
    await setup_integration(hass, config_entry)
    await _press(hass, STOP_BUTTON)
    mock_create_controller.stop_irrigation.assert_awaited_once()


async def test_advance_button(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The advance button moves to the next zone."""
    await setup_integration(hass, config_entry)
    await _press(hass, "button.esp_tm2_advanced_advance_zone")
    mock_create_controller.advance_zone.assert_awaited_once_with(1)


async def test_run_program_button(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The program buttons run the matching 0-based program."""
    await setup_integration(hass, config_entry)

    await _press(hass, "button.esp_tm2_advanced_run_program_a")
    mock_create_controller.set_program.assert_awaited_once_with(0)

    mock_create_controller.set_program.reset_mock()
    await _press(hass, "button.esp_tm2_advanced_run_program_b")
    mock_create_controller.set_program.assert_awaited_once_with(1)


async def test_control_error_is_surfaced(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A device error during control reaches the user."""
    await setup_integration(hass, config_entry)
    mock_create_controller.stop_irrigation.side_effect = RainbirdApiException("nope")

    with pytest.raises(HomeAssistantError, match="Rain Bird stop failed"):
        await _press(hass, STOP_BUTTON)


async def test_control_triggers_refresh(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer,
) -> None:
    """After a control action the state coordinator refreshes.

    So the active-zone sensor and switches reflect the change without waiting
    for the next scheduled poll. The refresh is debounced (the device takes one
    connection at a time), so it lands shortly after rather than instantly.
    """
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await setup_integration(hass, config_entry)
    before = mock_create_controller.get_zone_states.await_count

    await _press(hass, STOP_BUTTON)
    # Let the debounced refresh fire.
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_create_controller.get_zone_states.await_count > before


async def test_calendar_lists_upcoming_waterings(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The calendar returns bounded events without iterating forever.

    The recurrence is open-ended, so an unbounded query would loop until it
    overflowed the datetime range; async_get_events must stay within its window.
    """
    import datetime

    from homeassistant.util import dt as dt_util

    await hass.config.async_set_time_zone("Europe/Paris")
    await setup_integration(hass, config_entry)

    start = dt_util.now()
    end = start + datetime.timedelta(days=7)
    events = (
        await hass.data["calendar"]
        .get_entity(CALENDAR)
        .async_get_events(hass, start, end)
    )

    # PGM A runs daily, so about seven events across a week.
    assert 6 <= len(events) <= 8
    assert all(event.summary == "PGM A" for event in events)
