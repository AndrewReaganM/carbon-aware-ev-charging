"""Tests for the diagnostics handler."""
from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.carbon_aware_ev_charging.const import DOMAIN
from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonData
from custom_components.carbon_aware_ev_charging.diagnostics import (
    async_get_config_entry_diagnostics,
)


def _make_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        "co2_sensor": "sensor.co2",
        "fossil_sensor": "sensor.fossil",
        "charger_switch": "switch.charger",
    }
    entry.options = {
        "carbon_mode": "Moderate",
        "charge_mode": "auto",
        "notify_service": "notify.mobile",
    }
    return entry


def _make_coordinator(entry: MagicMock, data: EVCarbonData | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = data or EVCarbonData(
        co2=180.0,
        fossil_pct=35.0,
        z_score=-0.5,
        mean_7d=200.0,
        stdev_7d=10.0,
        predicted_state="carbon",
        should_charge=True,
        carbon_good=True,
        status_enum="low_carbon",
        status_reason="Grid is clean",
    )
    coord._last_z_score = -0.5
    coord._deque_7d = deque(maxlen=2016)
    coord._deque_30d = deque(maxlen=8640)
    for i in range(100):
        coord._deque_7d.append((1e9 + i * 300, 200.0 + i))
        coord._deque_30d.append((1e9 + i * 300, 200.0 + i))
    coord._stale_hard_count = 0
    coord._was_connected = True
    coord.last_update_success = True
    coord._co2_unavailable_since = None
    coord._fossil_unavailable_since = None
    return coord


async def test_diagnostics_returns_expected_keys(hass: HomeAssistant) -> None:
    """Diagnostics output contains config, options, coordinator, and current_data."""
    entry = _make_entry()
    coord = _make_coordinator(entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert set(result.keys()) == {"config", "options", "coordinator", "current_data"}
    assert result["coordinator"]["deque_7d_size"] == 100
    assert result["coordinator"]["deque_30d_size"] == 100
    assert result["coordinator"]["last_z_score"] == -0.5
    assert result["current_data"]["status_enum"] == "low_carbon"
    assert result["current_data"]["co2"] == 180.0


async def test_diagnostics_redacts_notify_service(hass: HomeAssistant) -> None:
    """Notify service is redacted in options output."""
    entry = _make_entry()
    coord = _make_coordinator(entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["options"]["notify_service"] == "**REDACTED**"
    assert result["options"]["carbon_mode"] == "Moderate"  # non-sensitive preserved


async def test_diagnostics_handles_no_data(hass: HomeAssistant) -> None:
    """Diagnostics works when coordinator.data is None (before first refresh)."""
    entry = _make_entry()
    coord = _make_coordinator(entry, data=None)
    coord.data = None
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["current_data"]["co2"] is None
    assert result["current_data"]["status_enum"] is None
    assert result["coordinator"]["deque_7d_size"] == 100  # coordinator state still present
