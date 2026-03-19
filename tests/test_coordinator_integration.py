"""Integration-level tests for EVCarbonCoordinator._async_update_data.

These tests exercise the coordinator against a live in-test HA instance so the
full _async_update_data path is covered: state reads, rolling stats, Z-score
computation, decision branches, and device control calls.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.carbon_aware_ev_charging.const import (
    CHARGE_MODE_AUTO,
    CHARGE_MODE_FORCE_OFF,
    CHARGE_MODE_FORCE_ON,
    CONF_CARBON_MODE,
    CONF_CHARGE_MODE,
    CONF_CHARGER_CONNECTED_ATTR,
    CONF_CHARGER_NOT_CONNECTED_VALUE,
    CONF_CHARGER_SWITCH,
    CONF_CO2_SENSOR,
    CONF_DEPARTURE_DAYS,
    CONF_DEPARTURE_HOUR,
    CONF_DRY_RUN,
    CONF_FOSSIL_SENSOR,
    CONF_LED_EFFECT_SELECT,
    CONF_LED_LIGHT,
    CONF_NOTIFY_SERVICE,
    DEQUE_7D,
    STATE_CARBON,
    STATE_OVERRIDE,
    STATE_PAUSED,
    STATE_SCHEDULED,
)
from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonCoordinator

# ── Deterministic history: mean ≈ 198.5, stdev ≈ 8.66 — passes warmup guards ─
_HISTORY_VALS = [200 + (i % 30 - 15) for i in range(100)]
_BASE_TS = datetime(2026, 3, 16, 8, 0, tzinfo=timezone.utc).timestamp()
_HISTORY = [(_BASE_TS - i * 300, float(v)) for i, v in enumerate(_HISTORY_VALS)]


# ── Fixture helpers ────────────────────────────────────────────────────────────


def _make_coord(
    hass: HomeAssistant,
    options_overrides: dict | None = None,
    data_overrides: dict | None = None,
) -> EVCarbonCoordinator:
    """Build a bare coordinator (bypasses DataUpdateCoordinator machinery)."""
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = {
        CONF_CO2_SENSOR: "sensor.co2",
        CONF_FOSSIL_SENSOR: "sensor.fossil",
        CONF_CHARGER_SWITCH: "switch.charger",
        CONF_CHARGER_CONNECTED_ATTR: "icon_name",
        CONF_CHARGER_NOT_CONNECTED_VALUE: "CarNotConnected",
        **(data_overrides or {}),
    }
    entry.options = {
        CONF_CARBON_MODE: "Moderate",
        CONF_CHARGE_MODE: CHARGE_MODE_AUTO,
        CONF_DEPARTURE_HOUR: 5,
        CONF_DEPARTURE_DAYS: ["2", "3"],
        CONF_DRY_RUN: True,  # dry-run by default; no real service calls
        CONF_NOTIFY_SERVICE: "",
        **(options_overrides or {}),
    }

    coord = EVCarbonCoordinator.__new__(EVCarbonCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord.logger = MagicMock()
    coord._deque_7d = deque(_HISTORY, maxlen=DEQUE_7D)
    coord._deque_30d = deque(_HISTORY, maxlen=DEQUE_7D)
    coord._last_z_score = None
    coord.last_update_success = True
    return coord


def _set_states(
    hass: HomeAssistant,
    co2: str = "150",
    fossil: str = "30",
    charger_state: str = "off",
    charger_attrs: dict | None = None,
) -> None:
    hass.states.async_set("sensor.co2", co2)
    hass.states.async_set("sensor.fossil", fossil)
    hass.states.async_set(
        "switch.charger",
        charger_state,
        charger_attrs or {"icon_name": "CarConnected"},
    )


async def _run(coord: EVCarbonCoordinator):
    """Run _async_update_data with the history-save coroutine stubbed out."""
    with patch.object(coord, "_async_save_history", new_callable=AsyncMock):
        return await coord._async_update_data()


def _mock_services(coord: EVCarbonCoordinator, hass: HomeAssistant) -> AsyncMock:
    """Replace coord.hass with a fake that records service calls.

    Python 3.14 makes ServiceRegistry.async_call read-only so patch.object
    cannot be used directly.  This helper swaps coord.hass for a lightweight
    mock that still exposes the real state registry for sensor reads.
    """
    svc = AsyncMock()
    fake = MagicMock()
    fake.states = hass.states
    fake.services.async_call = svc

    def _discard(coro):
        """Close the coroutine to prevent 'never awaited' warnings."""
        try:
            coro.close()
        except Exception:
            pass

    fake.async_create_task = _discard
    coord.hass = fake
    return svc


# ── Tests ──────────────────────────────────────────────────────────────────────


async def test_clean_grid_carbon_state(hass: HomeAssistant) -> None:
    """Clean CO2 + low fossil → STATE_CARBON, should_charge=True."""
    _set_states(hass, co2="150", fossil="30")
    data = await _run(_make_coord(hass))

    assert data.co2 == 150.0
    assert data.fossil_pct == 30.0
    assert data.z_score is not None
    assert data.z_score < 0  # 150 well below mean ~198
    assert data.carbon_good is True
    assert data.is_connected is True
    assert data.predicted_state == STATE_CARBON
    assert data.should_charge is True


async def test_dirty_grid_paused(hass: HomeAssistant) -> None:
    """High CO2 → carbon_good=False → STATE_PAUSED."""
    _set_states(hass, co2="260", fossil="70")
    data = await _run(_make_coord(hass))

    assert data.z_score is not None
    assert data.z_score > 0.47  # above Moderate threshold
    assert data.carbon_good is False
    assert data.predicted_state == STATE_PAUSED
    assert data.should_charge is False


async def test_force_on_mode(hass: HomeAssistant) -> None:
    """force_on → STATE_OVERRIDE regardless of grid cleanliness."""
    _set_states(hass, co2="260", fossil="70")
    data = await _run(_make_coord(hass, {CONF_CHARGE_MODE: CHARGE_MODE_FORCE_ON}))

    assert data.predicted_state == STATE_OVERRIDE
    assert data.should_charge is True  # car is connected


async def test_force_off_blocks_clean_grid(hass: HomeAssistant) -> None:
    """force_off → STATE_PAUSED even when grid is clean."""
    _set_states(hass, co2="150", fossil="20")
    data = await _run(_make_coord(hass, {CONF_CHARGE_MODE: CHARGE_MODE_FORCE_OFF}))

    assert data.predicted_state == STATE_PAUSED
    assert data.should_charge is False


async def test_fossil_hard_floor(hass: HomeAssistant) -> None:
    """Fossil % above 75 → carbon_good=False even with a low Z-score."""
    _set_states(hass, co2="150", fossil="80")
    data = await _run(_make_coord(hass))

    assert data.z_score is not None
    assert data.z_score < 0  # good Z-score
    assert data.carbon_good is False  # fossil floor blocks it
    assert data.predicted_state == STATE_PAUSED


async def test_car_not_connected(hass: HomeAssistant) -> None:
    """Clean grid but car not plugged in → is_connected=False, should_charge=False."""
    _set_states(hass, co2="150", fossil="20", charger_attrs={"icon_name": "CarNotConnected"})
    data = await _run(_make_coord(hass))

    assert data.is_connected is False
    assert data.carbon_good is True
    assert data.should_charge is False


async def test_co2_unavailable_fallback_window(hass: HomeAssistant) -> None:
    """Unavailable CO2 + fallback-window hour (23:00) → STATE_SCHEDULED."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})
    coord = _make_coord(hass)

    # Monday 23:00 UTC → fallback_window=True (hour >= 22)
    fake_now = datetime(2026, 3, 16, 23, 0, tzinfo=timezone.utc)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.carbon_data_unavailable is True
    assert data.predicted_state == STATE_SCHEDULED


async def test_co2_unavailable_outside_window(hass: HomeAssistant) -> None:
    """Unavailable CO2 outside all fallback windows → STATE_PAUSED."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})
    coord = _make_coord(hass)

    # Monday (weekday=0) 09:00 UTC — not in any fallback window or departure day
    fake_now = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.carbon_data_unavailable is True
    assert data.predicted_state == STATE_PAUSED


async def test_charger_turned_on_not_dry_run(hass: HomeAssistant) -> None:
    """dry_run=False + clean grid + car connected → switch.turn_on is called."""
    _set_states(hass, co2="150", fossil="20")
    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    svc = _mock_services(coord, hass)

    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 1
    assert switch_on[0].args[2]["entity_id"] == "switch.charger"


async def test_charger_turned_off_after_dwell(hass: HomeAssistant) -> None:
    """dry_run=False + dirty grid + charger on + dwell met → switch.turn_off called."""
    hass.states.async_set("sensor.co2", "260")
    hass.states.async_set("sensor.fossil", "70")
    hass.states.async_set("switch.charger", "on", {"icon_name": "CarConnected"})
    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    svc = _mock_services(coord, hass)

    # Advance the coordinator's view of time 20 min into the future so dwell is met
    from homeassistant.util import dt as real_dt
    future = real_dt.utcnow() + timedelta(minutes=20)
    future_local = real_dt.now() + timedelta(minutes=20)

    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = future
        mock_dt.now.return_value = future_local
        await _run(coord)

    switch_off = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_off")]
    assert len(switch_off) == 1


async def test_notification_sent_on_charge_start(hass: HomeAssistant) -> None:
    """Notification is sent when charger is turned on and notify_service is set."""
    _set_states(hass, co2="150", fossil="20")
    coord = _make_coord(
        hass, {CONF_DRY_RUN: False, CONF_NOTIFY_SERVICE: "notify.test_svc"}
    )
    svc = _mock_services(coord, hass)

    await _run(coord)

    notify_calls = [c for c in svc.call_args_list if c.args[:2] == ("notify", "test_svc")]
    assert len(notify_calls) == 1
    assert "Low-Carbon" in notify_calls[0].args[2]["title"]


async def test_led_called_with_correct_colour(hass: HomeAssistant) -> None:
    """LED light is set to green (STATE_CARBON) when grid is clean."""
    _set_states(hass, co2="150", fossil="20")
    hass.states.async_set("light.led", "off")
    hass.states.async_set("select.led_effect", "Middle Rising")

    coord = _make_coord(
        hass,
        data_overrides={CONF_LED_LIGHT: "light.led", CONF_LED_EFFECT_SELECT: "select.led_effect"},
    )

    svc = _mock_services(coord, hass)
    await _run(coord)

    light_calls = [c for c in svc.call_args_list if c.args[0] == "light"]
    assert len(light_calls) == 1
    assert light_calls[0].args[2]["hs_color"] == [120, 80]  # green for carbon

    effect_calls = [c for c in svc.call_args_list if c.args[0] == "select"]
    assert len(effect_calls) == 1
    assert effect_calls[0].args[2]["option"] == "Middle Rising"


async def test_warmup_z_score_is_none(hass: HomeAssistant) -> None:
    """Empty history (warmup) → z_score is None."""
    _set_states(hass)
    coord = _make_coord(hass)
    coord._deque_7d = deque(maxlen=DEQUE_7D)   # empty — warmup
    coord._deque_30d = deque(maxlen=DEQUE_7D)
    coord._last_z_score = None

    data = await _run(coord)
    assert data.z_score is None


async def test_charge_current_from_charger_attribute(hass: HomeAssistant) -> None:
    """charge_current_a is populated from the charger switch's charging_rate attr."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "on", {"icon_name": "CarConnected", "charging_rate": 16})

    data = await _run(_make_coord(hass))
    assert data.charge_current_a == 16


async def test_rolling_stats_populated(hass: HomeAssistant) -> None:
    """After a successful update, mean_7d / stdev_7d / z_score are non-None."""
    _set_states(hass, co2="200", fossil="40")
    data = await _run(_make_coord(hass))

    assert data.mean_7d is not None
    assert data.stdev_7d is not None
    assert data.mean_30d is not None
    assert data.stdev_30d is not None
    assert data.z_score is not None


# ── Stale-data detection ──────────────────────────────────────────────────────


async def test_fresh_data_not_stale(hass: HomeAssistant) -> None:
    """Freshly updated sensor states → data_stale=False, carbon_data_unavailable=False."""
    _set_states(hass, co2="150", fossil="30")
    data = await _run(_make_coord(hass))

    assert data.data_stale is False
    assert data.carbon_data_unavailable is False


async def test_stale_co2_marks_data_unavailable(hass: HomeAssistant) -> None:
    """CO2 sensor not updated for >30 min → data_stale=True, carbon_data_unavailable=True."""
    _set_states(hass, co2="150", fossil="30")

    # Backdate last_updated on both sensors to 40 min ago
    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    data = await _run(_make_coord(hass))

    assert data.data_stale is True
    assert data.carbon_data_unavailable is True


async def test_stale_fossil_marks_data_unavailable(hass: HomeAssistant) -> None:
    """Fossil sensor stale while CO2 is fresh → data_stale=True."""
    _set_states(hass, co2="150", fossil="30")

    # Only backdate the fossil sensor
    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.fossil").last_updated = stale_time

    data = await _run(_make_coord(hass))

    assert data.data_stale is True
    assert data.carbon_data_unavailable is True


async def test_stale_data_triggers_fallback_window(hass: HomeAssistant) -> None:
    """Stale data during a fallback window → STATE_SCHEDULED (not STATE_CARBON)."""
    _set_states(hass, co2="150", fossil="30")

    # Backdate sensors to make them stale
    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Use real utcnow (staleness check works) but fake now() at 23:00 for fallback window
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 23, 0, tzinfo=timezone.utc)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        data = await _run(coord)

    assert data.data_stale is True
    assert data.carbon_data_unavailable is True
    assert data.predicted_state == STATE_SCHEDULED


async def test_stale_data_outside_window_paused(hass: HomeAssistant) -> None:
    """Stale data outside any fallback window → STATE_PAUSED."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Real utcnow for staleness, fake local at 09:00 Monday (no window/departure)
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)  # Monday
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        data = await _run(coord)

    assert data.data_stale is True
    assert data.predicted_state == STATE_PAUSED


async def test_stale_status_reason(hass: HomeAssistant) -> None:
    """Stale data produces a specific status_reason mentioning staleness."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Real utcnow for staleness, fake local at 09:00 (no window → paused with stale reason)
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        data = await _run(coord)

    assert "stale" in data.status_reason.lower()


async def test_unavailable_sensor_not_checked_for_staleness(hass: HomeAssistant) -> None:
    """Sensors in 'unavailable' state are not flagged as stale (they're already handled)."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(hass)

    # Even 40 min in the future, unavailable sensors should not trigger data_stale
    from homeassistant.util import dt as real_dt

    fake_now = real_dt.utcnow() + timedelta(minutes=40)
    fake_local = real_dt.now() + timedelta(minutes=40)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_local
        data = await _run(coord)

    assert data.data_stale is False  # not stale — just unavailable
    assert data.carbon_data_unavailable is True  # still unavailable though
