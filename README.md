# Rain Bird Advanced

[![hacs][hacs-badge]][hacs]

A Home Assistant custom integration for Rain Bird controllers that **replaces**
the official `rainbird` integration with a more complete one: it keeps zone
control, the rain delay, the rain sensor and the schedule calendar, and adds
what the official one leaves out — a clean active-zone sensor, an inferred
active program, per-zone run history with estimated volumes, program buttons,
and a raw-command tool for exploration.

It talks to the controller over the **local** API only (`http://<ip>/stick`).

Built for a **Rain Bird ESP-TM2 + LNK2 WiFi module**. Other LNK/LNK2 controllers
supported by [pyrainbird][pyrainbird] should work, but are untested.

> **This is a replacement.** Install it, confirm it works, then **remove the
> official Rain Bird integration** — running both leaves two clients competing
> for the controller's single connection and two sets of zone switches. See
> [Migrating from the official integration](#migrating-from-the-official-integration).

---

## What you get

### Monitor

| Entity | What it is |
| --- | --- |
| `sensor.…_active_zone` | The zone currently watering. `active_zones` attribute lists all of them when several are open. |
| `sensor.…_active_program` | The program believed to be running: `PGM A`/`B`/`C`, or `manual`. **Inferred** — see below. |
| `sensor.…_program_A/B/C` | One per program. State is the **next run time**; attributes give the frequency (which days), start times, the zones **in watering order** with per-zone minutes, and the total duration. |
| `sensor.…_controller_mode` | `idle` / `watering` / `rain_delayed` / `disabled`. **Not the physical dial** — see below. |
| `sensor.…_zone_N_last_run_duration` | How long zone N last ran, in seconds. |
| `sensor.…_zone_N_last_run_at` | When zone N last **started** watering. |
| `sensor.…_zone_N_estimated_volume` | Estimated volume of zone N's last run, in liters. |
| `sensor.…_zone_N_total_volume` | Cumulative estimated volume for zone N. This is the one eligible for Home Assistant's water dashboard. |
| `binary_sensor.…_rain_sensor` | Whether the rain sensor is signalling rain. |
| `calendar.…_watering_schedule` | Upcoming scheduled waterings. |

### Control

| Entity | What it does |
| --- | --- |
| `switch.…_zone_N` | Start (for the zone's configured duration) or stop watering zone N. |
| `number.…_zone_N_run_duration` | How many minutes the zone N switch runs. A local setpoint, persisted. |
| `number.…_rain_delay` | The rain delay, in days. |
| `button.…_stop_watering` | Stop all irrigation. |
| `button.…_advance_zone` | Advance to the next zone. |
| `button.…_run_program_A/B/C` | Manually run a full program. |
| `rainbird_advanced.start_irrigation` | Run a targeted zone for an explicit duration (service). |
| `rainbird_advanced.raw_command` | Send an arbitrary command to the controller, for exploration and debugging. Admin only. |

History survives restarts. During a migration the entities attach to the **same
device card** as the official integration, so you see one Rain Bird device, not
two.

---

## What this integration cannot do

Read this section before filing a bug. These are hardware and protocol limits,
not oversights.

### It cannot read the physical dial (Off / Auto / Manual)

The Rain Bird SIP protocol has **no command that reports the dial position**.
It is not in pyrainbird, and it is not in the command set the controller
understands. The dial's state never travels over WiFi.

`sensor.…_controller_mode` is offered instead. It is a **derived software
state**, computed from the irrigation state, the rain delay and the global
disable flag. **If you turn the dial to OFF, this sensor may still report
`idle`.** It answers "what is the controller doing", not "where is the knob".

### It cannot read which program is running

The controller reports which zones are open. It never reports which program
opened them. `sensor.…_active_program` **deduces** the answer by cross-checking
the active zones against the stored schedule, and carries `is_inferred: true`
whenever it reports a program.

It can be wrong. The schedule is refreshed hourly, so editing your schedule and
watching the sensor within the hour will mislead you. A zone opened outside
every scheduled window is reported as `manual`.

### It cannot see runs shorter than the polling interval

Run history is built by polling and comparing what changed. A zone that starts
**and** stops between two polls is invisible: there is no event log on the
device to recover it from. With the 30 s default:

- runs longer than ~60 s are recorded reliably
- runs of 30–60 s are a coin flip
- runs under 30 s are lost

A zone that stops and immediately restarts within one interval also reads as a
single continuous run. Timestamps are accurate to roughly ± the poll interval.

When a duration cannot be trusted — Home Assistant was restarted mid-run with
nothing persisted, or a poll gap could have hidden a stop/restart — the duration
sensor carries `unreliable: true` rather than quietly reporting a wrong number.
Durations are never extrapolated, and implausible runs (over 6 h) are dropped
rather than reported.

### Volumes are estimates, not measurements

There is no flow meter. Volume is `configured flow rate × measured duration`.
Its accuracy is entirely the accuracy of the rate you enter.

Rates are **not** pre-filled from the controller. It does expose `flow_rates`,
but they are per-*program* rather than per-zone and their unit is undocumented
(pyrainbird has no enum for `flow_units`), so seeding from them would be
confidently wrong. Enter them yourself.

### There is no automatic discovery

The LNK2 does not advertise over mDNS, and DHCP matching is not viable: the MAC
prefix on these modules belongs to **AMPAK Technology**, the WiFi chip vendor,
not to Rain Bird — matching on it would hijack unrelated devices. Enter the IP
yourself, as with the official integration.

### Zone names come from the cloud, so there are none here

The local API has no zone names; the official integration reads them from Rain
Bird's cloud service. Zones are `Zone 1`…`Zone N`. Rename them in the Home
Assistant UI — it persists and survives reloads.

---

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Add this repository's URL, category **Integration**
3. Install **Rain Bird Advanced**, restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Rain Bird Advanced**

### Manual

Copy `custom_components/rainbird_advanced/` into your `config/custom_components/`
directory and restart.

## Configuration

You need the **IP address** of the LNK2 module and the **device password** you
set in the Rain Bird app.

Options (⚙ on the integration card):

- **Polling interval** — default 30 s. Runs shorter than this are not recorded.
- **Flow rate per zone** — in L/min. Leave at `0` to leave a zone's volume
  unknown.

Each zone also gets a **run-duration** number entity (how long its switch runs
it) and there is a device-wide **rain delay** number.

---

## Migrating from the official integration

The two are designed to run side by side **during migration only**:

1. Install and configure Rain Bird Advanced (above).
2. Confirm the new entities work — turn a zone on and off, check the sensors.
3. Move any automations, dashboards and scripts to the new entity ids
   (`switch.…`, `number.…`, etc.). The device card is shared, so this is a
   rename, not a re-discovery.
4. **Remove the official Rain Bird integration** (its entry → ⋮ → Delete).

**Why remove it.** The LNK2 answers one connection at a time. Two integrations
polling the same controller collide (the device replies "busy", HTTP 503), and
you would also have two zone switches for every zone. Once the official one is
gone, Rain Bird Advanced is the sole client and collisions essentially
disappear.

While both are installed, the collisions are handled rather than papered over:

- every request is serialized through a single lock, paced, and retried with
  jittered backoff on a busy response — the jitter matters, otherwise a 30 s and
  a 60 s poller phase-align and collide forever
- a busy device means *healthy but occupied*, so entities **hold their last
  known value** rather than flapping to `unavailable`

Worth knowing: pyrainbird has its own retry logic, but it is disabled for the
ESP-TM2 (`retries: false` in its model table), so on that controller the backoff
here is the only one there is.

> The Rain Bird 2.0 app and firmware are not compatible with Home Assistant at
> all — that is upstream, and applied to the official integration too.

---

## The `raw_command` service

> **⚠️ Admin only. This reaches undocumented firmware commands and, if you ask
> it to, can open valves. It is an exploration tool, not an automation building
> block.**

**Read commands run as-is. Commands that *act* on the controller — start or stop
watering, change the clock, set a rain delay — are refused unless you add
`allow_unsafe: true`.** Nothing is forbidden outright; the gate is there so a
typo cannot start watering the garden.

The classification is derived from the controller's own command table rather
than a hand-written list: a command whose only reply is a bare acknowledgement
is one that acts. New commands are therefore guarded automatically.

Two modes:

**`sip`** (default) — an encoded SIP command, by name:

```yaml
action: rainbird_advanced.raw_command
data:
  config_entry_id: 01JABCDEF...
  command: ModelAndVersionRequest
```

Names come from pyrainbird's `RAINBIRD_COMMANDS`. Positional integer parameters
go in `params`:

```yaml
action: rainbird_advanced.raw_command
data:
  config_entry_id: 01JABCDEF...
  command: CurrentStationsActiveRequest
  params: [0]
```

**`rpc`** — a zero-argument JSON-RPC method. Useful for probing what your
firmware exposes:

```yaml
action: rainbird_advanced.raw_command
data:
  config_entry_id: 01JABCDEF...
  command: getSettings
  mode: rpc
```

Both return the decoded response. Call from **Developer Tools → Actions** and
tick *Return response*.

To send something that acts, opt in explicitly — this really does water zone 3
for 5 minutes:

```yaml
action: rainbird_advanced.raw_command
data:
  config_entry_id: 01JABCDEF...
  command: ManuallyRunStationRequest
  params: [3, 5]
  allow_unsafe: true
```

If you use `getSettings` to work out what `flow_units` actually means on real
hardware, [please open an issue][issues] — that is the missing piece for
seeding flow rates automatically.

---

## Dashboard

A worked example is in [`examples/lovelace-dashboard.yaml`](examples/lovelace-dashboard.yaml):
a conditional "watering now" card, per-zone history, and monthly volume
statistics.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[pyrainbird]: https://github.com/allenporter/pyrainbird
[issues]: https://github.com/mdespeuilles/rainbird_advanced/issues
