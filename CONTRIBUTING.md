# Contributing

## Development environment

Requires **Python 3.14+** (`pytest-homeassistant-custom-component` requires it;
pyrainbird requires 3.13+).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest
```

## Running the tests

```bash
pytest                      # everything
pytest tests/test_run_tracker.py    # the important one
pytest --cov=custom_components/rainbird_advanced --cov-report=term-missing
```

`run_tracker.py` should stay above 90% — every duration the integration reports
comes out of it.

## How the tests mock the device

Tests mock `AsyncRainbirdController` with `AsyncMock(spec=...)` and patch
`custom_components.rainbird_advanced.api.create_controller` — **where it is
used**, not where it is defined.

`spec=` is deliberate: it makes an upstream signature change fail the tests
instead of passing against a mock that no longer resembles the real class.

**Why not pyrainbird's fake device?** `pyrainbird.testing.RainbirdFakeServer`
looks ideal — it even simulates an ESP-TM2. It is unusable here: its handler
table has no entry for `4C` (`CombinedControllerStateRequest`), which is one of
this integration's two primary polls, so the fake would NACK it. It also binds
real sockets, which the test harness blocks. Contributing a `4C` handler
upstream would be a worthwhile change.

Schedules in tests are built from **real** pyrainbird objects (`Schedule`,
`Program`, `ZoneDuration`) rather than mocked, so the recurrence and timeline
logic the program inference relies on is genuinely exercised.

## Architecture

```
api.py           Lock + backoff. The ONLY module that touches the controller.
coordinator.py   Fast state poll (30s) + slow schedule poll (1h).
run_tracker.py   Transition detection + Store persistence.
program_infer.py Timeline cross-reference for the program sensor.
entity.py        Base entities + RainbirdControlMixin (control + refresh).
sensor.py        Read-only sensors.
binary_sensor.py Rain sensor.
switch.py        Zone on/off + the start_irrigation entity service.
number.py        Per-zone duration setpoints + rain delay.
button.py        Stop, advance, run program A/B/C.
calendar.py      Upcoming waterings from the schedule timeline.
services.py      raw_command.
```

All control goes through `RainbirdControlMixin._async_control`, which routes to
`api.execute()` (so it takes the single device lock), surfaces device errors as
`HomeAssistantError`, and requests a debounced refresh so state entities catch
up. Never call the controller from an entity directly.

Two rules worth knowing before you change things:

**1. Nothing calls the controller outside `api.py`.** The LNK2 accepts one
connection at a time. `RainbirdApi.execute()` holds the lock for a whole
logical operation, so a 20-request `get_schedule()` can never be interleaved
with a poll. Bypassing it reintroduces the collisions the integration exists to
survive. Note that pyrainbird's `min_delay` is *pacing*, not mutual exclusion —
it is a plain elapsed-time check that two concurrent tasks would both pass.

**2. `RainbirdAdvState.fetched_at` is load-bearing.** On a tolerated failure the
coordinator returns the *previous object*, unchanged. The tracker keys off
`fetched_at` to recognize that replay and do nothing. If you ever make that path
build a fresh object — or synthesize an empty zone set — the tracker will read
it as "every zone stopped" and write a bogus run.

## Gotchas the hard way

- **Never use `Schedule.timeline`.** It passes `datetime.now().tzinfo`, which is
  `None` for a naive `now()`, producing naive events anchored to the *host's*
  timezone rather than Home Assistant's. It fails silently. Always
  `timeline_tz(dt_util.DEFAULT_TIME_ZONE)`. Guarded by
  `test_timeline_is_built_in_home_assistant_timezone`.
- **`ProgramTimeline` has `at_instant()`, not `active_at()`.** The latter lives
  on a different `ical` class.
- **`_process_command` is private pyrainbird API.** It is the only route to
  name-based SIP commands. The manifest pins pyrainbird exactly and
  `test_pyrainbird_private_api_is_intact` fails loudly on a version bump.
- **`WifiParams.mac_address` is `Optional`.** No MAC means no unique id.
- **`max_zones`/`max_programs` return 0** until `get_model_and_version()` has
  been awaited — they read a field that call populates as a side effect.
- **Zone keys are strings** in storage and options. JSON object keys cannot be
  ints, and letting them round-trip silently breaks lookups.
- **`Store.async_delay_save`'s callback runs in an executor thread.** Build the
  snapshot on the loop and close over it by value. Closing over `self` and
  building lazily is the classic bug.

## Adding an entity

1. Put it in the platform file for its kind (`sensor.py`, `switch.py`, …),
   extending `RainbirdAdvEntity` (fast coordinator) or `RainbirdAdvScheduleEntity`
   (hourly). Needing both? See `RainbirdActiveProgramSensor` — it subscribes to
   the second one manually. If it commands the device, also mix in
   `RainbirdControlMixin` and call `self._async_control(...)`.
2. Register it in that platform's `async_setup_entry`, and make sure the
   platform is listed in `PLATFORMS` in `__init__.py`.
3. Add `translation_key` entries to `strings.json`, `translations/en.json` and
   `translations/fr.json`, and an icon to `icons.json` (optional). No
   `_attr_icon`.
4. Add a test.

**Do not add a request to the 30 s cycle without a good reason.** It is two
requests today. Slow-changing data belongs on the hourly coordinator — that is
why `global_disable` lives there.

## pyrainbird version

The manifest pins `pyrainbird==6.5.0`, matching what HA core's `rainbird`
integration pins. Home Assistant installs both into one environment, so a
mismatch would upgrade the package under core's feet. **When bumping, check
core's pin first:**

```bash
grep requirements .../homeassistant/components/rainbird/manifest.json
```

## Pull requests

- `ruff format` and `ruff check` must pass
- new behavior needs a test
- if you hit a device limitation, document it in the README's
  "What this integration cannot do" section rather than working around it
  silently
