"""Integration-level tests for EVCarbonCoordinator._async_update_data.

These tests exercise the coordinator against a live in-test HA instance so the
full _async_update_data path is covered: state reads, rolling stats, Z-score
computation, decision branches, and device control calls.
"""

from __future__ import annotations

import contextlib
from collections import deque
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound

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
    CONF_FALLBACK_WINDOW_1_ENABLED,
    CONF_FALLBACK_WINDOW_1_END,
    CONF_FALLBACK_WINDOW_1_START,
    CONF_FALLBACK_WINDOW_2_ENABLED,
    CONF_FALLBACK_WINDOW_2_END,
    CONF_FALLBACK_WINDOW_2_START,
    CONF_FOSSIL_SENSOR,
    CONF_LED_EFFECT_SELECT,
    CONF_LED_LIGHT,
    CONF_NOTIFY_SERVICE,
    DEQUE_7D,
    DOMAIN,
    SENSOR_UNAVAILABLE_REPAIR_MINUTES,
    STATE_CARBON,
    STATE_OVERRIDE,
    STATE_PAUSED,
    STATE_SCHEDULED,
)
from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonCoordinator

# ── Deterministic history: mean ≈ 198.5, stdev ≈ 8.66 — passes warmup guards ─
_HISTORY_VALS = [200 + (i % 30 - 15) for i in range(100)]
_BASE_TS = datetime(2026, 3, 16, 8, 0, tzinfo=UTC).timestamp()
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
    coord._was_connected = False
    coord._stale_hard_count = 0
    coord._last_led_state = None
    coord._co2_unavailable_since = None
    coord._fossil_unavailable_since = None
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
        with contextlib.suppress(Exception):
            coro.close()

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
    fake_now = datetime(2026, 3, 16, 23, 0, tzinfo=UTC)
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
    fake_now = datetime(2026, 3, 16, 9, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.carbon_data_unavailable is True
    assert data.predicted_state == STATE_PAUSED


async def test_charger_turned_on_not_dry_run(hass: HomeAssistant) -> None:
    """dry_run=False + clean grid + car connected + cooldown met → switch.turn_on is called."""
    _set_states(hass, co2="150", fossil="20")

    # Backdate charger last_changed so cooldown is satisfied
    from homeassistant.util import dt as real_dt

    hass.states.get("switch.charger").last_changed = real_dt.utcnow() - timedelta(minutes=15)

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

    # Backdate charger so cooldown is met
    from homeassistant.util import dt as real_dt

    hass.states.get("switch.charger").last_changed = real_dt.utcnow() - timedelta(minutes=15)

    coord = _make_coord(hass, {CONF_DRY_RUN: False, CONF_NOTIFY_SERVICE: "notify.test_svc"})
    svc = _mock_services(coord, hass)

    await _run(coord)

    notify_calls = [c for c in svc.call_args_list if c.args[:2] == ("notify", "test_svc")]
    assert len(notify_calls) == 1
    assert "Low-Carbon" in notify_calls[0].args[2]["title"]


async def test_notify_failure_does_not_block_led(hass: HomeAssistant) -> None:
    """A failing notification must not prevent LED updates from running."""
    _set_states(hass, co2="150", fossil="20")
    hass.states.async_set("light.led", "off")
    hass.states.async_set("select.led_effect", "Middle Rising")

    from homeassistant.util import dt as real_dt

    hass.states.get("switch.charger").last_changed = real_dt.utcnow() - timedelta(minutes=15)

    coord = _make_coord(
        hass,
        options_overrides={CONF_DRY_RUN: False, CONF_NOTIFY_SERVICE: "notify.broken"},
        data_overrides={CONF_LED_LIGHT: "light.led", CONF_LED_EFFECT_SELECT: "select.led_effect"},
    )

    svc = _mock_services(coord, hass)

    # Make only the notify call raise; charger + LED calls succeed.
    async def _selective_failure(*args, **kwargs):
        if args[0] == "notify":
            raise ServiceNotFound("notify", "broken")
        return None

    svc.side_effect = _selective_failure

    await _run(coord)

    # Charger was turned on despite the notify failure
    charger_calls = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(charger_calls) == 1

    # LED was still updated despite the notify failure
    light_calls = [c for c in svc.call_args_list if c.args[0] == "light"]
    assert len(light_calls) == 1


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


async def test_led_shows_grid_state_when_disconnected(hass: HomeAssistant) -> None:
    """LED colour reflects grid decision even when car is not plugged in."""
    _set_states(hass, co2="150", fossil="20", charger_attrs={"icon_name": "CarNotConnected"})
    hass.states.async_set("light.led", "off")
    hass.states.async_set("select.led_effect", "Slow Blink")

    coord = _make_coord(
        hass,
        data_overrides={CONF_LED_LIGHT: "light.led", CONF_LED_EFFECT_SELECT: "select.led_effect"},
    )

    svc = _mock_services(coord, hass)
    await _run(coord)

    # Colour: green — grid is clean (what WOULD happen if plugged in)
    light_calls = [c for c in svc.call_args_list if c.args[0] == "light"]
    assert len(light_calls) == 1
    assert light_calls[0].args[2]["hs_color"] == [120, 80]  # green

    # Effect: Slow Blink — car not connected
    effect_calls = [c for c in svc.call_args_list if c.args[0] == "select"]
    assert len(effect_calls) == 1
    assert effect_calls[0].args[2]["option"] == "Slow Blink"


async def test_warmup_z_score_is_none(hass: HomeAssistant) -> None:
    """Empty history (warmup) → z_score is None."""
    _set_states(hass)
    coord = _make_coord(hass)
    coord._deque_7d = deque(maxlen=DEQUE_7D)  # empty — warmup
    coord._deque_30d = deque(maxlen=DEQUE_7D)
    coord._last_z_score = None

    data = await _run(coord)
    assert data.z_score is None


async def test_charge_current_from_charger_attribute(hass: HomeAssistant) -> None:
    """charge_current_a is populated from the charger switch's charging_rate attr."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set(
        "switch.charger",
        "on",
        {"icon_name": "CarConnected", "charging_rate": 16},
    )

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


# ── Cooldown after turn-off ───────────────────────────────────────────────────


async def test_cooldown_suppresses_turn_on(hass: HomeAssistant) -> None:
    """Charger just turned off (<10 min ago) + clean grid → turn_on suppressed."""
    # Charger is off (recently changed — last_changed is now)
    _set_states(hass, co2="150", fossil="20", charger_state="off")
    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    # Simulate that the car was already connected (not a fresh plug-in)
    coord._was_connected = True
    svc = _mock_services(coord, hass)

    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 0  # cooldown prevents turn-on


async def test_cooldown_allows_turn_on_after_elapsed(hass: HomeAssistant) -> None:
    """Charger off for >10 min + clean grid → turn_on is called."""
    _set_states(hass, co2="150", fossil="20", charger_state="off")

    # Backdate last_changed to 15 min ago so cooldown is met
    from homeassistant.util import dt as real_dt

    hass.states.get("switch.charger").last_changed = real_dt.utcnow() - timedelta(minutes=15)

    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    svc = _mock_services(coord, hass)

    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 1


async def test_force_on_bypasses_cooldown(hass: HomeAssistant) -> None:
    """force_on mode ignores cooldown and turns charger on immediately."""
    # Charger just turned off (last_changed = now)
    _set_states(hass, co2="150", fossil="20", charger_state="off")
    coord = _make_coord(hass, {CONF_DRY_RUN: False, CONF_CHARGE_MODE: CHARGE_MODE_FORCE_ON})
    svc = _mock_services(coord, hass)

    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 1  # force_on bypasses cooldown


async def test_cooldown_not_applied_on_first_start(hass: HomeAssistant) -> None:
    """When charger has been off for a long time (normal start), turn-on is not blocked."""
    _set_states(hass, co2="150", fossil="20", charger_state="off")

    # Backdate last_changed to 1 hour ago — well past cooldown
    from homeassistant.util import dt as real_dt

    hass.states.get("switch.charger").last_changed = real_dt.utcnow() - timedelta(hours=1)

    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    svc = _mock_services(coord, hass)

    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 1


async def test_cooldown_bypassed_on_reconnect(hass: HomeAssistant) -> None:
    """Car unplugged then replugged during cooldown → turn_on is allowed immediately."""
    # First update: car disconnected, charger off (just turned off — within cooldown)
    _set_states(
        hass,
        co2="150",
        fossil="20",
        charger_state="off",
        charger_attrs={"icon_name": "CarNotConnected"},
    )
    coord = _make_coord(hass, {CONF_DRY_RUN: False})
    svc = _mock_services(coord, hass)

    await _run(coord)
    # Car not connected → should_charge=False, no turn_on
    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 0

    # Second update: car plugged back in (transition disconnected → connected)
    # Charger is still recently off (within cooldown window)
    fake_hass = coord.hass
    fake_hass.states = hass.states  # restore real states for the re-plug
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    svc.reset_mock()
    await _run(coord)

    switch_on = [c for c in svc.call_args_list if c.args[:2] == ("switch", "turn_on")]
    assert len(switch_on) == 1  # cooldown bypassed because car just reconnected


# ── Tiered stale-data detection ────────────────────────────────────────────────


async def test_fresh_data_not_stale(hass: HomeAssistant) -> None:
    """Freshly updated sensor states → data_stale=False, carbon_data_unavailable=False."""
    _set_states(hass, co2="150", fossil="30")
    data = await _run(_make_coord(hass))

    assert data.data_stale is False
    assert data.carbon_data_unavailable is False


async def test_soft_stale_keeps_carbon_gate(hass: HomeAssistant) -> None:
    """Soft-stale (40 min) → data_stale=True but carbon gate still active."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    data = await _run(_make_coord(hass))

    assert data.data_stale is True
    assert data.carbon_data_unavailable is False  # soft stale — data still trusted


async def test_soft_stale_fossil_only(hass: HomeAssistant) -> None:
    """Fossil sensor soft-stale while CO2 is fresh → data_stale=True, carbon gate open."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=40)
    hass.states.get("sensor.fossil").last_updated = stale_time

    data = await _run(_make_coord(hass))

    assert data.data_stale is True
    assert data.carbon_data_unavailable is False


async def test_hard_stale_first_poll_not_unavailable(hass: HomeAssistant) -> None:
    """Hard-stale (70 min) on first poll → data_stale=True but NOT carbon_data_unavailable."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    data = await _run(_make_coord(hass))

    assert data.data_stale is True
    assert data.carbon_data_unavailable is False  # only 1 consecutive poll, need 3


async def test_hard_stale_consecutive_triggers_unavailable(hass: HomeAssistant) -> None:
    """3 consecutive hard-stale polls → carbon_data_unavailable=True."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Polls 1 and 2: not yet unavailable
    for _ in range(2):
        data = await _run(coord)
        assert data.data_stale is True
        assert data.carbon_data_unavailable is False

    # Poll 3: now unavailable
    data = await _run(coord)
    assert data.data_stale is True
    assert data.carbon_data_unavailable is True


async def test_hard_stale_counter_resets_on_fresh(hass: HomeAssistant) -> None:
    """Hard-stale counter resets when data becomes fresh again."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # 2 hard-stale polls
    await _run(coord)
    await _run(coord)

    # Data becomes fresh
    fresh_time = real_dt.utcnow()
    hass.states.get("sensor.co2").last_updated = fresh_time
    hass.states.get("sensor.fossil").last_updated = fresh_time
    data = await _run(coord)
    assert data.data_stale is False
    assert data.carbon_data_unavailable is False

    # Go stale again — counter starts from 0, so 1 hard-stale poll is not enough
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time
    data = await _run(coord)
    assert data.carbon_data_unavailable is False  # only 1 consecutive


async def test_stale_data_triggers_fallback_window(hass: HomeAssistant) -> None:
    """Hard-stale data (3 polls) during a fallback window → STATE_SCHEDULED."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Burn through the consecutive count
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 23, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        await _run(coord)
        await _run(coord)
        data = await _run(coord)

    assert data.data_stale is True
    assert data.carbon_data_unavailable is True
    assert data.predicted_state == STATE_SCHEDULED


async def test_stale_data_outside_window_paused(hass: HomeAssistant) -> None:
    """Hard-stale data (3 polls) outside any window → STATE_PAUSED."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Real utcnow for staleness, fake local at 09:00 Monday (no window/departure)
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 9, 0, tzinfo=UTC)  # Monday
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        await _run(coord)
        await _run(coord)
        data = await _run(coord)

    assert data.data_stale is True
    assert data.predicted_state == STATE_PAUSED


async def test_stale_status_reason(hass: HomeAssistant) -> None:
    """Hard-stale data (3 polls) produces a status_reason mentioning staleness."""
    _set_states(hass, co2="150", fossil="30")

    from homeassistant.util import dt as real_dt

    stale_time = real_dt.utcnow() - timedelta(minutes=70)
    hass.states.get("sensor.co2").last_updated = stale_time
    hass.states.get("sensor.fossil").last_updated = stale_time

    coord = _make_coord(hass)

    # Real utcnow for staleness, fake local at 09:00 (no window → paused with stale reason)
    real_utcnow = real_dt.utcnow()
    fake_local = datetime(2026, 3, 16, 9, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = real_utcnow
        mock_dt.now.return_value = fake_local
        await _run(coord)
        await _run(coord)
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


# ── Configurable fallback windows ──────────────────────────────────────────────


async def test_custom_fallback_window_activates(hass: HomeAssistant) -> None:
    """Custom window 1 set to 20:00–04:00 at hour 21 → STATE_SCHEDULED."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_FALLBACK_WINDOW_1_START: 20,
            CONF_FALLBACK_WINDOW_1_END: 4,
            CONF_FALLBACK_WINDOW_1_ENABLED: True,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Monday 21:00 → inside custom window 1
    fake_now = datetime(2026, 3, 16, 21, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED


async def test_custom_fallback_window_outside(hass: HomeAssistant) -> None:
    """Custom window 1 set to 20:00–04:00, hour 10 → STATE_PAUSED."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_FALLBACK_WINDOW_1_START: 20,
            CONF_FALLBACK_WINDOW_1_END: 4,
            CONF_FALLBACK_WINDOW_1_ENABLED: True,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Monday 10:00 → outside both windows
    fake_now = datetime(2026, 3, 16, 10, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


async def test_fallback_window_disabled(hass: HomeAssistant) -> None:
    """Both windows disabled → no fallback even during default window hours."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Monday 23:00 → would normally be in default window 1 (22–06)
    fake_now = datetime(2026, 3, 16, 23, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


async def test_fallback_window_2_activates(hass: HomeAssistant) -> None:
    """Window 1 disabled, window 2 at 12:00–16:00, hour 13 → STATE_SCHEDULED."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_START: 12,
            CONF_FALLBACK_WINDOW_2_END: 16,
            CONF_FALLBACK_WINDOW_2_ENABLED: True,
        },
    )

    fake_now = datetime(2026, 3, 16, 13, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED


# ── Departure prep bounded window ─────────────────────────────────────────────


async def test_departure_prep_inside_window(hass: HomeAssistant) -> None:
    """Unavailable data + Thu 03:00 with departure_hour=5 → departure_prep (window 02–05)."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 5,
            CONF_DEPARTURE_DAYS: ["3"],  # Thursday
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Thursday (weekday=3) 03:00 → inside prep window [02:00, 05:00)
    fake_now = datetime(2026, 3, 19, 3, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED
    assert data.status_enum == "departure_prep"


async def test_departure_prep_outside_window(hass: HomeAssistant) -> None:
    """Unavailable data + Thu 21:00 with departure_hour=5 → NOT departure_prep."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 5,
            CONF_DEPARTURE_DAYS: ["3"],  # Thursday
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Thursday (weekday=3) 21:00 → outside prep window [02:00, 05:00)
    fake_now = datetime(2026, 3, 19, 21, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


async def test_departure_prep_at_departure_hour_not_active(hass: HomeAssistant) -> None:
    """Departure hour itself is outside the prep window (end-exclusive)."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 5,
            CONF_DEPARTURE_DAYS: ["3"],
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Thursday 05:00 → at departure hour, window [02,05) is end-exclusive
    fake_now = datetime(2026, 3, 19, 5, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


async def test_departure_prep_wrong_day(hass: HomeAssistant) -> None:
    """Right hour but wrong day → no departure prep."""
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 5,
            CONF_DEPARTURE_DAYS: ["3"],  # Thursday only
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Monday (weekday=0) 03:00 → right hour window, wrong day
    fake_now = datetime(2026, 3, 16, 3, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


async def test_departure_prep_dirty_grid_still_charges(hass: HomeAssistant) -> None:
    """Dirty grid during prep window → departure_prep overrides grid_dirty."""
    # High CO2 + high fossil → carbon_good=False, data IS available
    _set_states(hass, co2="500", fossil="80", charger_attrs={"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 5,
            CONF_DEPARTURE_DAYS: ["3"],  # Thursday
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Thursday 03:00 → inside prep window, grid is dirty but prep wins
    fake_now = datetime(2026, 3, 19, 3, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED
    assert data.status_enum == "departure_prep"
    assert data.should_charge is True


async def test_departure_prep_midnight_wraparound(hass: HomeAssistant) -> None:
    """departure_hour=1 with 3h window → [22, 1) wraps midnight correctly.

    The user only configures Thursday as the departure day.  The coordinator
    must handle both the pre-midnight portion (Thu 22:00–23:59) and the
    post-midnight portion (Fri 00:00–00:59) without requiring the user to also
    add Friday to their departure_days.
    """
    hass.states.async_set("sensor.co2", "unavailable")
    hass.states.async_set("sensor.fossil", "unavailable")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarConnected"})

    coord = _make_coord(
        hass,
        options_overrides={
            CONF_DEPARTURE_HOUR: 1,
            CONF_DEPARTURE_DAYS: ["3"],  # Thursday only — no workaround needed
            CONF_FALLBACK_WINDOW_1_ENABLED: False,
            CONF_FALLBACK_WINDOW_2_ENABLED: False,
        },
    )

    # Thursday 23:00 → pre-midnight portion of [22, 1), weekday=3 (Thu)
    fake_now = datetime(2026, 3, 19, 23, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED
    assert data.status_enum == "departure_prep"

    # Friday 00:00 → post-midnight portion of [22, 1), weekday=4 (Fri).
    # With departure_days=["3"] (Thu only), the coordinator should recognise
    # this as "still Thursday's prep window" by checking yesterday's weekday.
    fake_now = datetime(2026, 3, 20, 0, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_SCHEDULED
    assert data.status_enum == "departure_prep"

    # Thursday 01:00 → at departure hour, OUTSIDE window [22, 1)
    fake_now = datetime(2026, 3, 19, 1, 0, tzinfo=UTC)
    with patch("custom_components.carbon_aware_ev_charging.coordinator.dt_util") as mock_dt:
        mock_dt.utcnow.return_value = fake_now
        mock_dt.now.return_value = fake_now
        data = await _run(coord)

    assert data.predicted_state == STATE_PAUSED


# ── Sensor availability repair issues ─────────────────────────────────────────


async def test_repair_issue_not_raised_before_threshold(hass: HomeAssistant) -> None:
    """No repair issue when sensor is unavailable for less than the threshold."""
    _set_states(hass, co2="unavailable", fossil="30")
    coord = _make_coord(hass)

    with patch(
        "custom_components.carbon_aware_ev_charging.coordinator.async_create_issue"
    ) as mock_create:
        await _run(coord)

    mock_create.assert_not_called()
    # tracking started
    assert coord._co2_unavailable_since is not None
    assert coord._fossil_unavailable_since is None


async def test_repair_issue_raised_after_threshold(hass: HomeAssistant) -> None:
    """Repair issue created once sensor is unavailable past the threshold."""
    _set_states(hass, co2="unavailable", fossil="30")
    coord = _make_coord(hass)

    # Pre-set tracking to just past threshold
    from homeassistant.util import dt as real_dt

    coord._co2_unavailable_since = real_dt.utcnow() - timedelta(
        minutes=SENSOR_UNAVAILABLE_REPAIR_MINUTES + 1,
    )

    with patch(
        "custom_components.carbon_aware_ev_charging.coordinator.async_create_issue"
    ) as mock_create:
        await _run(coord)

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    assert call_kwargs[0][1] == DOMAIN
    assert "sensor.co2" in call_kwargs[0][2]


async def test_repair_issue_dismissed_on_recovery(hass: HomeAssistant) -> None:
    """Repair issue dismissed when sensor recovers from unavailable."""
    _set_states(hass, co2="150", fossil="30")
    coord = _make_coord(hass)

    # Simulate that an issue was previously raised
    from homeassistant.util import dt as real_dt

    coord._co2_unavailable_since = real_dt.utcnow() - timedelta(minutes=60)

    with patch(
        "custom_components.carbon_aware_ev_charging.coordinator.async_delete_issue"
    ) as mock_delete:
        await _run(coord)

    mock_delete.assert_called_once_with(hass, DOMAIN, "sensor_unavailable_sensor.co2")
    assert coord._co2_unavailable_since is None


async def test_repair_issues_tracked_per_sensor(hass: HomeAssistant) -> None:
    """CO2 and fossil sensors are tracked independently."""
    _set_states(hass, co2="unavailable", fossil="unavailable")
    coord = _make_coord(hass)

    from homeassistant.util import dt as real_dt

    past = real_dt.utcnow() - timedelta(
        minutes=SENSOR_UNAVAILABLE_REPAIR_MINUTES + 1,
    )
    coord._co2_unavailable_since = past
    coord._fossil_unavailable_since = past

    with patch(
        "custom_components.carbon_aware_ev_charging.coordinator.async_create_issue"
    ) as mock_create:
        await _run(coord)

    assert mock_create.call_count == 2
    issue_ids = {c[0][2] for c in mock_create.call_args_list}
    assert "sensor_unavailable_sensor.co2" in issue_ids
    assert "sensor_unavailable_sensor.fossil" in issue_ids


# ── Deque timestamp pruning ───────────────────────────────────────────────────


async def test_old_entries_pruned_from_7d_deque(hass: HomeAssistant) -> None:
    """Entries older than 7 days are removed from _deque_7d on update."""
    _set_states(hass, co2="150", fossil="30")
    coord = _make_coord(hass)

    # Inject entries: 50 within 7d, 50 older than 7d
    now_ts = datetime.now(tz=UTC).timestamp()
    old_entries = [(now_ts - 8 * 86_400 + i * 60, 200.0) for i in range(50)]
    fresh_entries = [(now_ts - 3 * 86_400 + i * 60, 200.0) for i in range(50)]
    coord._deque_7d.clear()
    for e in old_entries + fresh_entries:
        coord._deque_7d.append(e)

    assert len(coord._deque_7d) == 100

    await _run(coord)

    # Old entries pruned; fresh kept + 1 new from the poll
    assert len(coord._deque_7d) == 51


async def test_old_entries_pruned_from_30d_deque(hass: HomeAssistant) -> None:
    """Entries older than 30 days are removed from _deque_30d on update."""
    _set_states(hass, co2="150", fossil="30")
    coord = _make_coord(hass)

    now_ts = datetime.now(tz=UTC).timestamp()
    old_entries = [(now_ts - 35 * 86_400 + i * 60, 200.0) for i in range(30)]
    fresh_entries = [(now_ts - 10 * 86_400 + i * 60, 200.0) for i in range(50)]
    coord._deque_30d.clear()
    for e in old_entries + fresh_entries:
        coord._deque_30d.append(e)

    assert len(coord._deque_30d) == 80

    await _run(coord)

    # Old entries pruned; fresh kept + 1 new from the poll
    assert len(coord._deque_30d) == 51


async def test_pruning_preserves_recent_entries(hass: HomeAssistant) -> None:
    """All entries within the time window survive pruning."""
    _set_states(hass, co2="150", fossil="30")
    coord = _make_coord(hass)

    now_ts = datetime.now(tz=UTC).timestamp()
    # All entries are within 1 day — well inside 7d window
    recent_entries = [(now_ts - 3600 + i * 60, 180.0 + i) for i in range(40)]
    coord._deque_7d.clear()
    for e in recent_entries:
        coord._deque_7d.append(e)

    await _run(coord)

    # 40 kept + 1 new = 41
    assert len(coord._deque_7d) == 41
