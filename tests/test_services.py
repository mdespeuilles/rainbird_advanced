"""Tests for the raw_command service."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from pyrainbird.async_client import AsyncRainbirdController
from pyrainbird.exceptions import RainbirdApiException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_advanced.const import DOMAIN, SERVICE_RAW_COMMAND

from .test_coordinator import setup_integration


def test_pyrainbird_private_api_is_intact() -> None:
    """Guard the private pyrainbird API raw_command depends on.

    _process_command is the only route to name-based SIP commands with encoding
    and response validation, so the integration uses it despite the underscore.
    The manifest pins pyrainbird exactly; this fails loudly in CI on a version
    bump rather than at 5am in someone's garden.
    """
    assert hasattr(AsyncRainbirdController, "_process_command")
    params = inspect.signature(AsyncRainbirdController._process_command).parameters
    assert "command" in params
    assert "funct" in params


async def test_raw_command_sip(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A SIP command returns its decoded response."""
    await setup_integration(hass, config_entry)
    mock_create_controller._process_command.return_value = {"model": 5, "major": 1}

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {
            "config_entry_id": config_entry.entry_id,
            "command": "ModelAndVersionRequest",
        },
        blocking=True,
        return_response=True,
    )

    assert response == {"result": {"model": 5, "major": 1}}


async def test_raw_command_sip_passes_params(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Positional parameters reach the encoder."""
    await setup_integration(hass, config_entry)
    mock_create_controller._process_command.return_value = {"ok": 1}

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {
            "config_entry_id": config_entry.entry_id,
            "command": "CurrentStationsActiveRequest",
            "params": [0],
        },
        blocking=True,
        return_response=True,
    )

    args = mock_create_controller._process_command.call_args.args
    assert args[1] == "CurrentStationsActiveRequest"
    assert args[2] == 0


async def test_raw_command_rpc(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """An RPC command reaches the JSON-RPC layer."""
    await setup_integration(hass, config_entry)
    mock_create_controller.test_rpc_support.return_value = {"globalDisable": False}

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {
            "config_entry_id": config_entry.entry_id,
            "command": "getSettings",
            "mode": "rpc",
        },
        blocking=True,
        return_response=True,
    )

    assert response == {"result": {"globalDisable": False}}
    mock_create_controller.test_rpc_support.assert_awaited_once_with("getSettings")


async def test_raw_command_rejects_unknown_sip_command(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A typo must not be encoded and sent to the controller."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="Unknown SIP command"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {
                "config_entry_id": config_entry.entry_id,
                "command": "NotARealRequest",
            },
            blocking=True,
            return_response=True,
        )

    mock_create_controller._process_command.assert_not_called()


async def test_raw_command_rejects_unknown_entry(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """An unknown controller id is a user error, not a crash."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {"config_entry_id": "does-not-exist", "command": "ModelAndVersionRequest"},
            blocking=True,
            return_response=True,
        )


async def test_raw_command_surfaces_device_errors(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A device error is reported rather than swallowed."""
    await setup_integration(hass, config_entry)
    mock_create_controller._process_command.side_effect = RainbirdApiException("nope")

    with pytest.raises(HomeAssistantError, match="Rain Bird command failed"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {
                "config_entry_id": config_entry.entry_id,
                "command": "ModelAndVersionRequest",
            },
            blocking=True,
            return_response=True,
        )


async def test_raw_command_is_admin_only(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
    hass_read_only_user,
) -> None:
    """Non-admins must not reach a primitive that can open valves."""
    await setup_integration(hass, config_entry)

    from homeassistant.core import Context

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {
                "config_entry_id": config_entry.entry_id,
                "command": "ModelAndVersionRequest",
            },
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_read_only_user.id),
        )


async def test_raw_command_takes_the_device_lock(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The service must not bypass serialization and collide with a poll."""
    await setup_integration(hass, config_entry)
    api = config_entry.runtime_data.api

    held = []

    async def _check_lock(*args, **kwargs):
        held.append(api._lock.locked())
        return {"ok": 1}

    mock_create_controller._process_command.side_effect = _check_lock

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {"config_entry_id": config_entry.entry_id, "command": "ModelAndVersionRequest"},
        blocking=True,
        return_response=True,
    )

    assert held == [True]


async def test_acting_command_is_refused_by_default(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A command that opens a valve must not fire on a typo."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="acts on the controller"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {
                "config_entry_id": config_entry.entry_id,
                "command": "ManuallyRunStationRequest",
                "params": [3, 5],
            },
            blocking=True,
            return_response=True,
        )

    # The point of the guard: nothing reached the device.
    mock_create_controller._process_command.assert_not_called()


async def test_acting_command_runs_with_allow_unsafe(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The guard is a speed bump, not a wall: opting in still works."""
    await setup_integration(hass, config_entry)
    mock_create_controller._process_command.return_value = {"echo": 1}

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {
            "config_entry_id": config_entry.entry_id,
            "command": "ManuallyRunStationRequest",
            "params": [3, 5],
            "allow_unsafe": True,
        },
        blocking=True,
        return_response=True,
    )

    assert response == {"result": {"echo": 1}}
    assert mock_create_controller._process_command.call_args.args[1] == (
        "ManuallyRunStationRequest"
    )


async def test_read_command_needs_no_opt_in(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Exploring with read commands stays frictionless."""
    await setup_integration(hass, config_entry)
    mock_create_controller._process_command.return_value = {"model": 5}

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RAW_COMMAND,
        {"config_entry_id": config_entry.entry_id, "command": "ModelAndVersionRequest"},
        blocking=True,
        return_response=True,
    )

    mock_create_controller._process_command.assert_called_once()


async def test_every_acting_sip_command_is_guarded(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """No command that acts on the controller may slip past the guard.

    The classification is derived from pyrainbird's own command table (an
    acting command replies with a bare ACK) rather than a hand-written list, so
    this asserts the derivation against the commands we know are dangerous. If
    pyrainbird adds one, it is guarded automatically.
    """
    await setup_integration(hass, config_entry)

    dangerous = [
        "ManuallyRunProgramRequest",
        "ManuallyRunStationRequest",
        "StackManuallyRunStationRequest",
        "TestStationsRequest",
        "StopIrrigationRequest",
        "AdvanceStationRequest",
        "RainDelaySetRequest",
        "SetCurrentTimeRequest",
        "SetCurrentDateRequest",
    ]

    for command in dangerous:
        with pytest.raises(ServiceValidationError, match="acts on the controller"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RAW_COMMAND,
                {"config_entry_id": config_entry.entry_id, "command": command},
                blocking=True,
                return_response=True,
            )

    mock_create_controller._process_command.assert_not_called()


async def test_non_getter_rpc_is_guarded(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """An RPC method that is not a getter needs the same opt-in."""
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError, match="acts on the controller"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RAW_COMMAND,
            {
                "config_entry_id": config_entry.entry_id,
                "command": "setWifiParams",
                "mode": "rpc",
            },
            blocking=True,
            return_response=True,
        )

    mock_create_controller.test_rpc_support.assert_not_called()
