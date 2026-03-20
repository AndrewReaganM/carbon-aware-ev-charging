"""Tests for Carbon-Aware EV Charging integration setup/unload lifecycle (__init__.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.carbon_aware_ev_charging import async_migrate_entry
from custom_components.carbon_aware_ev_charging.const import (
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
    CONF_NOTIFY_SERVICE,
    DOMAIN,
)

_VALID_DATA = {
    CONF_CO2_SENSOR: "sensor.co2",
    CONF_FOSSIL_SENSOR: "sensor.fossil",
    CONF_CHARGER_SWITCH: "switch.charger",
    CONF_CHARGER_CONNECTED_ATTR: "icon_name",
    CONF_CHARGER_NOT_CONNECTED_VALUE: "CarNotConnected",
}

_VALID_OPTIONS = {
    CONF_CARBON_MODE: "Moderate",
    CONF_CHARGE_MODE: "auto",
    CONF_DEPARTURE_HOUR: 5,
    CONF_DEPARTURE_DAYS: ["2", "3"],
    CONF_DRY_RUN: True,
    CONF_NOTIFY_SERVICE: "",
}


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """async_setup_entry registers coordinator and platforms; unload cleans up."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    with patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore:
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value=None)
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]

    # Unload
    result = await hass.config_entries.async_unload(entry.entry_id)
    assert result is True
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_entry_with_persisted_history(hass: HomeAssistant) -> None:
    """Persisted rolling history is restored from storage on first refresh."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    # Timestamps must be within the last 7 days to survive time-based pruning.
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    fake_history = [[now_ts - (50 - i) * 300, float(200 + i % 10)] for i in range(50)]

    with patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore:
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(
            return_value={
                "deque_7d": fake_history,
                "deque_30d": fake_history,
                "last_z_score": -0.25,
            }
        )
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        await hass.config_entries.async_setup(entry.entry_id)

    from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonCoordinator

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert isinstance(coordinator, EVCarbonCoordinator)
    # 50 restored + 1 appended during the first _async_update_data call
    assert len(coordinator._deque_7d) == 51
    # z_score is recalculated from the restored deque + fresh CO2 reading,
    # so it won't match the persisted value (-0.25).
    assert coordinator._last_z_score == pytest.approx(-1.5)

    await hass.config_entries.async_unload(entry.entry_id)


# ── Recorder backfill tests ───────────────────────────────────────────────────


def _make_fake_state(state_val: str, last_updated: datetime) -> MagicMock:
    """Build a minimal mock of a recorder State object."""
    s = MagicMock()
    s.state = state_val
    s.last_updated = last_updated
    return s


async def test_backfill_populates_empty_deques(hass: HomeAssistant) -> None:
    """When Store is empty, recorder history seeds the deques."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    now = datetime.now(tz=timezone.utc)
    # 20 states spread over the last 3 days (all within 7d window)
    fake_states = [
        _make_fake_state(str(180 + i), now - timedelta(hours=i * 3))
        for i in range(20)
    ]

    async def fake_backfill(self_coord):
        """Simulate a successful recorder backfill."""
        cutoff_7d = now - timedelta(days=7)
        self_coord._load_recorder_states(fake_states, cutoff_7d)

    with (
        patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore,
        patch.object(
            type(hass.data.get(DOMAIN, {}).get(entry.entry_id, object())),
            "_async_backfill_from_recorder",
            new=fake_backfill,
        ) if False else
        patch(
            "custom_components.carbon_aware_ev_charging.coordinator.EVCarbonCoordinator._async_backfill_from_recorder",
            new=fake_backfill,
        ),
    ):
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value=None)  # empty store
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        await hass.config_entries.async_setup(entry.entry_id)

    from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonCoordinator

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert isinstance(coordinator, EVCarbonCoordinator)
    # 20 from recorder + 1 from the first _async_update_data poll
    assert len(coordinator._deque_30d) == 21
    assert len(coordinator._deque_7d) == 21  # all within 7d

    await hass.config_entries.async_unload(entry.entry_id)


async def test_backfill_skips_unavailable_states(hass: HomeAssistant) -> None:
    """Recorder states with 'unavailable' or 'unknown' are filtered out."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    now = datetime.now(tz=timezone.utc)
    fake_states = [
        _make_fake_state("200", now - timedelta(hours=2)),
        _make_fake_state("unavailable", now - timedelta(hours=1)),
        _make_fake_state("unknown", now - timedelta(minutes=30)),
        _make_fake_state("210", now - timedelta(minutes=10)),
    ]

    async def fake_backfill(self_coord):
        cutoff_7d = now - timedelta(days=7)
        self_coord._load_recorder_states(fake_states, cutoff_7d)

    with (
        patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore,
        patch(
            "custom_components.carbon_aware_ev_charging.coordinator.EVCarbonCoordinator._async_backfill_from_recorder",
            new=fake_backfill,
        ),
    ):
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value=None)
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        await hass.config_entries.async_setup(entry.entry_id)

    coordinator = hass.data[DOMAIN][entry.entry_id]
    # 2 valid from recorder + 1 from live poll = 3
    assert len(coordinator._deque_30d) == 3

    await hass.config_entries.async_unload(entry.entry_id)


async def test_backfill_skipped_when_store_has_data(hass: HomeAssistant) -> None:
    """Recorder backfill does not run when Store already has history."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    fake_history = [[1_000_000_000 + i * 300, float(200 + i)] for i in range(10)]

    backfill_called = False

    async def spy_backfill(self_coord):
        nonlocal backfill_called
        backfill_called = True

    with (
        patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore,
        patch(
            "custom_components.carbon_aware_ev_charging.coordinator.EVCarbonCoordinator._async_backfill_from_recorder",
            new=spy_backfill,
        ),
    ):
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(
            return_value={
                "deque_7d": fake_history,
                "deque_30d": fake_history,
                "last_z_score": 0.0,
            }
        )
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        await hass.config_entries.async_setup(entry.entry_id)

    # Backfill should NOT have been called
    assert backfill_called is False

    await hass.config_entries.async_unload(entry.entry_id)


async def test_backfill_handles_recorder_not_running(hass: HomeAssistant) -> None:
    """Backfill gracefully no-ops when recorder is not running."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    # Don't patch backfill — let it run, but recorder isn't set up in test HA,
    # so get_instance will raise KeyError which the code catches gracefully.
    with patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore:
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value=None)
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        result = await hass.config_entries.async_setup(entry.entry_id)

    # Should still set up successfully, just without backfill
    assert result is True

    await hass.config_entries.async_unload(entry.entry_id)


# ── Reactive state-change refresh ─────────────────────────────────────────────


async def test_state_change_triggers_refresh(hass: HomeAssistant) -> None:
    """Changing a monitored sensor triggers an immediate coordinator refresh."""
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off", {"icon_name": "CarNotConnected"})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_DATA,
        options=_VALID_OPTIONS,
    )
    entry.add_to_hass(hass)

    with patch("custom_components.carbon_aware_ev_charging.coordinator.Store") as MockStore:
        store_inst = MagicMock()
        store_inst.async_load = AsyncMock(return_value=None)
        store_inst.async_save = AsyncMock()
        MockStore.return_value = store_inst

        await hass.config_entries.async_setup(entry.entry_id)

    coordinator = hass.data[DOMAIN][entry.entry_id]

    # Record how many updates have happened so far
    initial_count = coordinator.data is not None  # True after first refresh

    with patch.object(coordinator, "async_request_refresh") as mock_refresh:
        # Change CO2 sensor state
        hass.states.async_set("sensor.co2", "180")
        await hass.async_block_till_done()

        assert mock_refresh.call_count >= 1

    # Also verify charger state changes trigger a refresh
    with patch.object(coordinator, "async_request_refresh") as mock_refresh:
        hass.states.async_set("switch.charger", "on", {"icon_name": "CarConnected"})
        await hass.async_block_till_done()

        assert mock_refresh.call_count >= 1

    await hass.config_entries.async_unload(entry.entry_id)

    # After unload, state changes should NOT trigger refresh
    with patch.object(coordinator, "async_request_refresh") as mock_refresh:
        hass.states.async_set("sensor.co2", "999")
        await hass.async_block_till_done()

        assert mock_refresh.call_count == 0


# ── Config entry migration ────────────────────────────────────────────────────


async def test_migrate_entry_v1(hass: HomeAssistant) -> None:
    """VERSION 1 entries migrate without changes."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_VALID_DATA, options=_VALID_OPTIONS, version=1,
    )
    assert await async_migrate_entry(hass, entry) is True


async def test_migrate_entry_future_version_fails(hass: HomeAssistant) -> None:
    """Entries from a higher version cannot be downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_VALID_DATA, options=_VALID_OPTIONS, version=99,
    )
    assert await async_migrate_entry(hass, entry) is False
