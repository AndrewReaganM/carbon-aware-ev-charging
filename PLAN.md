# PLAN: Carbon-Aware EV Charging — HACS Custom Integration

## What We Are Building

A HACS-installable Home Assistant custom integration that replicates the
functionality currently spread across `ev.yaml`, `ev_helpers.yaml`,
`ev_sensors.yaml`, and `templates.yaml`. Users install via HACS, complete
a UI config flow (no YAML editing required), and get all entities, sensors,
and automations created automatically.

---

## Architecture Overview

```
custom_components/carbon_aware_ev_charging/
├── __init__.py              Integration setup, forward entry setup to platforms
├── manifest.json            HACS metadata: name, version, iot_class, dependencies
├── config_flow.py           UI wizard: collect entity IDs and preferences
├── const.py                 All string constants, default values, domain name
├── coordinator.py           DataUpdateCoordinator: fetches CO2/fossil data every 5 min
├── sensor.py                Registers sensor entities (Z-score, low_carbon_now)
├── binary_sensor.py         Registers binary_sensor.ev_connected
├── select.py                Registers input_select equivalents as SelectEntity
├── number.py                Registers input_number.ev_departure_hour as NumberEntity
├── switch.py                (Optional) Wraps charger switch with charging logic
├── automation.py            Registers the charging automation logic as a service/script
├── strings.json             UI strings for config flow labels and descriptions
├── translations/
│   └── en.json              English translations (mirrors strings.json)
└── services.yaml            Declares any custom services exposed by the integration
```

---

## Phase 1 — Scaffold and Manifest

### Tasks
1. Create `custom_components/carbon_aware_ev_charging/` directory
2. Write `manifest.json`:
   ```json
   {
     "domain": "carbon_aware_ev_charging",
     "name": "Carbon-Aware EV Charging",
     "version": "0.1.0",
     "config_flow": true,
     "iot_class": "local_polling",
     "dependencies": [],
     "requirements": [],
     "codeowners": []
   }
   ```
3. Write `const.py` with all magic strings:
   - `DOMAIN = "carbon_aware_ev_charging"`
   - Config entry keys: `CONF_CO2_SENSOR`, `CONF_FOSSIL_SENSOR`, `CONF_CHARGER_SWITCH`,
     `CONF_CHARGER_ATTR`, `CONF_LED_LIGHT`, `CONF_LED_EFFECT_SELECT`,
     `CONF_DEPARTURE_HOUR`, `CONF_CARBON_MODE`
   - Threshold constants: `THRESHOLD_LENIENT`, `THRESHOLD_MODERATE`, `THRESHOLD_STRICT`
   - `FOSSIL_HARD_FLOOR = 75`
   - `STATS_WARMUP_MIN_STDEV = 5`, `STATS_WARMUP_MIN_MEAN = 50`
4. Write minimal `__init__.py` that calls `hass.config_entries.async_setup_entry`
   forwarding to `sensor`, `binary_sensor`, `select`, `number` platforms.

### Acceptance Criteria
- Integration loads without errors (check HA logs)
- Appears in Settings → Integrations with "Add" button

---

## Phase 2 — Config Flow

### Tasks
1. Write `config_flow.py` as a `ConfigFlow` subclass with `async_step_user`.
2. **Step 1 — Required sensors**: Ask for:
   - CO2 intensity sensor entity ID (suggest `sensor.` prefix, validate it exists)
   - Fossil fuel % sensor entity ID
   - Charger switch entity ID
   - Charger "connected" attribute name (default: `icon_name`, not-connected value: `CarNotConnected`)
3. **Step 2 — Optional LED**: Ask for:
   - RGB indicator light entity ID (optional)
   - LED effect select entity ID (optional)
4. **Step 3 — Preferences**: Ask for:
   - Carbon mode (dropdown: Lenient / Moderate / Strict)
   - Departure hour (number 0–23)
   - Enable dry-run (checkbox, default off)
5. Validate all provided entity IDs exist in `hass.states`.
6. Store as a config entry. Write `strings.json` / `translations/en.json` with
   human-readable labels and descriptions for each field.

### Options Flow
Add `OptionsFlow` so users can change carbon mode, departure hour, and dry-run
from the integration's "Configure" button without re-running the full wizard.

### Acceptance Criteria
- Full wizard completes and creates a config entry
- Invalid entity IDs show inline error messages
- Options flow lets user change mode without re-entering entity IDs

---

## Phase 3 — DataUpdateCoordinator

### Tasks
1. Write `coordinator.py` as a `DataUpdateCoordinator` subclass.
   - Poll interval: 5 minutes (matches current time_pattern trigger)
   - `_async_update_data()` reads current states:
     - `co2_intensity` from configured sensor
     - `fossil_pct` from configured sensor
     - Computes `is_connected` from charger switch attribute
   - Store 7-day rolling stats:
     - On each update, append `(timestamp, co2_value)` to an in-memory deque
       with `maxlen = 2016` (7d × 288 readings/day)
     - Compute `mean` and `stdev` from deque
     - Guard: `stdev > 5 and mean > 50` before computing Z-score
     - Fall back to last good Z-score if guard fails (prevents reload spikes)
   - Compute `z_score`, `low_carbon_now`, `predicted_state`, `should_charge`
   - Expose results as a dataclass on `coordinator.data`
2. Instantiate coordinator in `__init__.py` on entry setup; store on
   `hass.data[DOMAIN][entry_id]`.

### Note on statistics platform replacement
The current setup relies on HA's `statistics` platform sensors (7d mean/stdev).
The coordinator replaces these with in-memory rolling stats. This avoids creating
"helper" sensor entities and keeps all logic inside the integration.
A 30d deque can be maintained in parallel for the dashboard stats boxes.

### Acceptance Criteria
- `coordinator.data.z_score` updates every 5 minutes
- Z-score is `None` during warmup (< 7 days of data)
- No reload spikes (guard verified by unit test)

---

## Phase 4 — Sensor Entities

### Tasks
1. Write `sensor.py` registering two sensors per config entry:

   **`sensor.ev_co2_z_score`**
   - native_value: `coordinator.data.z_score` (float, rounds to 2dp)
   - unit: `"σ"`
   - state_class: `SensorStateClass.MEASUREMENT`
   - available: `coordinator.data.z_score is not None`
   - icon: `mdi:sigma`

   **`sensor.ev_low_carbon_now`**
   - native_value: `True` / `False` as string (matches current template behavior)
   - icon: `mdi:leaf`
   - available: always (defaults to False during warmup)

2. Both sensors update via `CoordinatorEntity` — no polling needed in the entity itself.

### Acceptance Criteria
- Both sensors appear under the integration's device
- `ev_low_carbon_now` is `False` during coordinator warmup, not `unavailable`

---

## Phase 5 — Binary Sensor

### Tasks
1. Write `binary_sensor.py` registering:

   **`binary_sensor.ev_connected`**
   - `is_on`: reads `state_attr(charger_switch, attr_name) != not_connected_value`
   - `device_class`: `BinarySensorDeviceClass.PLUG`
   - Updates via coordinator poll (or state subscription to the charger switch)

### Acceptance Criteria
- Reflects car connection state within one poll cycle

---

## Phase 6 — Select and Number Entities

### Tasks
1. Write `select.py` registering:

   **`select.ev_charge_mode`**
   - options: `["auto", "force_on"]`
   - current_option: read from config entry options (persisted)
   - `async_select_option`: updates config entry options, triggers coordinator refresh

   **`select.ev_carbon_mode`**
   - options: `["Lenient", "Moderate", "Strict"]`
   - current_option: read from config entry options

2. Write `number.py` registering:

   **`number.ev_departure_hour`**
   - min: 0, max: 23, step: 1
   - native_unit: `"h"`
   - mode: `NumberMode.BOX`
   - Persisted in config entry options

### Acceptance Criteria
- Changing mode in UI immediately affects next coordinator cycle's `predicted_state`

---

## Phase 7 — Automation Logic (Charger Control + LED)

### Tasks
1. The coordinator already computes `should_charge` and `predicted_state`.
2. In `coordinator.py` `_async_update_data()`, after computing state:
   - If `should_charge` changed: call `hass.services.async_call` to
     `switch.turn_on` / `switch.turn_off` the configured charger switch
     (skip if dry_run is enabled)
   - Call `light.turn_on` with appropriate `hs_color` based on `predicted_state`
   - Call `select.select_option` on LED effect entity
3. Log decision via `_LOGGER.info(...)` matching the existing debug log format.

### Note on HA service calls in integrations
Direct service calls from within an integration are acceptable but couple the
integration tightly to HA services. An alternative is a separate `switch.py`
that wraps the charger and controls it via property setters — cleaner for testing.

### Acceptance Criteria
- Charger turns on/off correctly per predicted_state
- LED reflects state
- Dry-run prevents switch calls but still updates LED and logs

---

## Phase 8 — Testing

### Tasks
1. Write `tests/test_coordinator.py`:
   - Test Z-score is `None` with < 7d data
   - Test reload spike guard (stdev < 5 returns last good value)
   - Test each `predicted_state` branch
   - Test `ev_low_carbon_now` respects fossil hard floor
2. Write `tests/test_config_flow.py`:
   - Test valid entity IDs complete flow
   - Test invalid entity ID shows error
   - Test options flow updates mode
3. Use `pytest-homeassistant-custom-component` for HA test fixtures.

### Acceptance Criteria
- All tests pass with `pytest tests/`
- Z-score spike scenario explicitly covered

---

## Phase 9 — HACS Packaging

### Tasks
1. Create GitHub repository `ev-carbon-charger`.
2. Add `hacs.json` at repo root:
   ```json
   {
     "name": "Carbon-Aware EV Charging",
     "category": "integration"
   }
   ```
3. Tag a `v0.1.0` release on GitHub.
4. Submit to HACS default repository list (optional — can install by custom repo URL first).
5. Write `README.md` covering:
   - Prerequisites (Electricity Maps integration, compatible charger switch)
   - Installation via HACS
   - Config flow walkthrough
   - Entity reference table
   - Sensitivity mode explanation

### Acceptance Criteria
- Installable via HACS → "Custom repositories" → paste GitHub URL
- All entities appear after completing config flow with no YAML changes required

---

## Entity Naming Strategy

Current YAML uses bare entity IDs like `sensor.32_79_96_48_co2_intensity`.
The integration should use `unique_id` patterns tied to the config entry ID so
multiple instances don't collide:

```
sensor.{entry_id}_co2_z_score
sensor.{entry_id}_ev_low_carbon_now
binary_sensor.{entry_id}_ev_connected
select.{entry_id}_ev_charge_mode
select.{entry_id}_ev_carbon_mode
number.{entry_id}_ev_departure_hour
```

---

## Key Decisions / Open Questions

| Question | Recommendation |
|---|---|
| Should rolling stats be in-memory or persist across restarts? | Persist via `hass.helpers.storage` — otherwise warmup restarts on every HA restart |
| Should 30d stats be maintained? | Yes, add a second deque for the analysis dashboard |
| Should dashboards be auto-created? | Optional: use `lovelace.dashboard_create` service in `__init__` setup, behind a config flow checkbox |
| Support multiple chargers? | Yes — support multiple config entries, each with its own set of entities |
| `ev_connected` derived from icon attribute or separate sensor? | Make the attribute name and not-connected value configurable in config flow (different charger firmware exposes different attributes) |

---

## Dependencies

- `pytest-homeassistant-custom-component` — test framework
- No external Python packages required (all logic uses HA built-ins and stdlib `statistics` module)
- `custom:plotly-graph` (HACS frontend card) — required for analysis dashboard, document as prerequisite
