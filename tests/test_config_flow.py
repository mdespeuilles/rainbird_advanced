"""Tests for the Rain Bird Advanced config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pyrainbird.data import WifiParams
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdCodingException,
    RainbirdDeviceBusyException,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rainbird_advanced.const import CONF_ZONES, DOMAIN

from .conftest import HOST, MAC_ADDRESS, PASSWORD

USER_INPUT = {CONF_HOST: HOST, CONF_PASSWORD: PASSWORD}


@pytest.fixture(autouse=True)
def _no_setup() -> None:
    """Do not run the full integration setup during flow tests."""
    with patch(
        "custom_components.rainbird_advanced.async_setup_entry", return_value=True
    ):
        yield


async def test_user_flow_success(
    hass: HomeAssistant, mock_create_controller: AsyncMock
) -> None:
    """A valid host and password creates an entry keyed on the MAC."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ESP-TM2 Advanced"
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_MAC] == MAC_ADDRESS
    # Zones are cached so the options form never has to touch the device.
    assert result["data"][CONF_ZONES] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (RainbirdAuthException("nope"), "invalid_auth"),
        (RainbirdDeviceBusyException("busy"), "device_busy"),
        (RainbirdApiException("boom"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        # Separate exception root: a decode failure must not read as a bug.
        (RainbirdCodingException("bad decode"), "cannot_connect"),
        # A raw socket error that never got wrapped.
        (OSError("connection reset"), "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    side_effect: Exception,
    expected: str,
) -> None:
    """Connection problems surface as recoverable form errors."""
    mock_create_controller.get_model_and_version.side_effect = side_effect

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_unexpected_error_surfaces_detail(
    hass: HomeAssistant, mock_create_controller: AsyncMock
) -> None:
    """A truly unexpected error names its cause instead of a dead-end.

    So a field report from the dialog is actionable rather than just
    "unexpected error".
    """
    mock_create_controller.get_model_and_version.side_effect = RuntimeError(
        "something weird"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "unknown"}
    assert "RuntimeError" in result["description_placeholders"]["error_detail"]
    assert "something weird" in result["description_placeholders"]["error_detail"]


async def test_user_flow_recovers_after_error(
    hass: HomeAssistant, mock_create_controller: AsyncMock
) -> None:
    """After a failure the user can retry in the same flow."""
    mock_create_controller.get_model_and_version.side_effect = RainbirdAuthException()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_create_controller.get_model_and_version.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_device_without_mac_is_rejected(
    hass: HomeAssistant, mock_create_controller: AsyncMock
) -> None:
    """No MAC means no stable unique id, so the flow must abort."""
    mock_create_controller.get_wifi_params.return_value = WifiParams(mac_address=None)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_mac"


async def test_duplicate_device_aborts(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """The same controller cannot be added twice."""
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """Reauth stores the new password and reloads."""
    config_entry.add_to_hass(hass)

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_rejects_bad_password(
    hass: HomeAssistant,
    mock_create_controller: AsyncMock,
    config_entry: MockConfigEntry,
) -> None:
    """A still-wrong password keeps the user in the form."""
    config_entry.add_to_hass(hass)
    mock_create_controller.get_model_and_version.side_effect = RainbirdAuthException()

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
