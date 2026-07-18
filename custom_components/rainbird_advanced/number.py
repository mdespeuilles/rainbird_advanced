"""Number platform: per-zone run duration and the rain delay."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DEFAULT_ZONE_DURATION,
    MAX_RAIN_DELAY,
    MAX_ZONE_DURATION,
    MIN_ZONE_DURATION,
)
from .entity import RainbirdAdvEntity, RainbirdControlMixin
from .models import RainbirdAdvConfigEntry, RainbirdAdvData

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    data = entry.runtime_data
    entities: list[NumberEntity] = [RainbirdRainDelayNumber(data)]
    entities.extend(RainbirdZoneDurationNumber(data, zone) for zone in data.zones)
    async_add_entities(entities)


class RainbirdZoneDurationNumber(RainbirdAdvEntity, RestoreNumber):
    """How long the zone switch runs this zone.

    A local setpoint, not a device value: it persists across restarts and feeds
    the zone switch. No device round-trip is involved in reading or setting it.
    """

    _attr_translation_key = "zone_duration"
    _attr_native_min_value = MIN_ZONE_DURATION
    _attr_native_max_value = MAX_ZONE_DURATION
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the number."""
        super().__init__(data)
        self._zone = zone
        self._attr_unique_id = f"{data.mac_address}_zone_{zone}_duration"
        self._attr_translation_placeholders = {"zone": str(zone)}
        self._attr_native_value = DEFAULT_ZONE_DURATION
        data.zone_durations[zone] = DEFAULT_ZONE_DURATION

    async def async_added_to_hass(self) -> None:
        """Restore the last set duration."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) and (
            last.native_value is not None
        ):
            self._attr_native_value = last.native_value
            self._data.zone_durations[self._zone] = int(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new duration."""
        self._attr_native_value = value
        self._data.zone_durations[self._zone] = int(value)
        self.async_write_ha_state()


class RainbirdRainDelayNumber(RainbirdControlMixin, RainbirdAdvEntity, NumberEntity):
    """The rain delay, in days. Backed by the device."""

    _attr_translation_key = "rain_delay"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_RAIN_DELAY
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the number."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_rain_delay"

    @property
    def native_value(self) -> int | None:
        """Return the current rain delay in days."""
        if not (state := self.coordinator.data):
            return None
        return state.controller_state.delay_setting

    async def async_set_native_value(self, value: float) -> None:
        """Set the rain delay."""
        await self._async_control(
            "set rain delay", self._data.api.set_rain_delay(int(value))
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
