"""Fixtures for Rain Bird Advanced tests."""

from __future__ import annotations

import datetime
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD
from pyrainbird.async_client import AsyncRainbirdController
from pyrainbird.const import DayOfWeek, ProgramFrequency
from pyrainbird.data import (
    AvailableStations,
    ControllerInfo,
    ControllerState,
    ModelAndVersion,
    Program,
    Schedule,
    States,
    WeatherAdjustmentMask,
    WifiParams,
    ZoneDuration,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_advanced.const import (
    CONF_SCAN_INTERVAL,
    CONF_ZONE_FLOW_RATES,
    CONF_ZONES,
    DOMAIN,
)

MAC_ADDRESS = "44:2c:05:00:11:22"
HOST = "192.168.1.50"
PASSWORD = "hunter2"
SERIAL_NUMBER = "0x12635436566"

# device_id 0005 -> ESP-TM2: 12 stations, 3 programs, retries disabled.
ESP_TM2_MODEL = 0x0005


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading of this custom component in every test."""
    yield


@pytest.fixture
def model_and_version() -> ModelAndVersion:
    """Return an ESP-TM2 identity."""
    return ModelAndVersion(model=ESP_TM2_MODEL, major=1, minor=3)


@pytest.fixture
def controller_state() -> ControllerState:
    """Return an idle controller state."""
    return ControllerState(
        delay_setting=0,
        sensor_state=0,
        irrigation_state=0,
        seasonal_adjust=100,
        remaining_runtime=0,
        active_station=0,
        device_time=datetime.datetime(2026, 7, 17, 12, 0, 0),
    )


@pytest.fixture
def schedule() -> Schedule:
    """Return a real schedule: PGM A waters zone 3 daily at 06:00 for 30 min.

    Built from real pyrainbird objects rather than mocked, so the timeline and
    recurrence logic the program inference depends on is genuinely exercised.
    """
    program = Program(
        program=0,
        frequency=ProgramFrequency.CUSTOM,
        days_of_week=set(DayOfWeek),
        starts=[datetime.time(6, 0)],
        durations=[ZoneDuration(zone=3, duration=datetime.timedelta(minutes=30))],
        controller_info=ControllerInfo(),
    )
    return Schedule(controller_info=ControllerInfo(), programs=[program])


@pytest.fixture
def mock_controller(
    model_and_version: ModelAndVersion,
    controller_state: ControllerState,
    schedule: Schedule,
) -> AsyncMock:
    """Return a mocked pyrainbird controller.

    spec'd against the real class so a signature change upstream breaks these
    tests rather than silently passing.
    """
    controller = AsyncMock(spec=AsyncRainbirdController)
    controller.get_model_and_version.return_value = model_and_version
    # Zones 1-7 available, matching the fake device's default station set.
    controller.get_available_stations.return_value = AvailableStations("7F000000")
    controller.get_wifi_params.return_value = WifiParams(mac_address=MAC_ADDRESS)
    controller.get_serial_number.return_value = SERIAL_NUMBER
    controller.get_zone_states.return_value = States("0000")
    controller.get_combined_controller_state.return_value = controller_state
    controller.get_weather_adjustment_mask.return_value = WeatherAdjustmentMask(
        num_programs=3, program_opt_out_mask="07", global_disable=False
    )
    controller.get_schedule.return_value = schedule
    return controller


@pytest.fixture
def mock_create_controller(mock_controller: AsyncMock) -> Generator[AsyncMock]:
    """Patch controller creation where it is used, not where it is defined."""
    with patch(
        "custom_components.rainbird_advanced.api.create_controller",
        return_value=mock_controller,
    ):
        yield mock_controller


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a configured config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=MAC_ADDRESS,
        title="ESP-TM2 Advanced",
        data={
            CONF_HOST: HOST,
            CONF_PASSWORD: PASSWORD,
            CONF_MAC: MAC_ADDRESS,
            CONF_ZONES: [1, 2, 3, 4, 5, 6, 7],
        },
        options={
            CONF_SCAN_INTERVAL: 30,
            CONF_ZONE_FLOW_RATES: {"1": 10.0},
        },
    )
