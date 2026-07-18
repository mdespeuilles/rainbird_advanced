"""Switch platform for Rain Bird Advanced: one on/off switch per zone."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)

from .const import (
    ATTR_DURATION,
    MAX_ZONE_DURATION,
    MIN_ZONE_DURATION,
    SERVICE_START_IRRIGATION,
)
from .entity import RainbirdAdvEntity, RainbirdControlMixin
from .models import RainbirdAdvConfigEntry, RainbirdAdvData

# The device answers one connection at a time; serialize platform updates too.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the zone switches."""
    data = entry.runtime_data

    # An entity service: it targets a zone switch and takes an explicit
    # duration, unlike turning the switch on (which uses the zone's number).
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_START_IRRIGATION,
        {
            vol.Required(ATTR_DURATION): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_ZONE_DURATION, max=MAX_ZONE_DURATION),
            )
        },
        "async_start_irrigation",
    )

    async_add_entities(RainbirdZoneSwitch(data, zone) for zone in data.zones)


class RainbirdZoneSwitch(RainbirdControlMixin, RainbirdAdvEntity, SwitchEntity):
    """Start or stop watering a single zone.

    Turning on runs the zone for the duration currently set on its duration
    number. Turning off stops all irrigation -- the controller runs one zone at
    a time and offers no per-zone stop, so this mirrors the official behavior.
    """

    _attr_translation_key = "zone"

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the switch."""
        super().__init__(data)
        self._zone = zone
        self._attr_unique_id = f"{data.mac_address}_zone_{zone}_switch"
        self._attr_translation_placeholders = {"zone": str(zone)}

    @property
    def is_on(self) -> bool:
        """Return whether this zone is currently watering."""
        if not (state := self.coordinator.data):
            return False
        return self._zone in state.active_zones

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start this zone for its configured duration."""
        minutes = self._data.zone_durations[self._zone]
        await self._async_control(
            "start", self._data.api.irrigate_zone(self._zone, minutes)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop irrigation."""
        await self._async_control("stop", self._data.api.stop_irrigation())

    async def async_start_irrigation(self, duration: int) -> None:
        """Run this zone for an explicit duration (entity service)."""
        await self._async_control(
            "start", self._data.api.irrigate_zone(self._zone, duration)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
