"""Constants for the Rain Bird Advanced integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "rainbird_advanced"
MANUFACTURER: Final = "Rain Bird"

# Config entry data
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_ZONES: Final = "zones"
"""Zones reported by the controller, cached so the options form renders
without a device call."""

# Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ZONE_FLOW_RATES: Final = "zone_flow_rates"

DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600
DEFAULT_FLOW_RATE: Final = 0.0

# Per-zone run duration (minutes) the switch uses when turned on.
DEFAULT_ZONE_DURATION: Final = 6
MIN_ZONE_DURATION: Final = 1
MAX_ZONE_DURATION: Final = 240

# Rain delay bounds, in days.
MAX_RAIN_DELAY: Final = 14

# After a control action, refresh soon so the state sensors catch up without
# waiting for the next scheduled poll. Debounced by the coordinator.
CONTROL_REFRESH_DELAY: Final = 2

SCHEDULE_UPDATE_INTERVAL: Final = timedelta(hours=1)

# Spacing pyrainbird enforces between consecutive requests on the same client.
# This is pacing, not mutual exclusion: the check is a plain elapsed-time
# comparison, so two concurrent tasks would both pass it. RainbirdApi's lock is
# what actually serializes access.
#
# Careful: any value > 0 also makes pyrainbird install a hard 10s per-request
# aiohttp timeout. Raising this raises the lock hold time of get_schedule(),
# which is ~20 sequential requests.
MIN_DELAY: Final = 0.5

TIMEOUT_SECONDS: Final = 20
DEBOUNCER_COOLDOWN: Final = 5

# Backoff between retries after a "device busy" (HTTP 503) response, in seconds.
# pyrainbird has its own JitterRetry, but it is gated on model_info.retries,
# which is false for the ESP-TM2 -- so on that controller this is the only
# backoff there is.
BUSY_BACKOFF: Final = (1.0, 2.0, 4.0)
BUSY_JITTER: Final = 0.5

# Consecutive update failures tolerated before entities go unavailable.
# Collisions with the official integration or the Rain Bird app are expected and
# mean the device is healthy but busy, so a single 503 must not make entities
# flap to unavailable.
FAILURE_TOLERANCE: Final = 3

# A poll gap larger than this multiple of the scan interval means we may have
# missed a stop/start transition, so in-flight runs are flagged as unreliable.
GAP_TOLERANCE_FACTOR: Final = 2

# Runs longer than this are implausible for irrigation and indicate stale state
# restored after a long outage. They are discarded rather than reported.
MAX_PLAUSIBLE_RUN_HOURS: Final = 6

STORAGE_VERSION: Final = 1

# Services
SERVICE_RAW_COMMAND: Final = "raw_command"
SERVICE_START_IRRIGATION: Final = "start_irrigation"
ATTR_DURATION: Final = "duration"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_COMMAND: Final = "command"
ATTR_PARAMS: Final = "params"
ATTR_MODE: Final = "mode"
ATTR_ALLOW_UNSAFE: Final = "allow_unsafe"
MODE_SIP: Final = "sip"
MODE_RPC: Final = "rpc"
RAW_COMMAND_MODES: Final = [MODE_SIP, MODE_RPC]

# A SIP command whose only reply is a bare acknowledgement is one that acts on
# the controller rather than reporting from it -- starting irrigation, setting
# the clock, cancelling a run. Read commands all answer with a data response.
#
# Verified against pyrainbird 6.5.0: this rule catches every acting command
# (ManuallyRun*, Test*, Stop*, Advance*, Set*, RainDelaySet) with no false
# negatives, and needs no hand-maintained list that could drift out of date.
ACK_RESPONSE: Final = "01"

# Zero-argument JSON-RPC methods that only report state are conventionally
# named get*. Anything else is treated as acting until proven otherwise.
RPC_READ_PREFIX: Final = "get"

# Controller mode sensor values. This is NOT the physical dial position, which
# the SIP protocol does not expose. See README.
CONTROLLER_MODE_IDLE: Final = "idle"
CONTROLLER_MODE_WATERING: Final = "watering"
CONTROLLER_MODE_RAIN_DELAYED: Final = "rain_delayed"
CONTROLLER_MODES: Final = [
    CONTROLLER_MODE_IDLE,
    CONTROLLER_MODE_WATERING,
    CONTROLLER_MODE_RAIN_DELAYED,
]

# Active program sensor
PROGRAM_MANUAL: Final = "manual"
INFERENCE_BASIS_TIMELINE: Final = "timeline"
INFERENCE_BASIS_NO_SCHEDULE: Final = "no_schedule"

# Entity attributes
ATTR_ACTIVE_ZONES: Final = "active_zones"
ATTR_ZONE_ID: Final = "zone_id"
ATTR_IS_INFERRED: Final = "is_inferred"
ATTR_INFERENCE_BASIS: Final = "inference_basis"
ATTR_UNRELIABLE: Final = "unreliable"
ATTR_DEVICE_TIME: Final = "device_time"
ATTR_STARTED_AT: Final = "started_at"
ATTR_ACTIVE_STATION: Final = "active_station"
ATTR_IRRIGATION_STATE: Final = "irrigation_state"

# Program-detail sensor attributes
ATTR_CONFIGURED: Final = "configured"
ATTR_FREQUENCY: Final = "frequency"
ATTR_START_TIMES: Final = "start_times"
ATTR_ZONES: Final = "zones"
ATTR_TOTAL_DURATION: Final = "total_duration_minutes"
