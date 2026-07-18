"""Binary sensor platform: the rain sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import RainbirdAdvEntity
from .models import RainbirdAdvConfigEntry, RainbirdAdvData

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    async_add_entities([RainbirdRainSensor(entry.runtime_data)])


class RainbirdRainSensor(RainbirdAdvEntity, BinarySensorEntity):
    """Whether the rain sensor is currently signalling rain.

    Read from the controller state already fetched each poll, so it adds no
    extra request.
    """

    _attr_translation_key = "rain_sensor"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the sensor."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_rain_sensor"

    @property
    def is_on(self) -> bool | None:
        """Return True if the rain sensor is active."""
        if not (state := self.coordinator.data):
            return None
        return bool(state.controller_state.sensor_state)
