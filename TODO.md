# TODO — Carbon-Aware EV Charging

## Reliability & Robustness

- [x] **Entity availability diagnostics** — Raise an HA Repair issue when the configured CO2 or fossil sensor stays `unavailable` for an extended period, instead of silently falling back.
- [x] **Stale-data detection** — Tiered staleness: soft stale (>30 min) logs a warning and sets `data_stale` but keeps the carbon gate active; hard unavailable (>60 min for 3 consecutive polls) sets `carbon_data_unavailable` and falls through to schedule/paused logic. Prevents flapping between `data_stale` and `low_carbon` when sensor updates are slightly delayed.
- [x] **Min cooldown on turn-on** — Add a `MIN_COOLDOWN_MINUTES` to prevent the charger restarting immediately after being turned off when the z-score bounces near the threshold. Bypassed on fresh plug-in or force_on mode.
- [x] **Deque timestamp pruning** — `_update_statistics` now prunes entries older than 7d/30d from the rolling deques before computing statistics, preventing stale data from skewing results after HA restarts or polling gaps.

## User Experience

- [ ] **Auto-generated dashboard** — On setup, create a sidebar Lovelace dashboard with gauges, controls, and history graphs using the actual entity IDs. See `PLAN-dashboard.md`.
- [ ] **Energy dashboard integration** — Expose a `total_increasing` sensor tracking cumulative kWh charged during "carbon" vs. "scheduled" windows for HA's built-in Energy dashboard.
- [x] **Enum-typed status sensor** — `ev_charging_status` is now a `device_class: enum` sensor with 12 fixed states (`not_connected`, `forced_off`, `override`, `low_carbon`, `departure_prep`, `fallback`, `data_stale`, `waiting_for_data`, `fossil_high`, `grid_dirty`, `unavailable`, `unknown`). The human-readable explanation is in the `status_reason` attribute.
- [x] **`ev_low_carbon_now` as a proper `binary_sensor`** — Converted from a sensor returning `"True"`/`"False"` strings to a native `binary_sensor` with `is_on` for proper on/off icons, history colouring, and automation triggers.

## Intelligence & Features

- [ ] **Forecast-aware scheduling** — Integrate with solar forecast (Forecast.Solar, Solcast) or Electricity Maps forecast endpoints to plan charging windows proactively rather than reacting in real time.
- [ ] **Target SoC / energy budget** — Let users set a target kWh per session (or read an SoC entity). Charge aggressively when the car is low and departure is soon; be picky about carbon when there's plenty of time.
- [ ] **Cost-aware mode** — Add an optional electricity price sensor and a "minimise cost" or "balance cost + carbon" mode for users with time-of-use tariffs.
- [x] **Configurable fallback windows** — The fixed 22:00–06:00 and 11:00–15:00 windows are now user-configurable via the options flow and number/switch entities. Each window has an enabled toggle and start/end hour settings. Defaults to the original hardcoded values.

## Code Quality & Developer Experience

- [x] **Dry-run as a `switch` entity** — Exposed `dry_run` as an `EvOptionSwitch` entity so users can toggle it from the dashboard or automations without navigating to the integration options flow.
- [x] **Diagnostics handler** — `diagnostics.py` implements `async_get_config_entry_diagnostics()` exposing config, options (with `notify_service` redacted), coordinator state (deque sizes, z-score, staleness counters, unavailability tracking), and current decision data.
- [x] **Config entry migration** — `async_migrate_entry` in `__init__.py` handles VERSION 1 (no-op) and rejects future versions. Ready for schema changes.
- [x] **Shared entity base class** — All platform files now import `EVChargerBaseEntity` from `base_entity.py`, which provides `device_info`, `_data`, and `_async_update_option()`. Removed duplicate base classes and ~80 lines of boilerplate across `sensor.py`, `binary_sensor.py`, `select.py`, `number.py`, and `switch.py`.
- [x] **Move local constants to `const.py`** — `_DAY_OPTIONS` moved from `config_flow.py` to `const.py` as `DAY_OPTIONS`.
- [x] **Consistent entity unique ID patterns** — All entity unique_id suffixes are now defined as `ENTITY_ID_*` constants in `const.py`. Dynamic entities (switches, fallback-window numbers) use `CONF_*` keys as suffixes, with a comment warning against renaming. Prevents accidental history loss on refactor.
- [ ] **Config validation improvements** — Notify service format check is duplicated in config_flow; fallback window start/end not validated (start < end); no check that at least one fallback window is enabled.
- [ ] **Test coverage gaps** — Fill notable gaps:
  - [ ] LED control logic in the coordinator
  - [ ] Notification service calls
  - [ ] Options flow in config_flow
  - [ ] Full update cycle integration test checking charger service calls
  - [ ] Min-dwell timer logic
- [ ] **Centralise test fixtures** — Each test file defines its own `_make_coordinator()` / `_make_coord()` and `_set_state()` helpers. Move these to `conftest.py` as shared fixtures.

## Completed

- [x] **Clean up storage on deletion** — `async_remove_entry` in `__init__.py` removes the persisted rolling-stats store file when the integration is deleted, preventing orphaned `.storage` files.
- [x] **Centralised preference defaults** — All preference default values (`PREFERENCE_DEFAULTS` dict in `const.py`) are now defined in one place. Coordinator `_resolve_config()`, config flow (initial setup + options), and entity `native_value`/`current_option`/`is_on` properties all reference this single source of truth. Eliminated ~20 scattered hardcoded defaults across 5 files.
- [x] **Coordinator method extraction** — Broke the monolithic `_async_update_data` (~350 lines) into five focused methods: `_resolve_config()`, `_read_sensors()`, `_update_statistics()`, `_evaluate_charging()`, `_control_devices()` with typed dataclass contracts between them. Also centralised the config/options precedence pattern via a `_pref()` helper, moved `_DAY_NAMES` to module level, and converted `_UNAVAILABLE_STATES` to `frozenset`.
- [x] **Recorder history backfill** — On first start, seed rolling deques from HA recorder's existing CO2 history.
- [x] **Persisted rolling history** — Deques and last z-score survive HA restarts via `Store`.
- [x] **Status reason sensor** — Human-readable explanation of what the charger is doing and why.
