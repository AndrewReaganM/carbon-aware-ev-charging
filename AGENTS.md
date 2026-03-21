# Carbon-Aware EV Charging — Agent Guide

## What This Is

A HACS custom integration for Home Assistant that charges an electric vehicle
preferentially during low-carbon grid periods. The charging decision is driven
by a Z-score statistical signal computed from a 7-day rolling mean/stdev of
real-time CO₂ intensity data. Includes configurable sensitivity modes, fallback
charging windows, dry-run testing, push notifications, and optional LED status
feedback.

---

## Repository Layout

```
custom_components/carbon_aware_ev_charging/   Core integration code
├── __init__.py          Entry setup/unload, storage cleanup, options listener
├── manifest.json        HACS metadata (name, version, iot_class)
├── const.py             All constants — thresholds, defaults, entity IDs, status maps
├── coordinator.py       DataUpdateCoordinator: polls CO₂ data, computes Z-score,
│                        controls charger and LED, persists rolling history
├── config_flow.py       Three-step UI wizard + options flow
├── sensor.py            Z-score sensor, charging status sensor, charge rate/current
├── binary_sensor.py     ev_connected binary sensor
├── select.py            ev_charge_mode and ev_carbon_mode select entities
├── number.py            ev_departure_hour number entity
├── switch.py            Fallback-window enable/disable switches
├── base_entity.py       Shared CoordinatorEntity base class
├── diagnostics.py       HA diagnostics support
├── strings.json         UI string keys
└── translations/en.json English translations

tests/
├── conftest.py                    pytest-homeassistant-custom-component fixtures
├── test_coordinator.py            Unit tests for Z-score, decision logic, staleness
├── test_coordinator_integration.py Integration tests (full HA hass fixture)
├── test_config_flow.py            Config and options flow tests
├── test_entities.py               Sensor/select/number/binary-sensor entity tests
├── test_init.py                   Setup, unload, migrate, storage removal tests
└── test_diagnostics.py            Diagnostics redaction tests

ev_dashboard.yaml         Ready-made operational HA dashboard (copy into HA)
hacs.json                 HACS category declaration
pyproject.toml            Python project metadata, dev deps, ruff/ty config
prek.toml                 Pre-commit hook config (ruff, ty, yaml/toml/json checks)
.releaserc.json           semantic-release config (bumps version in manifest + pyproject)
.github/workflows/
└── release.yml           CI pipeline: lint → type-check → test → HACS/hassfest validate → release
```

---

## Architecture

### Coordinator (`coordinator.py`)

`EVCarbonCoordinator` is the heart of the integration. It:

1. Polls every 5 minutes (and reacts immediately to state changes on CO₂,
   fossil-fuel, and charger entities).
2. Maintains two in-memory time-bounded `deque`s:
   - `_deque_7d` — up to `DEQUE_7D` (2 016) readings, pruned to 7 days
   - `_deque_30d` — up to `DEQUE_30D` (8 640) readings, pruned to 30 days
3. On first install (empty deques), backfills history from the HA recorder so
   the Z-score becomes useful immediately rather than after 7 days.
4. Persists deques to `hass.helpers.storage` so rolling stats survive restarts.
5. Runs a four-phase update pipeline each cycle:
   - `_resolve_config()` — merges `entry.data` + `entry.options` with defaults
   - `_read_sensors()` — parses CO₂, fossil %, charger state, power sensor
   - `_update_statistics()` — appends reading, prunes deques, computes Z-score
   - `_evaluate_charging()` — determines `status_enum` and `predicted_state`
   - `_control_devices()` — actuates charger switch and LED (respects dry-run,
     dwell, and cooldown guards)

### Decision chain (`_evaluate_charging`)

Priority order (highest wins):

```
charge_mode == force_off  → STATUS_FORCED_OFF   (paused)
charge_mode == force_on   → STATUS_OVERRIDE     (charging)
carbon gate open          → STATUS_LOW_CARBON   (charging)
roadtrip prep window      → STATUS_ROADTRIP_PREP (charging)
departure prep window     → STATUS_DEPARTURE_PREP (charging)
data unavailable + fallback window → STATUS_FALLBACK (charging)
data stale                → STATUS_DATA_STALE   (paused)
data unavailable          → STATUS_WAITING_FOR_DATA (paused)
fossil % ≥ 75             → STATUS_FOSSIL_HIGH  (paused)
z_score ≥ threshold       → STATUS_GRID_DIRTY   (paused)
car not connected         → STATUS_NOT_CONNECTED (paused, overlaid last)
```

`STATUS_MAP` in `const.py` is the single source of truth mapping each
`status_enum` to `(predicted_state, chargeable)`. Never duplicate this logic.

### Roadtrip Prep feature

The integration watches configured HA calendar entities for events whose
titles match `[PREFIX optional_soc% optional_lead_h]` and starts charging
ahead of the event start to hit a target SoC.

**Title format** — all optional fields after PREFIX are individually optional:
```
[PREFIX soc% lead_h]   e.g. [IONIQ 90% 4h]
[IONIQ 80%]            → soc=80, lead=default
[IONIQ 6h]             → soc=None, lead=6
[IONIQ]                → soc=None, lead=default
```
Regex: `\[(?P<prefix>[^\]0-9%h]+?)(?:\s+(?P<soc>\d+)%)?(?:\s+(?P<lead>\d+)h)?\]`
Prefix matching is case-insensitive.

**Multiple events**: all matching events in the 24-hour lookahead window are
merged into one synthetic event with the **earliest start** and the **highest
SoC target** across all overlapping events.

**SoC gate**: if a SoC sensor is configured and the current SoC ≥ target, the
roadtrip prep branch is skipped (no-op) — charger stays off unless another
branch fires.

**Charge limit**: before turning the charger on for roadtrip prep, if a charge
limit entity is configured, `_async_set_charge_limit()` is called first. The
entity domain is auto-detected: `number` → `number.set_value`; `select` →
`select.select_option`.

**LED**: roadtrip prep uses a distinct cyan colour (`LED_COLOUR["roadtrip"] =
[180, 90]`) rather than the generic scheduled (red) colour.

### Carbon gate

```
carbon_good = (
    not carbon_data_unavailable
    AND z_score < effective_threshold   # threshold + 0.4σ hysteresis when already on
    AND fossil_pct < 75                 # hard floor always enforced
)
```

### Z-score

```
z_score = (co2 - mean_7d) / stdev_7d
```

When `stdev_7d == 0` (all readings identical), `z_score = 0.0`. When CO₂ is
unavailable, the last good Z-score is held rather than returning `None`, to
prevent spurious state changes.

### Dwell and cooldown guards

- **Dwell** (`MIN_DWELL_MINUTES = 15`): once the charger turns on, it stays on
  for at least 15 minutes before the integration will turn it off again.
- **Cooldown** (`MIN_COOLDOWN_MINUTES = 10`): after the charger turns off, the
  integration waits 10 minutes before turning it on again.
- Both guards are bypassed in `force_on` mode and on initial car reconnect.

### Staleness detection

- **Soft stale** (> 30 min since last update): flag raised in logs and UI;
  carbon data is still used.
- **Hard stale** (> 60 min, 3 consecutive polls): sensor treated as unavailable;
  falls back to scheduled windows.

---

## Key Constants (`const.py`)

| Constant | Value | Purpose |
|---|---|---|
| `DOMAIN` | `"carbon_aware_ev_charging"` | Integration domain |
| `THRESHOLD_LENIENT` | `0.92` | Z-score gate for Lenient mode (~82% of hours) |
| `THRESHOLD_MODERATE` | `0.47` | Z-score gate for Moderate mode (~68% of hours) |
| `THRESHOLD_STRICT` | `-0.18` | Z-score gate for Strict mode (~43% of hours) |
| `FOSSIL_HARD_FLOOR` | `75.0` | Max fossil % before carbon gate is forced closed |
| `HYSTERESIS_SIGMA` | `0.4` | Added to threshold when charger is already on |
| `DEQUE_7D` | `2016` | Max rolling-window readings (7d × 288/day) |
| `DEQUE_30D` | `8640` | Max rolling-window readings (30d × 288/day) |
| `MIN_DWELL_MINUTES` | `15` | Minimum on-time before turning charger off |
| `MIN_COOLDOWN_MINUTES` | `10` | Minimum off-time before turning charger back on |
| `DEPARTURE_PREP_HOURS` | `3` | Hours before departure hour that prep charging begins |
| `STALE_DATA_MINUTES` | `30` | Soft-stale threshold (minutes) |
| `STALE_HARD_MINUTES` | `60` | Hard-stale threshold (minutes) |
| `STALE_HARD_CONSECUTIVE` | `3` | Consecutive hard-stale polls before unavailable |
| `SENSOR_UNAVAILABLE_REPAIR_MINUTES` | `30` | Minutes before HA Repair issue is raised |

### Entity unique ID suffixes

Entity unique IDs are `{entry.entry_id}_{suffix}`. **Never change these
values** — doing so orphans the entity in HA and loses its history.

| Constant | Suffix | Entity |
|---|---|---|
| `ENTITY_ID_Z_SCORE` | `co2_z_score` | Z-score sensor |
| `ENTITY_ID_CHARGING_STATUS` | `ev_charging_status` | Status enum sensor |
| `ENTITY_ID_CHARGE_RATE_KW` | `ev_charge_rate_kw` | Charge power sensor |
| `ENTITY_ID_CHARGE_CURRENT` | `ev_charge_current` | Charge current sensor |
| `ENTITY_ID_CONNECTED` | `ev_connected` | Car-connected binary sensor |
| `ENTITY_ID_LOW_CARBON_NOW` | `ev_low_carbon_now` | Carbon gate sensor |
| `ENTITY_ID_CHARGE_MODE` | `ev_charge_mode` | Charge mode select |
| `ENTITY_ID_CARBON_MODE` | `ev_carbon_mode` | Carbon sensitivity select |
| `ENTITY_ID_DEPARTURE_HOUR` | `ev_departure_hour` | Departure hour number |

---

## Entities Created

One logical HA device is registered per config entry, grouping:

| Entity type | Entity | Description |
|---|---|---|
| `sensor` | `ev_co2_z_score` | Current Z-score (σ, 2 dp); `None` during warmup |
| `sensor` | `ev_charging_status` | Status enum (device_class: enum) with human-readable reason |
| `sensor` | `ev_low_carbon_now` | `True` when carbon gate is open |
| `sensor` | `ev_charge_rate_kw` | Current charge power in kW (requires power sensor) |
| `sensor` | `ev_charge_current` | Current charge current in amps |
| `binary_sensor` | `ev_connected` | `on` when car is plugged in |
| `select` | `ev_charge_mode` | `auto` / `force_on` / `force_off` |
| `select` | `ev_carbon_mode` | `Lenient` / `Moderate` / `Strict` |
| `number` | `ev_departure_hour` | Hour (0–23) to begin departure-prep charging |
| `switch` | fallback window 1 | Enable/disable overnight fallback window |
| `switch` | fallback window 2 | Enable/disable midday fallback window |

---

## Config Flow (`config_flow.py`)

Three setup steps, all validated against live HA states:

**Step 1 — Sensors & Charger** (stored in `entry.data`)
- CO₂ intensity sensor (`sensor` domain, required)
- Fossil fuel % sensor (`sensor` domain, required)
- Charger switch (`switch` domain, required)
- Connection attribute name (default: `icon_name`)
- Not-connected attribute value (default: `CarNotConnected`)
- Charger power sensor (`sensor` domain, optional)

**Step 2 — LED Indicator** (stored in `entry.data`, both optional)
- RGB indicator light (`light` domain)
- LED effect selector (`select` domain)

**Step 3 — Preferences** (stored in `entry.options`, changeable without re-wizard)
- Carbon sensitivity mode (`Lenient` / `Moderate` / `Strict`)
- Departure hour (0–23)
- Departure days (multi-select, Mon–Sun)
- Dry-run toggle
- Fallback window 1 enable/start/end (default: 22:00–06:00)
- Fallback window 2 enable/start/end (default: 11:00–15:00)
- Notification service (optional, must start with `notify.`)

An **options flow** (`EVCarbonChargerOptionsFlow`) exposes all Step 3 fields
for reconfiguration via **Settings → Devices & Services → Configure**.

### Config entry storage split

`entry.data` holds entity IDs and hardware config (immutable after setup).
`entry.options` holds user preferences (mutable via options flow). The
coordinator always resolves the merged view via `_resolve_config()`, which
applies `entry.options` over `entry.data` with `PREFERENCE_DEFAULTS` as the
final fallback.

---

## Development Setup

Requires Python 3.12+, managed with `uv`.

```bash
uv sync          # install all dev dependencies into .venv
```

### Running tests

```bash
uv run pytest                        # all tests
uv run pytest tests/test_coordinator.py  # one file
uv run pytest -x -q                  # fail-fast, quiet
```

Tests use `pytest-homeassistant-custom-component` which provides the full HA
`hass` fixture. The `conftest.py` enables custom integrations via
`auto_enable_custom_integrations`.

### Linting and type checking

```bash
uv run ruff check .          # lint
uv run ruff check --fix .    # lint + auto-fix
uv run ruff format .         # format
uv run ty check              # type check (Astral ty)
```

Pre-commit hooks (via `prek.toml`) run ruff and ty automatically on commit.
Direct pushes to `main` are blocked by the `no-commit-to-branch` hook.

### Commit message convention

This repo uses **Conventional Commits** enforced by semantic-release:

```
feat: add X         → minor version bump
fix: correct Y      → patch version bump
feat!: breaking Z   → major version bump
chore: ...          → no release
docs: ...           → no release
test: ...           → no release
refactor: ...       → no release (unless feat/fix)
```

The CI pipeline auto-releases on every push to `main` if all checks pass.

---

## CI Pipeline (`.github/workflows/release.yml`)

Jobs run in parallel, all must pass before `release`:

| Job | Tool | What it checks |
|---|---|---|
| `lint` | `ruff check` + `ruff format --check` | Style and code quality |
| `type-check` | `ty check` | Static types |
| `test` | `pytest` | All unit and integration tests |
| `validate-hacs` | `hacs/action` | HACS manifest correctness |
| `validate-hassfest` | `home-assistant/actions/hassfest` | HA integration correctness |
| `release` | `semantic-release` | Cuts GitHub release, bumps version |

The `release` job bumps the version in `pyproject.toml` and
`manifest.json` via the `@semantic-release/exec` plugin, then commits
`chore(release): X.Y.Z [skip ci]` back to `main`.

---

## Adding a New Entity

1. Choose a unique ID suffix string and add it to `const.py` as
   `ENTITY_ID_<NAME> = "<suffix>"`. Never change it after release.
2. Add the platform string to `PLATFORMS` in `const.py` if it is a new
   platform type.
3. Implement the entity in the appropriate platform file, inheriting from
   `CarbonAwareEVChargingEntity` in `base_entity.py`.
4. Register it in the platform's `async_setup_entry` function.
5. Add translation keys to `strings.json` and `translations/en.json`.
6. Write tests in the relevant `tests/test_*.py` file.

---

## Modifying the Decision Logic

All charging-state logic lives in `coordinator.py:_evaluate_charging`. The
mapping from `status_enum` to `(predicted_state, chargeable)` is the
`STATUS_MAP` dict in `const.py`. Changes to the decision chain should:

1. Add any new `STATUS_*` constants to `const.py`.
2. Update `CHARGING_STATUSES` list and `STATUS_MAP` dict in `const.py`.
3. Add translation keys in `strings.json` and `translations/en.json`.
4. Update `_evaluate_charging` in `coordinator.py`.
5. Add test cases covering the new branch in `tests/test_coordinator.py`.

---

## Known Issues / Quirks

- `entry.data` entity IDs cannot be changed after setup without re-adding the
  integration. Hardware-level config is intentionally separated from preferences.
- The Z-score requires at least 2 readings before it has a value, and is only
  statistically meaningful after ~7 days. During warmup, the carbon gate
  defaults to `False` (falls back to scheduled windows).
- When `stdev_7d == 0` (all readings identical, e.g. immediately after install
  with a single backfilled value), `z_score` is returned as `0.0`.
- The recorder backfill runs only on first install (empty deques). Subsequent
  restarts restore from the integration's own `hass.helpers.storage` file.
- Config entry schema version is `1`. Any breaking change to `entry.data`
  structure requires an `async_migrate_entry` migration path in `__init__.py`.
- Do not use `template:` inside HA packages — it is a top-level singleton and
  the last definition silently wins. The included `ev_dashboard.yaml` avoids
  this pattern.
- The `ev_dashboard.yaml` contains three placeholder entity IDs
  (`YOUR_CO2_SENSOR`, `YOUR_FOSSIL_SENSOR`, `YOUR_CHARGER_SWITCH`) that must
  be replaced before use.
