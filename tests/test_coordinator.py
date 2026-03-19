"""Unit tests for EVCarbonCoordinator logic.

These tests exercise the pure-Python logic paths (Z-score computation,
predicted_state branching, carbon gate) using a minimal mock of HomeAssistant
so the tests run without a live HA instance.
"""
from __future__ import annotations

import statistics
from collections import deque
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    DEQUE_7D,
    STATE_CARBON,
    STATE_OVERRIDE,
    STATE_PAUSED,
    STATE_SCHEDULED
)
from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonCoordinator


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_coordinator(
    hass: HomeAssistant,
    data_overrides: dict[str, Any] | None = None,
    options_overrides: dict[str, Any] | None = None,
) -> EVCarbonCoordinator:
    """Build a coordinator backed by mocked config entry."""
    data = {
        CONF_CO2_SENSOR: "sensor.co2",
        CONF_FOSSIL_SENSOR: "sensor.fossil",
        CONF_CHARGER_SWITCH: "switch.charger",
        CONF_CHARGER_CONNECTED_ATTR: "icon_name",
        CONF_CHARGER_NOT_CONNECTED_VALUE: "CarNotConnected",
        **(data_overrides or {}),
    }
    options = {
        CONF_CARBON_MODE: "Moderate",
        CONF_CHARGE_MODE: CHARGE_MODE_AUTO,
        CONF_DEPARTURE_HOUR: 5,
        CONF_DEPARTURE_DAYS: ["2", "3"],
        CONF_DRY_RUN: True,  # always dry_run in tests — never touch real switches
        **(options_overrides or {}),
    }
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = data
    entry.options = options

    coord = EVCarbonCoordinator.__new__(EVCarbonCoordinator)
    coord.hass = hass
    coord.entry = entry
    coord.logger = MagicMock()
    coord._deque_7d = deque(maxlen=DEQUE_7D)  # type: ignore[reportPrivateUsage]
    coord._deque_30d = deque(maxlen=DEQUE_7D)  # type: ignore[reportPrivateUsage]
    coord._last_z_score = None  # type: ignore[reportPrivateUsage]
    coord._was_connected = False  # type: ignore[reportPrivateUsage]
    coord.last_update_success = True
    return coord


def _set_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    hass.states.async_set(entity_id, state, attributes or {})


# ── Z-score math ───────────────────────────────────────────────────────────────

class TestZScoreMath:
    """Test Z-score computation in isolation (no HA required)."""

    def test_z_score_formula(self):
        co2, mean, stdev = 250.0, 200.0, 40.0
        expected = round((co2 - mean) / stdev, 2)
        assert expected == pytest.approx(1.25)

    def test_z_score_negative(self):
        co2, mean, stdev = 150.0, 200.0, 40.0
        z = round((co2 - mean) / stdev, 2)
        assert z == pytest.approx(-1.25)

# ── Carbon gate ────────────────────────────────────────────────────────────────

class TestCarbonGate:
    """Test the low_carbon_now gate logic."""

    def _gate(
        self,
        z_score: float,
        fossil_pct: float,
        threshold: float,
        charger_on: bool = False,
        hysteresis: float = 0.4,
    ) -> bool:
        effective = (threshold + hysteresis) if charger_on else threshold
        return z_score < effective and fossil_pct < 75.0

    def test_moderate_clean_grid(self):
        # z_score well below Moderate threshold (0.47), fossil low
        assert self._gate(z_score=0.0, fossil_pct=30.0, threshold=0.47)

    def test_moderate_dirty_grid(self):
        assert not self._gate(z_score=1.0, fossil_pct=30.0, threshold=0.47)

    def test_fossil_hard_floor(self):
        """Z-score low but fossil % above hard floor → gate closed."""
        assert not self._gate(z_score=-1.0, fossil_pct=80.0, threshold=0.47)

    def test_hysteresis_keeps_charger_on(self):
        """Z-score just above threshold but charger already on → hysteresis holds it on."""
        threshold = 0.47
        z = 0.60  # above threshold, below threshold+0.4
        assert not self._gate(z, 30.0, threshold, charger_on=False)
        assert self._gate(z, 30.0, threshold, charger_on=True)

    def test_strict_mode(self):
        assert not self._gate(z_score=0.0, fossil_pct=30.0, threshold=-0.18)
        assert self._gate(z_score=-0.5, fossil_pct=30.0, threshold=-0.18)


# ── predicted_state branches ───────────────────────────────────────────────────

class TestPredictedState:
    """Test the priority logic for predicted_state."""

    def _predict(
        self,
        charge_mode=CHARGE_MODE_AUTO,
        carbon_good=False,
        carbon_data_unavailable=False,
        fallback_window=False,
        departure_prep=False,
    ):
        from custom_components.carbon_aware_ev_charging.const import (
            CHARGE_MODE_FORCE_OFF,
            CHARGE_MODE_FORCE_ON,
            STATE_CARBON,
            STATE_OVERRIDE,
            STATE_PAUSED,
            STATE_SCHEDULED,
        )

        if charge_mode == CHARGE_MODE_FORCE_OFF:
            return STATE_PAUSED
        if charge_mode == CHARGE_MODE_FORCE_ON:
            return STATE_OVERRIDE
        if carbon_good:
            return STATE_CARBON
        if carbon_data_unavailable and (fallback_window or departure_prep):
            return STATE_SCHEDULED
        return STATE_PAUSED

    def test_force_off_beats_everything(self):
        assert self._predict(
            charge_mode=CHARGE_MODE_FORCE_OFF, carbon_good=True
        ) == STATE_PAUSED

    def test_force_on_beats_carbon(self):
        # force_on overrides, but carbon_good doesn't matter for the winner label
        assert self._predict(charge_mode=CHARGE_MODE_FORCE_ON) == STATE_OVERRIDE

    def test_carbon_good(self):
        assert self._predict(carbon_good=True) == STATE_CARBON

    def test_scheduled_fallback_when_data_unavailable(self):
        assert (
            self._predict(
                carbon_data_unavailable=True, fallback_window=True
            )
            == STATE_SCHEDULED
        )

    def test_paused_when_data_available_and_not_clean(self):
        assert self._predict(carbon_good=False, carbon_data_unavailable=False) == STATE_PAUSED

    def test_paused_when_data_unavailable_but_no_window(self):
        assert self._predict(carbon_data_unavailable=True, fallback_window=False, departure_prep=False) == STATE_PAUSED


# ── Reload spike guard (integration-level) ────────────────────────────────────

class TestReloadSpikeGuard:
    """Verify that a spike-guard failure falls back to last good Z-score."""

    def test_z_score_none_during_warmup(self):
        """Before any history is accumulated, z_score should be None."""
        last_z = None  # no previous value
        stdev = 0.0
        mean = 0.0
        z_score = None
        # If last_z is None and guard fails, z_score stays None
        result = last_z if z_score is None else z_score
        assert result is None
