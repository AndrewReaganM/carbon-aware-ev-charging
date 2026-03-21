# Refactor Notes

Code smells, design issues, and improvement ideas identified during review.
Issues marked ✅ are resolved.

---

## Priority Summary

| Priority | # | Issue | Status |
|---|---|---|---|
| High | 1 | Departure-prep cross-midnight bug | ✅ Fixed |
| High | 2 | Bare `except Exception` swallows backfill errors | ✅ Fixed |
| High | 5 | Sync file read at module import | ✅ Fixed |
| Medium | 3 | Fire-and-forget save tasks | ✅ Fixed |
| Medium | 4 | Double `utcnow()` call in `_read_sensors` | ✅ Fixed |
| Medium | 10 | Identical switch class duplication | Open |
| Medium | 11 | Duplicated config flow prefs schema | Open |
| Medium | 13 | Dead `if False else` in tests | Open |
| Medium | 14 | Test re-implements production logic | Open |
| Low | 6 | `@cached_property` fragility on `device_info` | Open |
| Low | 7 | `setattr` bypasses type checking | Open |
| Low | 8 | Deprecated `OptionsFlow.__init__` pattern | Open |
| Low | 9 | Diagnostics couples to private coordinator API | Open |
| Low | 12 | Duplicated test helper factories | Open |
| Low | 15 | `pyproject.toml` housekeeping | Open |
| Low | 16 | Inconsistent `self._data` usage in `binary_sensor.py` | Open |

---

## Design Issues

### ✅ 1. Departure-prep cross-midnight weekday check (`coordinator.py:659`)

**Problem:** When the departure-prep window crosses midnight (e.g. `departure_hour=1` → window `[22, 01)`),
hours after midnight fall on the next calendar day. The old code checked `weekday in cfg.departure_days`,
so a user with `departure_days=["3"]` (Thursday) would see the coordinator fail to trigger prep at
Friday 00:00–00:59, requiring the awkward workaround of adding Friday to their departure days.

**Fix applied:** When `departure_prep_start > departure_hour` (window wraps midnight) and
`hour < departure_hour` (post-midnight portion), check `(weekday - 1) % 7 in cfg.departure_days`
instead — i.e. look at *yesterday's* departure day. The user only needs to configure the actual
departure day.

**Files changed:** `coordinator.py`, `tests/test_coordinator_integration.py`

---

### 2. Bare `except Exception` in `_async_backfill_from_recorder` (`coordinator.py:298`) — ✅ Fixed

**Fix applied:** Narrowed to `except (OSError, RuntimeError, sqlite3.DatabaseError)` and
upgraded log level from `debug` to `warning` so recorder failures are visible. Added stdlib
`import sqlite3`. Added test `test_backfill_handles_recorder_query_error` covering the new path.

---

### 3. Fire-and-forget `async_create_task` for history saves (`coordinator.py:341`, `584`) — ✅ Fixed

**Fix applied:** Replaced both `async_create_task(self._async_save_history())` calls with
`async_create_background_task(..., name)`. Updated `_mock_services` in
`test_coordinator_integration.py` to discard `async_create_background_task` coroutines,
eliminating the unawaited-coroutine `RuntimeWarning` on Python 3.14.

---

### 4. Double `dt_util.utcnow()` call in `_read_sensors` (`coordinator.py:465–466`) — ✅ Fixed

**Fix applied:** Captured `now = dt_util.utcnow()` once and derived both thresholds from it.

---

### 5. Synchronous file read at module import (`__init__.py:18`) — ✅ Fixed

**Fix applied:** Removed `json.loads(Path(...).read_text())` at module level. Added `VERSION = "1.7.0"`
to `const.py` and imported it in `__init__.py`, replacing `_MANIFEST.get("version", "unknown")`
with the constant. Removed `json`, `Path` imports from `__init__.py`.

---

### 6. `@cached_property` fragility on `device_info` (`base_entity.py:23`)

**Problem:** `cached_property` writes to the instance's `__dict__`. If a future HA version adds
`__slots__` to an entity base class, this will raise `AttributeError` at runtime. HA's own
integrations use `@property` for `device_info`.

**Fix idea:** Replace `@cached_property` with `@property`. `DeviceInfo` is a small dict-like;
constructing it on each property access is negligible.

---

### 7. `setattr` with string keys for instance attributes (`coordinator.py:540–566`)

**Problem:** Unavailability timestamps are read/written via `getattr(self, attr)` /
`setattr(self, attr, ...)` with string attribute names like `"_co2_unavailable_since"`. Static
type checkers cannot verify these attributes exist, and a rename will silently break diagnostics.

**Fix idea:** Replace with a typed dict:
```python
self._unavailable_since: dict[str, datetime | None] = {}
```

---

### 8. Deprecated `OptionsFlow.__init__` pattern (`config_flow.py:253`)

**Problem:** `EVCarbonChargerOptionsFlow.__init__` manually stores `self._config_entry`. In
HA 2024.x the base `OptionsFlow` class provides `self.config_entry` directly; storing it
separately is redundant and generates a deprecation warning on newer HA versions.

**Fix idea:** Remove the constructor and replace all `self._config_entry` references with
`self.config_entry`.

---

### 9. Diagnostics couples directly to private coordinator API (`diagnostics.py:27–42`)

**Problem:** `diagnostics.py` accesses 7 private coordinator attributes directly
(`coordinator._last_z_score`, `coordinator._deque_7d`, etc.). A rename in `coordinator.py`
won't be caught by the type checker — diagnostics silently returns `None` instead of failing.

**Fix idea:** Add a `diagnostic_info(self) -> dict` method to `EVCarbonCoordinator` and call
that from `diagnostics.py`.

---

## Code Duplication

### 10. `EvFallbackWindowSwitch` and `EvOptionSwitch` are identical (`switch.py`)

**Problem:** Both classes have the same `__init__`, `is_on`, `async_turn_on`, and
`async_turn_off`. Any bug fix must be applied twice.

**Fix idea:** Delete `EvFallbackWindowSwitch`. It does nothing `EvOptionSwitch` doesn't already
do — use `EvOptionSwitch` for all three switch entities.

---

### 11. Config flow prefs schema duplicated verbatim (`config_flow.py`)

**Problem:** The entire `vol.Schema({...})` block for preferences appears in both
`async_step_prefs` (initial wizard) and `async_step_init` (options flow), differing only in
where defaults come from.

**Fix idea:** Extract a `_prefs_schema(defaults: Callable[[str], Any]) -> vol.Schema` helper
to replace both occurrences.

---

### 12. Duplicated coordinator factory helpers in tests

**Problem:** `test_coordinator.py` defines `_make_coordinator` and
`test_coordinator_integration.py` defines `_make_coord` — near-duplicates with subtly different
shapes (e.g. one populates `_co2_unavailable_since`, the other doesn't). Divergence here can
mask bugs.

**Fix idea:** Move a single canonical `make_coordinator` fixture into `conftest.py`.

---

## Test Issues

### 13. Dead `if False else` code in `test_init.py:154–165`

**Problem:**
```python
patch.object(...)
if False
else patch("...EVCarbonCoordinator._async_backfill_from_recorder", ...)
```
The `patch.object(...)` branch is unreachable — leftover scaffolding from a refactor.

**Fix idea:** Simplify to just the `patch(...)` call.

---

### 14. `TestPredictedState._predict()` duplicates production logic (`test_coordinator.py:155–182`)

**Problem:** The test reimplements the decision chain locally rather than calling the real
`_evaluate_charging`. If production logic changes, these tests continue passing while
actual behaviour diverges.

**Fix idea:** Drive `TestPredictedState` through the actual coordinator method, as
`test_coordinator_integration.py` already does.

---

## Minor / Style

### 15. `pyproject.toml` housekeeping

- `[project.optional-dependencies]` and `[dependency-groups]` both declare dev dependencies
  with different version pins — pick one canonical source.
- `"main.py"` listed in `[tool.ruff.lint.per-file-ignores]` — no `main.py` exists in the repo.
- `S311` (non-crypto random) suppressed globally — there is no `random` usage in this codebase.

---

### 16. Inconsistent `self._data` usage in `binary_sensor.py`

**Problem:** `EvConnectedBinarySensor.is_on` accesses `self.coordinator.data.is_connected`
directly (`binary_sensor.py:49`), while `EvLowCarbonNowBinarySensor` uses the `self._data`
convenience property defined in `base_entity.py`. Inconsistent within the same file.

**Fix idea:** Change line 49 to `self._data.is_connected`.
