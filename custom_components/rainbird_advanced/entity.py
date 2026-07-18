"""Base entity for Rain Bird Advanced."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MANUFACTURER
from .coordinator import RainbirdAdvScheduleCoordinator, RainbirdAdvStateCoordinator
from .models import RainbirdAdvData


def build_device_info(data: RainbirdAdvData) -> DeviceInfo:
    """Return device info that merges with the official integration's device.

    Matching on the MAC connection attaches these entities to the same device
    card the core rainbird integration creates, instead of showing a second,
    near-identical Rain Bird device. The registry supports several config
    entries per device, so this is a supported arrangement rather than a
    conflict.

    Only connections is set: identifiers would be a second key to keep in sync
    for no extra benefit, since the connection already drives the match.
    """
    return DeviceInfo(
        connections={(CONNECTION_NETWORK_MAC, data.mac_address)},
        manufacturer=MANUFACTURER,
        model=data.model_info.model_name,
        sw_version=f"{data.model_info.major}.{data.model_info.minor}",
    )


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
