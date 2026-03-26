# Carbon-Aware EV Charging — Agent Guide

## What This Is

A HACS custom integration for Home Assistant that charges an EV preferentially
during low-carbon grid periods. The charging decision is driven by a Z-score
statistical signal computed from a 7-day rolling mean/stdev of real-time CO₂
intensity data. Includes configurable sensitivity modes, fallback charging
windows, dry-run testing, push notifications, and optional LED status feedback.

---

## Build, Lint, Test

Requires Python 3.12+, managed with `uv`.

```bash
uv sync                                         # install dev dependencies

uv run pytest                                   # all tests
uv run pytest tests/test_coordinator.py         # single file
uv run pytest tests/test_coordinator.py::TestCarbonGate  # single class
uv run pytest tests/test_coordinator.py::TestCarbonGate::test_fossil_high  # single test
uv run pytest -x -q                            # fail-fast, quiet

uv run ruff check .                             # lint
uv run ruff check --fix .                       # lint + auto-fix
uv run ruff format .                            # format
uv run ty check                                 # type-check (Astral ty)

git add -A && prek                              # run all pre-commit hooks
```

**Always `git add` before running `prek`** — it stashes unstaged changes and
tests the staged version. Direct pushes to `main` are blocked by the
`no-commit-to-branch` hook.

After any code change: run the full test suite and prek, and fix everything
before considering the task done.

---

## Code Style

### General

- Python 3.12+. Line length: **100**. Quote style: **double**. Indent: **spaces**.
- Every file starts with `from __future__ import annotations`.
- No `print()` statements — use `_LOGGER` (see Logging below).
- No bare `except:` — always name the exception type.

### Imports

Isort order enforced by ruff (`I` rules):
1. `from __future__ import annotations`
2. stdlib (`contextlib`, `logging`, `re`, `statistics`, `collections`, `datetime`, `typing`)
3. HA core (`homeassistant.*`)
4. Local (`.const`, `.coordinator`, `.base_entity`)

Use explicit named imports from `.const` — never `from .const import *`.

### Types

- All function signatures and return types must be annotated.
- Use `X | None` (not `Optional[X]`). Use `X | Y` unions (not `Union[X, Y]`).
- Use built-in generics: `list[str]`, `dict[str, Any]`, `tuple[float, float]`.
- `Any` is acceptable for HA state objects and raw config dicts.
- HA-internal attribute assignments in tests trigger false-positive
  `invalid-assignment` errors from `ty` — these are suppressed globally for
  `tests/**` in `pyproject.toml`. Do not add inline `# type: ignore` for these.
- Avoid `# type: ignore` entirely unless there is no other option.

### Naming

- `UPPER_SNAKE_CASE` for module-level constants in `const.py`.
- `_lower_snake` prefix for private methods and instance variables.
- `async_*` prefix for all coroutines (mirrors HA convention).
- `_LOGGER = logging.getLogger(__name__)` — one logger per module, module-level.
- Test helper factories: `_make_<thing>(overrides)` pattern, returning the
  constructed object. Override dicts use `**(overrides or {})`.

### Logging

Use `_LOGGER` with `%`-style formatting (enforced by `G` ruff rules):

```python
_LOGGER.debug("[EV] Some message: val=%s", val)       # correct
_LOGGER.debug(f"[EV] Some message: val={val}")        # wrong — f-string
```

Prefix all messages with `[EV]`. Use `_LOGGER.debug` for routine poll info,
`_LOGGER.info` for significant state transitions, `_LOGGER.warning` for
recoverable problems, `_LOGGER.exception` (with `exc_info=True`) for errors
with tracebacks.

### Error Handling

- Use `try/except SpecificError` — not `contextlib.suppress` — when the failure
  should be **visible in the HA log**. Always log a `WARNING` or `EXCEPTION` so
  users can diagnose problems.
- Use `contextlib.suppress(ValueError)` only for genuinely ignorable parse
  errors (e.g. `float()` on a bad string).
- Service calls that must succeed (e.g. setting charge limit) use
  `blocking=True` so errors surface immediately.
- Fire-and-forget service calls (charger on/off, LED, notifications) use
  `blocking=False` — failures are caught at the `_control_devices` level.
- The `_control_devices` wrapper catches only `ServiceNotFound` (boot-time
  platform not yet loaded). Any other exception propagates and marks the
  coordinator unavailable.
- After fixing a recoverable error (e.g. service now available), the guard
  variable (`_roadtrip_limit_applied_for`, `_last_led_state`) must **only be
  updated on success**, never before the call, so a failure is retried next poll.

### Dataclasses

Internal pipeline structs (`_ResolvedConfig`, `_SensorReadings`, `_Statistics`,
`_ChargingDecision`) are `@dataclass`. Use `field(default=None)` for optional
fields with a default. Never add business logic to dataclasses.

### Tests

- Tests use `pytest-homeassistant-custom-component` (`hass` fixture from HA).
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed on individual
  tests unless the file mixes sync and async (roadtrip tests use it explicitly).
- Group related tests in classes: `class TestFeatureName:`.
- Module-level `_make_<thing>` factory functions for shared fixtures; keep them
  close to the tests that use them.
- Tests that bypass `__init__` via `EVCarbonCoordinator.__new__` **must** set
  every instance variable that `__init__` sets. Check `coordinator.py:__init__`
  when adding new instance variables and update all three `_make_coord` /
  `_make_coordinator` helpers in `test_coordinator.py`,
  `test_coordinator_integration.py`, and `test_roadtrip.py`.
- Integration test history (`_HISTORY` / `_BASE_TS`) must use
  `datetime.now(UTC)` — never a hardcoded date — so entries always fall within
  the 7-day rolling window regardless of when tests run.
- `CONF_DRY_RUN: True` is set in all test coordinators by default so no real
  HA service calls are made. Tests that need to exercise device control bypass
  dry-run and mock `hass.services.async_call` with `AsyncMock`.

---

## Repository Layout

```
custom_components/carbon_aware_ev_charging/
├── __init__.py          Setup/unload, storage cleanup, options listener
├── manifest.json        HACS metadata — single source of truth for VERSION
├── const.py             All constants, thresholds, defaults, STATUS_MAP
├── coordinator.py       DataUpdateCoordinator: poll → stats → decision → control
├── config_flow.py       Three-step UI wizard + options flow
├── sensor.py            Z-score, charging status, charge rate/current sensors
├── binary_sensor.py     ev_connected binary sensor
├── select.py            ev_charge_mode and ev_carbon_mode selects
├── number.py            ev_departure_hour number entity
├── switch.py            Fallback-window enable/disable switches
├── base_entity.py       Shared CoordinatorEntity base class
├── diagnostics.py       HA diagnostics support
├── strings.json         UI string keys
└── translations/en.json English translations

tests/
├── conftest.py                      auto_enable_custom_integrations fixture
├── test_coordinator.py              Unit tests: Z-score math, decision branches
├── test_coordinator_integration.py  Integration tests: full HA hass fixture
├── test_roadtrip.py                 Roadtrip prep + charge-limit tests
├── test_config_flow.py              Config and options flow tests
├── test_entities.py                 Sensor/select/number/binary-sensor tests
├── test_init.py                     Setup, unload, migrate, storage tests
└── test_diagnostics.py              Diagnostics redaction tests
```

---

## Architecture

### Coordinator update pipeline (every 5 min + reactive on state changes)

1. `_resolve_config()` — merge `entry.data` + `entry.options` with `PREFERENCE_DEFAULTS`
2. `_read_sensors()` — parse CO₂, fossil %, charger state, power sensor
3. `_update_statistics()` — append to deques, prune to time window, compute Z-score
4. `_evaluate_charging()` — determine `status_enum` and `predicted_state`
5. `_control_devices()` — actuate charger switch and LED (dry-run, dwell, cooldown)

### Decision chain priority (highest wins)

```
force_off              → STATUS_FORCED_OFF      (paused)
force_on               → STATUS_OVERRIDE        (charging)
carbon gate open       → STATUS_LOW_CARBON      (charging)
roadtrip prep window   → STATUS_ROADTRIP_PREP   (charging)
departure prep window  → STATUS_DEPARTURE_PREP  (charging)
unavail + fallback win → STATUS_FALLBACK         (charging)
data stale             → STATUS_DATA_STALE      (paused)
data unavailable       → STATUS_WAITING_FOR_DATA (paused)
fossil ≥ 75%           → STATUS_FOSSIL_HIGH     (paused)
z_score ≥ threshold    → STATUS_GRID_DIRTY      (paused)
car not connected      → STATUS_NOT_CONNECTED   (paused, overlaid last)
```

`STATUS_MAP` in `const.py` maps `status_enum → (predicted_state, chargeable)`.
Never duplicate this logic — always extend `STATUS_MAP`.

### Key constants

| Constant | Value | Purpose |
|---|---|---|
| `THRESHOLD_LENIENT` | `0.92` | Z-score gate (~82% of hours) |
| `THRESHOLD_MODERATE` | `0.47` | Z-score gate (~68% of hours) |
| `THRESHOLD_STRICT` | `-0.18` | Z-score gate (~43% of hours) |
| `FOSSIL_HARD_FLOOR` | `75.0` | Max fossil % before gate forced closed |
| `HYSTERESIS_SIGMA` | `0.4` | Added to threshold when charger already on |
| `MIN_DWELL_MINUTES` | `15` | Minimum on-time before turning charger off |
| `MIN_COOLDOWN_MINUTES` | `10` | Minimum off-time before turning charger on |

### Entity unique ID suffixes — never change after release

`{entry.entry_id}_{suffix}`. Changing a suffix orphans the entity in HA.

---

## Commit Convention (Conventional Commits → semantic-release)

```
feat: add X       → minor bump    fix: correct Y  → patch bump
feat!: break Z    → major bump    chore/docs/test/refactor → no release
```

---

## Adding a New Entity

1. Add `ENTITY_ID_<NAME> = "<suffix>"` to `const.py`. Never change it post-release.
2. Add platform to `PLATFORMS` in `const.py` if new platform type.
3. Implement entity inheriting `CarbonAwareEVChargingEntity` from `base_entity.py`.
4. Register in platform `async_setup_entry`.
5. Add translation keys to `strings.json` and `translations/en.json`.
6. Write tests.

## Modifying the Decision Logic

1. Add `STATUS_*` constant to `const.py`.
2. Update `CHARGING_STATUSES` list and `STATUS_MAP` dict in `const.py`.
3. Add translation keys in `strings.json` and `translations/en.json`.
4. Update `_evaluate_charging` in `coordinator.py`.
5. Add test cases in `tests/test_coordinator.py`.

---

## Known Quirks

- `entry.data` entity IDs are immutable after setup — hardware config is
  separated from preferences intentionally.
- Z-score needs ≥ 2 readings; statistically meaningful only after ~7 days.
  During warmup the carbon gate defaults `False` (falls back to scheduled windows).
- When `stdev_7d == 0` (all readings identical), `z_score = 0.0` by definition.
- Recorder backfill runs only on first install (empty deques). Subsequent
  restarts restore from `hass.helpers.storage`.
- Config entry schema version is `1`. Breaking changes to `entry.data` require
  an `async_migrate_entry` path in `__init__.py`.
