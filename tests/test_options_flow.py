"""Tests for the Rain Bird Advanced options flow."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pyrainbird.data import States
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.rainbird_advanced.const import (
    CONF_SCAN_INTERVAL,
    CONF_ZONE_FLOW_RATES,
)

from .test_coordinator import setup_integration


async def test_options_flow_renders_a_field_per_zone(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The form offers one flow rate per available zone, plus the interval."""
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    keys = {str(key) for key in result["data_schema"].schema}
    assert CONF_SCAN_INTERVAL in keys
    for zone in (1, 2, 3, 4, 5, 6, 7):
        assert f"flow_rate_{zone}" in keys


async def test_options_flow_saves_string_keyed_rates(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Flow rates persist under string keys.

    Options are JSON round-tripped, so integer keys would come back as strings
    and silently stop matching. Storing strings up front keeps the two sides
    honest.
    """
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            "flow_rate_1": 12.5,
            "flow_rate_2": 0,
            "flow_rate_3": 8.0,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    rates = config_entry.options[CONF_ZONE_FLOW_RATES]
    assert all(isinstance(key, str) for key in rates)
    assert rates["1"] == 12.5
    assert rates["3"] == 8.0


async def test_changing_options_applies_new_flow_rate(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A reconfigured rate takes effect on the next run, end to end."""
    freezer.move_to("2026-07-17 06:00:00+00:00")
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, "flow_rate_1": 20.0}
    )
    await hass.async_block_till_done()

    # One minute of zone 1 at the new 20 L/min rate.
    mock_create_controller.get_zone_states.return_value = States("0100")
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    mock_create_controller.get_zone_states.return_value = States("0000")
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    volume = hass.states.get("sensor.esp_tm2_advanced_zone_1_estimated_volume")
    assert float(volume.state) == 10.0


async def test_changing_scan_interval_repolls_at_the_new_rate(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Slowing the poll actually slows the requests."""
    await setup_integration(hass, config_entry)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    await hass.async_block_till_done()

    mock_create_controller.get_zone_states.reset_mock()

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_create_controller.get_zone_states.call_count == 0

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_create_controller.get_zone_states.call_count == 1
