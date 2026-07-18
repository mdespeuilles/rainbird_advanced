"""Sensor platform for Rain Bird Advanced."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTIVE_STATION,
    ATTR_ACTIVE_ZONES,
    ATTR_DEVICE_TIME,
    ATTR_INFERENCE_BASIS,
    ATTR_IRRIGATION_STATE,
    ATTR_IS_INFERRED,
    ATTR_STARTED_AT,
    ATTR_UNRELIABLE,
    CONTROLLER_MODE_DISABLED,
    CONTROLLER_MODE_IDLE,
    CONTROLLER_MODE_RAIN_DELAYED,
    CONTROLLER_MODE_WATERING,
    CONTROLLER_MODES,
)
from .entity import RainbirdAdvEntity
from .models import RainbirdAdvConfigEntry, RainbirdAdvData
from .program_infer import infer_active_program

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    data = entry.runtime_data

    entities: list[SensorEntity] = [
        RainbirdActiveZoneSensor(data),
        RainbirdActiveProgramSensor(data),
        RainbirdControllerModeSensor(data),
    ]
    for zone in data.zones:
        entities.extend(
            [
                RainbirdZoneLastRunDurationSensor(data, zone),
                RainbirdZoneLastRunAtSensor(data, zone),
                RainbirdZoneVolumeSensor(data, zone),
                RainbirdZoneTotalVolumeSensor(data, zone),
            ]
        )

    async_add_entities(entities)


class RainbirdActiveZoneSensor(RainbirdAdvEntity, SensorEntity):
    """The zone currently irrigating."""

    _attr_translation_key = "active_zone"

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the sensor."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_active_zone"

    @property
    def native_value(self) -> int | None:
        """Return the lowest active zone, or None when idle."""
        if not self.coordinator.data:
            return None
        active = self.coordinator.data.active_zones
        return min(active) if active else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return every active zone, plus the device's own view for comparison."""
        if not (data := self.coordinator.data):
            return None
        return {
            ATTR_ACTIVE_ZONES: sorted(data.active_zones),
            # Diagnostic cross-check. Not used as the source of truth: it is a
            # single int whose multi-zone meaning is undefined upstream.
            ATTR_ACTIVE_STATION: data.controller_state.active_station,
        }


class RainbirdActiveProgramSensor(RainbirdAdvEntity, SensorEntity):
    """The program believed to be running.

    Inferred, never read: the controller reports open zones but not which
    program opened them.
    """

    _attr_translation_key = "active_program"

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the sensor."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_active_program"

    async def async_added_to_hass(self) -> None:
        """Follow the schedule coordinator as well as the state coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._data.schedule_coordinator.async_add_listener(
                self._handle_coordinator_update
            )
        )

    @property
    def native_value(self) -> str | None:
        """Return the inferred program name."""
        if not (data := self.coordinator.data):
            return None
        schedule = self._data.schedule_coordinator.data
        timeline = schedule.timeline if schedule else None
        # dt_util.now() is aware and local, matching the timezone the timeline
        # was built with. Mixing naive and aware here raises.
        name, _ = infer_active_program(timeline, data.active_zones, dt_util.now())
        return name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Flag that this value is a deduction, and say what backs it."""
        if not (data := self.coordinator.data):
            return None
        schedule = self._data.schedule_coordinator.data
        timeline = schedule.timeline if schedule else None
        name, basis = infer_active_program(timeline, data.active_zones, dt_util.now())
        if name is None:
            return {ATTR_IS_INFERRED: False}
        return {ATTR_IS_INFERRED: True, ATTR_INFERENCE_BASIS: basis}


class RainbirdControllerModeSensor(RainbirdAdvEntity, SensorEntity):
    """What the controller is doing.

    This is a derived software state, NOT the position of the physical dial.
    The local API exposes no dial reading at all, so turning the dial to OFF may
    leave this sensor reporting idle.
    """

    _attr_translation_key = "controller_mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONTROLLER_MODES

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the sensor."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_controller_mode"

    async def async_added_to_hass(self) -> None:
        """Follow the schedule coordinator for the global disable flag."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._data.schedule_coordinator.async_add_listener(
                self._handle_coordinator_update
            )
        )

    @property
    def native_value(self) -> str | None:
        """Return the current mode, first match wins."""
        if not (data := self.coordinator.data):
            return None

        schedule = self._data.schedule_coordinator.data
        if schedule is not None and schedule.global_disable:
            return CONTROLLER_MODE_DISABLED
        if data.controller_state.delay_setting > 0:
            return CONTROLLER_MODE_RAIN_DELAYED
        if data.active_zones:
            return CONTROLLER_MODE_WATERING
        return CONTROLLER_MODE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the raw signals, including the device clock.

        device_time is worth surfacing: Rain Bird clocks drift and are often
        wrong after a power cut, and a visibly skewed value explains a lot.
        """
        if not (data := self.coordinator.data):
            return None
        state = data.controller_state
        return {
            ATTR_IRRIGATION_STATE: state.irrigation_state,
            ATTR_DEVICE_TIME: state.device_time.isoformat(),
        }


class RainbirdZoneEntity(RainbirdAdvEntity):
    """Base for per-zone entities."""

    def __init__(self, data: RainbirdAdvData, zone: int, key: str) -> None:
        """Initialize the entity."""
        super().__init__(data)
        self._zone = zone
        self._attr_unique_id = f"{data.mac_address}_zone_{zone}_{key}"
        self._attr_translation_placeholders = {"zone": str(zone)}

    @property
    def _tracker(self):  # noqa: ANN202
        return self._data.tracker


class RainbirdZoneLastRunDurationSensor(RainbirdZoneEntity, SensorEntity):
    """How long the zone last ran."""

    _attr_translation_key = "zone_last_run_duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(data, zone, "last_run_duration")

    @property
    def native_value(self) -> int | None:
        """Return the last run duration in seconds."""
        run = self._tracker.last_runs.get(self._zone)
        return run.duration_s if run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Say whether the measurement can be trusted."""
        run = self._tracker.last_runs.get(self._zone)
        if run is None:
            return None
        attrs: dict[str, Any] = {ATTR_UNRELIABLE: run.unreliable}
        if started := self._tracker.active_since(self._zone):
            attrs[ATTR_STARTED_AT] = started.isoformat()
        return attrs


class RainbirdZoneLastRunAtSensor(RainbirdZoneEntity, SensorEntity):
    """When the zone last started watering."""

    _attr_translation_key = "zone_last_run_at"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(data, zone, "last_run_at")

    @property
    def native_value(self) -> datetime | None:
        """Return when the last run began.

        The start, not the end: "when did zone 3 last water" means when it
        began, and start + duration reconstructs the whole run.
        """
        run = self._tracker.last_runs.get(self._zone)
        return run.started_at if run else None


class RainbirdZoneVolumeSensor(RainbirdZoneEntity, SensorEntity):
    """Estimated volume of the zone's last run."""

    _attr_translation_key = "zone_estimated_volume"
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(data, zone, "estimated_volume")

    @property
    def native_value(self) -> float | None:
        """Return the estimated volume of the last run, in liters."""
        run = self._tracker.last_runs.get(self._zone)
        return run.volume_l if run else None


class RainbirdZoneTotalVolumeSensor(RainbirdZoneEntity, SensorEntity):
    """Cumulative estimated volume for the zone.

    This is the one that can feed Home Assistant's water dashboard; the
    per-run sensor rises and falls and is not eligible.
    """

    _attr_translation_key = "zone_total_volume"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, data: RainbirdAdvData, zone: int) -> None:
        """Initialize the sensor."""
        super().__init__(data, zone, "total_volume")

    @property
    def native_value(self) -> float | None:
        """Return the cumulative estimated volume, in liters."""
        return self._tracker.totals.get(self._zone)
