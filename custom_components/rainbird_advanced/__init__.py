"""The Rain Bird Advanced integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from pyrainbird.exceptions import (
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdDeviceBusyException,
)

from .api import async_create_api, async_create_device_session, async_probe_device
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_ZONE_FLOW_RATES,
    CONF_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ZONE_DURATION,
    DOMAIN,
    STORAGE_VERSION,
)
from .coordinator import RainbirdAdvScheduleCoordinator, RainbirdAdvStateCoordinator
from .models import RainbirdAdvConfigEntry, RainbirdAdvData
from .run_tracker import ZoneRunTracker
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services, which are global rather than per config entry."""
    async_setup_services(hass)
    return True


def _parse_flow_rates(entry: RainbirdAdvConfigEntry) -> dict[int, float]:
    """Return configured flow rates keyed by zone.

    Options round-trip through JSON, so the stored keys are strings.
    """
    raw = entry.options.get(CONF_ZONE_FLOW_RATES, {})
    rates: dict[int, float] = {}
    for zone_str, rate in raw.items():
        try:
            rates[int(zone_str)] = float(rate)
        except ValueError, TypeError:
            _LOGGER.warning("Ignoring invalid flow rate for zone %s", zone_str)
    return rates


async def async_setup_entry(hass: HomeAssistant, entry: RainbirdAdvConfigEntry) -> bool:
    """Set up Rain Bird Advanced from a config entry."""
    session = async_create_device_session(hass)

    try:
        api = await async_create_api(
            session, entry.data[CONF_HOST], entry.data[CONF_PASSWORD]
        )
        probe = await async_probe_device(api)
    except RainbirdAuthException as err:
        await session.close()
        raise ConfigEntryAuthFailed("Rain Bird device rejected the password") from err
    except RainbirdDeviceBusyException as err:
        await session.close()
        # Healthy device, just talking to someone else. Worth retrying.
        raise ConfigEntryNotReady(
            "Rain Bird device is busy; another client may be connected"
        ) from err
    except (RainbirdApiException, TimeoutError) as err:
        await session.close()
        raise ConfigEntryNotReady(
            f"Could not connect to Rain Bird device: {err}"
        ) from err

    entry.async_on_unload(session.close)

    scan_interval = timedelta(
        seconds=int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    )

    store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    tracker = ZoneRunTracker(store, scan_interval, _parse_flow_rates(entry))
    await tracker.async_load()

    coordinator = RainbirdAdvStateCoordinator(hass, entry, api, tracker, scan_interval)
    schedule_coordinator = RainbirdAdvScheduleCoordinator(hass, entry, api)

    await coordinator.async_config_entry_first_refresh()

    zones = entry.data.get(CONF_ZONES, probe["zones"])
    entry.runtime_data = RainbirdAdvData(
        api=api,
        model_info=probe["model_info"],
        mac_address=entry.data[CONF_MAC],
        host=entry.data[CONF_HOST],
        zones=zones,
        coordinator=coordinator,
        schedule_coordinator=schedule_coordinator,
        tracker=tracker,
        # Seeded to the default; the duration number entities overwrite each
        # entry from restored state as they are added.
        zone_durations=dict.fromkeys(zones, DEFAULT_ZONE_DURATION),
    )

    async def _async_flush_history() -> None:
        await tracker.async_save_now()

    entry.async_on_unload(_async_flush_history)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Fetch the schedule in the background. It is a ~20-request read, made slower
    # while the official integration also polls the single-connection device, so
    # blocking startup on it can leave setup "starting" for a long time. The
    # calendar and per-program sensors populate once it completes.
    entry.async_create_background_task(
        hass,
        schedule_coordinator.async_refresh(),
        f"{DOMAIN}_initial_schedule",
    )
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: RainbirdAdvConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: RainbirdAdvConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
