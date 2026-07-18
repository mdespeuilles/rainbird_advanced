"""Tests for the Rain Bird Advanced coordinators."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pyrainbird.data import States
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdDeviceBusyException,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.rainbird_advanced.const import FAILURE_TOLERANCE


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the integration under test."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_succeeds(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The integration loads and exposes entities."""
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.esp_tm2_advanced_active_zone") is not None


async def test_setup_retries_when_device_busy(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A busy device is healthy but occupied, so setup should retry."""
    mock_create_controller.get_model_and_version.side_effect = (
        RainbirdDeviceBusyException("busy")
    )
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_triggers_reauth_on_bad_password(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A rejected password asks the user to re-authenticate."""
    mock_create_controller.get_model_and_version.side_effect = RainbirdAuthException()
    await setup_integration(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_busy_device_does_not_flap_entities(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Losing a poll race with another client must not blank the sensors.

    This is the whole point of coexisting with the official integration: a 503
    means someone else is talking to the device, not that the device is gone.
    """
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)
    assert hass.states.get("sensor.esp_tm2_advanced_active_zone").state == "3"

    # Retries are exhausted inside the API layer, so every poll now fails.
    mock_create_controller.get_zone_states.side_effect = RainbirdDeviceBusyException(
        "busy"
    )

    for _ in range(FAILURE_TOLERANCE):
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.esp_tm2_advanced_active_zone").state == "3"


async def test_entities_go_unavailable_once_tolerance_is_exceeded(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A device that is genuinely gone must eventually be reported as such."""
    mock_create_controller.get_zone_states.return_value = States("0400")
    await setup_integration(hass, config_entry)

    mock_create_controller.get_zone_states.side_effect = RainbirdApiException("gone")

    for _ in range(FAILURE_TOLERANCE + 1):
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.esp_tm2_advanced_active_zone").state == "unavailable"


async def test_tolerated_failure_does_not_end_runs(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed poll must not look like every zone stopped.

    The coordinator replays the previous observation on a tolerated failure. If
    that replay were mistaken for a fresh reading of an empty zone set, the
    tracker would record a bogus run.
    """
    mock_create_controller.get_zone_states.return_value = States("0100")
    await setup_integration(hass, config_entry)

    tracker = config_entry.runtime_data.tracker
    mock_create_controller.get_zone_states.side_effect = RainbirdDeviceBusyException()

    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert tracker.last_runs == {}
    assert tracker.active_since(1) is not None


async def test_requests_are_serialized(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The device answers one connection at a time, so calls must not overlap."""
    in_flight = 0
    max_in_flight = 0

    async def _track(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return States("0000")

    mock_create_controller.get_zone_states.side_effect = _track
    await setup_integration(hass, config_entry)

    api = config_entry.runtime_data.api
    await asyncio.gather(
        *(api.execute(mock_create_controller.get_zone_states) for _ in range(5))
    )

    assert max_in_flight == 1


async def test_busy_is_retried_then_succeeds(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A transient busy response is absorbed by the backoff.

    pyrainbird's own retry is disabled on the ESP-TM2 (models.yaml sets
    retries: false), so this backoff is the only one in play.
    """
    await setup_integration(hass, config_entry)
    api = config_entry.runtime_data.api

    calls = 0

    async def _busy_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RainbirdDeviceBusyException("busy")
        return "ok"

    assert await api.execute(_busy_once) == "ok"
    assert calls == 2


async def test_schedule_failure_leaves_other_sensors_working(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A device that will not surrender its schedule still reports zones."""
    mock_create_controller.get_schedule.side_effect = RainbirdApiException(
        "no schedule"
    )
    mock_create_controller.get_zone_states.return_value = States("0400")

    await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.esp_tm2_advanced_active_zone").state == "3"


async def test_unload(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The entry unloads cleanly."""
    await setup_integration(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
