"""Base entity for Rain Bird Advanced."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyrainbird.exceptions import RainbirdApiException

from .const import MANUFACTURER
from .coordinator import RainbirdAdvScheduleCoordinator, RainbirdAdvStateCoordinator
from .models import RainbirdAdvData


def build_device_info(data: RainbirdAdvData) -> DeviceInfo:
    """Return device info for the controller.

    Matching on the MAC connection attaches these entities to the same device
    card the official integration creates, so during a migration both appear on
    one card rather than two. The registry supports several config entries per
    device, so this is a supported arrangement rather than a conflict, and once
    the official integration is removed this integration owns the device.

    Only connections is set: identifiers would be a second key to keep in sync
    for no extra benefit, since the connection already drives the match.
    """
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, data.mac_address)},
        manufacturer=MANUFACTURER,
        model=data.model_info.model_name,
        sw_version=f"{data.model_info.major}.{data.model_info.minor}",
        configuration_url=f"http://{data.host}",
    )


class RainbirdControlMixin:
    """Shared behavior for entities that command the controller."""

    _data: RainbirdAdvData

    async def _async_control(self, action: str, coro) -> None:  # noqa: ANN001
        """Run a control coroutine, then refresh so state sensors catch up.

        Device errors are surfaced to the user rather than swallowed, and the
        refresh is debounced so a burst of control actions does not hammer the
        single-connection device.
        """
        try:
            await coro
        except RainbirdApiException as err:
            raise HomeAssistantError(f"Rain Bird {action} failed: {err}") from err
        await self._data.coordinator.async_request_refresh()


class RainbirdAdvEntity(CoordinatorEntity[RainbirdAdvStateCoordinator]):
    """Base entity backed by the fast state coordinator."""

    _attr_has_entity_name = True

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the entity."""
        super().__init__(data.coordinator)
        self._data = data
        self._attr_device_info = build_device_info(data)


class RainbirdAdvScheduleEntity(CoordinatorEntity[RainbirdAdvScheduleCoordinator]):
    """Base entity backed by the slow schedule coordinator."""

    _attr_has_entity_name = True

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the entity."""
        super().__init__(data.schedule_coordinator)
        self._data = data
        self._attr_device_info = build_device_info(data)
