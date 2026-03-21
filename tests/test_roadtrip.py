"""Tests for the Roadtrip Prep feature.

Covers:
- parse_roadtrip_title — all four title variants and edge cases
- RoadtripEvent.prep_start property
- _evaluate_charging roadtrip branch: priority order, SoC gate
- _async_find_active_roadtrip: calendar response parsing, multi-event merge
- _async_set_charge_limit: number and select domain dispatch
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
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
    CONF_FOSSIL_SENSOR,
    CONF_ROADTRIP_CALENDARS,
    CONF_ROADTRIP_CHARGE_LIMIT_ENTITY,
    CONF_ROADTRIP_DEFAULT_LEAD_HOURS,
    CONF_ROADTRIP_PREFIX,
    CONF_ROADTRIP_SOC_SENSOR,
    DEQUE_7D,
    STATE_SCHEDULED,
    STATUS_FORCED_OFF,
    STATUS_LOW_CARBON,
    STATUS_OVERRIDE,
    STATUS_ROADTRIP_PREP,
)
from custom_components.carbon_aware_ev_charging.coordinator import (
    EVCarbonCoordinator,
    RoadtripEvent,
    _ChargingDecision,
    _ResolvedConfig,
    _SensorReadings,
    _Statistics,
)

UTC = UTC

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_coordinator(
    hass: HomeAssistant,
    data_overrides: dict[str, Any] | None = None,
    options_overrides: dict[str, Any] | None = None,
) -> EVCarbonCoordinator:
    """Build a coordinator backed by a mocked config entry."""
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
        CONF_DRY_RUN: True,
        CONF_ROADTRIP_CALENDARS: ["calendar.home"],
        CONF_ROADTRIP_PREFIX: "IONIQ",
        CONF_ROADTRIP_DEFAULT_LEAD_HOURS: 3,
        CONF_ROADTRIP_SOC_SENSOR: "",
        CONF_ROADTRIP_CHARGE_LIMIT_ENTITY: "",
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
    coord._deque_7d = deque(maxlen=DEQUE_7D)
    coord._deque_30d = deque(maxlen=DEQUE_7D)
    coord._last_z_score = None
    coord._was_connected = False
    coord._stale_hard_count = 0
    coord._last_led_state = None
    coord.last_update_success = True
    return coord


def _make_resolved_config(overrides: dict[str, Any] | None = None) -> _ResolvedConfig:
    """Return a minimal _ResolvedConfig for decision-chain tests."""
    defaults: dict[str, Any] = {
        "co2_entity": "sensor.co2",
        "fossil_entity": "sensor.fossil",
        "charger_entity": "switch.charger",
        "connected_attr": "icon_name",
        "not_connected_val": "CarNotConnected",
        "power_entity": None,
        "led_light": None,
        "led_effect_select": None,
        "carbon_mode": "Moderate",
        "charge_mode": CHARGE_MODE_AUTO,
        "departure_hour": 5,
        "departure_days": [],
        "dry_run": True,
        "notify_service": "",
        "fb1_start": 22,
        "fb1_end": 6,
        "fb2_start": 11,
        "fb2_end": 15,
        "fb1_enabled": False,
        "fb2_enabled": False,
        "roadtrip_calendars": ["calendar.home"],
        "roadtrip_prefix": "IONIQ",
        "roadtrip_default_lead_hours": 3,
        "roadtrip_soc_sensor": None,
        "roadtrip_charge_limit_entity": None,
    }
    defaults.update(overrides or {})
    return _ResolvedConfig(**defaults)


def _make_sensors(overrides: dict[str, Any] | None = None) -> _SensorReadings:
    """Return a minimal _SensorReadings (car connected, charger off, data available)."""
    defaults: dict[str, Any] = {
        "co2": 200.0,
        "fossil_pct": 30.0,
        "carbon_data_unavailable": False,
        "data_stale": False,
        "is_connected": True,
        "charger_is_on": False,
        "charger_state": None,
        "charge_rate_kw": None,
        "charge_current_a": None,
    }
    defaults.update(overrides or {})
    return _SensorReadings(**defaults)


def _make_stats(z_score: float = 1.5) -> _Statistics:
    """Return a _Statistics object with a dirty-grid Z-score by default."""
    return _Statistics(
        mean_7d=200.0,
        stdev_7d=40.0,
        mean_30d=200.0,
        stdev_30d=40.0,
        z_score=z_score,
    )


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ── parse_roadtrip_title ──────────────────────────────────────────────────────


class TestParseRoadtripTitle:
    """Tests for EVCarbonCoordinator.parse_roadtrip_title."""

    @pytest.fixture
    def parse(self):
        coord = EVCarbonCoordinator.__new__(EVCarbonCoordinator)
        return coord.parse_roadtrip_title

    def test_full_title(self, parse):
        """[IONIQ 90% 4h] → soc=90, lead=4."""
        result = parse("[IONIQ 90% 4h]", "IONIQ", 3)
        assert result == (90, 4)

    def test_soc_only(self, parse):
        """[IONIQ 80%] → soc=80, lead=default."""
        result = parse("[IONIQ 80%]", "IONIQ", 3)
        assert result == (80, 3)

    def test_lead_only(self, parse):
        """[IONIQ 6h] → soc=None, lead=6."""
        result = parse("[IONIQ 6h]", "IONIQ", 3)
        assert result == (None, 6)

    def test_prefix_only(self, parse):
        """[IONIQ] → soc=None, lead=default."""
        result = parse("[IONIQ]", "IONIQ", 3)
        assert result == (None, 3)

    def test_case_insensitive_prefix(self, parse):
        """Prefix match is case-insensitive."""
        assert parse("[ioniq 90% 4h]", "IONIQ", 3) == (90, 4)
        assert parse("[IONIQ 90% 4h]", "ioniq", 3) == (90, 4)

    def test_no_match_different_prefix(self, parse):
        """Different prefix → None."""
        assert parse("[LEAF 90% 4h]", "IONIQ", 3) is None

    def test_no_bracket_in_title(self, parse):
        """Plain event title → None."""
        assert parse("Family trip to the mountains", "IONIQ", 3) is None

    def test_empty_prefix_config(self, parse):
        """Empty configured prefix always returns None (roadtrip disabled)."""
        assert parse("[IONIQ 90% 4h]", "", 3) is None

    def test_title_with_prefix_in_larger_string(self, parse):
        """Prefix embedded in a longer event description."""
        result = parse("Weekend away [IONIQ 80% 2h] — return Monday", "IONIQ", 3)
        assert result == (80, 2)

    def test_default_lead_hours_used(self, parse):
        """Default lead hours come from the parameter, not a constant."""
        result = parse("[IONIQ]", "IONIQ", 8)
        assert result == (None, 8)


# ── RoadtripEvent.prep_start ──────────────────────────────────────────────────


class TestRoadtripEventPrepStart:
    def test_prep_start_calculation(self):
        start = datetime(2026, 3, 21, 10, 0, tzinfo=UTC)
        event = RoadtripEvent(summary="Trip", start=start, soc_target=90, lead_hours=4)
        expected = datetime(2026, 3, 21, 6, 0, tzinfo=UTC)
        assert event.prep_start == expected

    def test_prep_start_midnight_wrap(self):
        start = datetime(2026, 3, 21, 2, 0, tzinfo=UTC)
        event = RoadtripEvent(summary="Trip", start=start, soc_target=None, lead_hours=3)
        expected = datetime(2026, 3, 20, 23, 0, tzinfo=UTC)
        assert event.prep_start == expected


# ── Decision chain: roadtrip priority ─────────────────────────────────────────


class TestRoadtripDecisionChain:
    """Tests for roadtrip branch in _evaluate_charging."""

    def _evaluate(
        self,
        hass: HomeAssistant,
        coord: EVCarbonCoordinator,
        active_roadtrip: RoadtripEvent | None,
        charge_mode: str = CHARGE_MODE_AUTO,
        carbon_good: bool = False,
        is_connected: bool = True,
        z_score: float = 1.5,
    ) -> _ChargingDecision:
        cfg = _make_resolved_config({"charge_mode": charge_mode})
        sensors = _make_sensors({"is_connected": is_connected})
        stats = _make_stats(z_score=z_score)
        return coord._evaluate_charging(cfg, sensors, stats, active_roadtrip)

    def test_roadtrip_activates_when_in_window(self, hass: HomeAssistant):
        coord = _make_coordinator(hass)
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ 80% 4h]",
            start=now + timedelta(hours=2),
            soc_target=80,
            lead_hours=4,
        )
        # Now is well within the prep window (4h before start)
        decision = self._evaluate(hass, coord, active_roadtrip=event)
        assert decision.status_enum == STATUS_ROADTRIP_PREP
        assert decision.should_charge is True
        assert decision.predicted_state == STATE_SCHEDULED
        assert decision.led_state == "roadtrip"

    def test_force_off_beats_roadtrip(self, hass: HomeAssistant):
        coord = _make_coordinator(hass)
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ]",
            start=now + timedelta(hours=2),
            soc_target=None,
            lead_hours=4,
        )
        decision = self._evaluate(
            hass, coord, active_roadtrip=event, charge_mode=CHARGE_MODE_FORCE_OFF
        )
        assert decision.status_enum == STATUS_FORCED_OFF
        assert decision.should_charge is False

    def test_force_on_beats_roadtrip(self, hass: HomeAssistant):
        coord = _make_coordinator(hass)
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ]",
            start=now + timedelta(hours=2),
            soc_target=None,
            lead_hours=4,
        )
        decision = self._evaluate(
            hass, coord, active_roadtrip=event, charge_mode=CHARGE_MODE_FORCE_ON
        )
        assert decision.status_enum == STATUS_OVERRIDE

    def test_carbon_beats_roadtrip(self, hass: HomeAssistant):
        """carbon > roadtrip in the priority chain."""
        coord = _make_coordinator(hass)
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ]",
            start=now + timedelta(hours=2),
            soc_target=None,
            lead_hours=4,
        )
        decision = self._evaluate(
            hass, coord, active_roadtrip=event, carbon_good=True, z_score=-0.5
        )
        assert decision.status_enum == STATUS_LOW_CARBON

    def test_no_roadtrip_when_not_in_window(self, hass: HomeAssistant):
        """active_roadtrip=None → not in roadtrip status."""
        coord = _make_coordinator(hass)
        decision = self._evaluate(hass, coord, active_roadtrip=None)
        assert decision.status_enum != STATUS_ROADTRIP_PREP

    def test_roadtrip_not_connected_overlaid(self, hass: HomeAssistant):
        """Car disconnected overlays roadtrip prep — should_charge is False."""
        coord = _make_coordinator(hass)
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ]",
            start=now + timedelta(hours=2),
            soc_target=None,
            lead_hours=4,
        )
        decision = self._evaluate(hass, coord, active_roadtrip=event, is_connected=False)
        assert decision.should_charge is False
        # led_state should still reflect roadtrip (computed before connection check)
        assert decision.led_state == "roadtrip"


# ── SoC gate ──────────────────────────────────────────────────────────────────


class TestRoadtripSoCGate:
    """Roadtrip charging skipped when SoC target is already met."""

    def test_soc_met_skips_roadtrip(self, hass: HomeAssistant):
        """When current SoC >= target, roadtrip prep should not activate."""
        hass.states.async_set("sensor.soc", "85")  # already at 85%
        coord = _make_coordinator(
            hass,
            options_overrides={
                CONF_ROADTRIP_SOC_SENSOR: "sensor.soc",
            },
        )
        now = _now_utc()
        # Event with 80% target — already met
        event = RoadtripEvent(
            summary="[IONIQ 80% 4h]",
            start=now + timedelta(hours=2),
            soc_target=80,
            lead_hours=4,
        )
        cfg = _make_resolved_config(
            {
                "roadtrip_soc_sensor": "sensor.soc",
            }
        )
        sensors = _make_sensors()
        stats = _make_stats()
        decision = coord._evaluate_charging(cfg, sensors, stats, event)
        # SoC 85 >= target 80 → roadtrip gate closed
        assert decision.status_enum != STATUS_ROADTRIP_PREP

    def test_soc_below_target_activates_roadtrip(self, hass: HomeAssistant):
        """When current SoC < target, roadtrip prep should activate."""
        hass.states.async_set("sensor.soc", "60")
        coord = _make_coordinator(
            hass,
            options_overrides={
                CONF_ROADTRIP_SOC_SENSOR: "sensor.soc",
            },
        )
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ 80% 4h]",
            start=now + timedelta(hours=2),
            soc_target=80,
            lead_hours=4,
        )
        cfg = _make_resolved_config(
            {
                "roadtrip_soc_sensor": "sensor.soc",
            }
        )
        sensors = _make_sensors()
        stats = _make_stats()
        decision = coord._evaluate_charging(cfg, sensors, stats, event)
        assert decision.status_enum == STATUS_ROADTRIP_PREP

    def test_no_soc_sensor_configured_allows_roadtrip(self, hass: HomeAssistant):
        """Without a SoC sensor, roadtrip prep always activates if in window."""
        coord = _make_coordinator(hass, options_overrides={CONF_ROADTRIP_SOC_SENSOR: ""})
        now = _now_utc()
        event = RoadtripEvent(
            summary="[IONIQ 80% 4h]",
            start=now + timedelta(hours=2),
            soc_target=80,
            lead_hours=4,
        )
        cfg = _make_resolved_config({"roadtrip_soc_sensor": None})
        sensors = _make_sensors()
        stats = _make_stats()
        decision = coord._evaluate_charging(cfg, sensors, stats, event)
        assert decision.status_enum == STATUS_ROADTRIP_PREP


# ── _async_find_active_roadtrip ───────────────────────────────────────────────


class TestAsyncFindActiveRoadtrip:
    """Tests for calendar query and event-merge strategy."""

    def _make_coord_with_mock_services(
        self,
        hass: HomeAssistant,
        service_response: Any,
        *,
        side_effect: Exception | None = None,
    ) -> EVCarbonCoordinator:
        """Return a coordinator whose hass.services.async_call is an AsyncMock."""
        coord = _make_coordinator(hass)
        mock_services = AsyncMock()
        if side_effect is not None:
            mock_services.async_call = AsyncMock(side_effect=side_effect)
        else:
            mock_services.async_call = AsyncMock(return_value=service_response)
        coord.hass = MagicMock()
        coord.hass.services = mock_services
        coord.hass.states = hass.states
        return coord

    @pytest.mark.asyncio
    async def test_no_calendars_returns_none(self, hass: HomeAssistant):
        coord = _make_coordinator(
            hass, options_overrides={CONF_ROADTRIP_CALENDARS: [], CONF_ROADTRIP_PREFIX: "IONIQ"}
        )
        cfg = _make_resolved_config({"roadtrip_calendars": [], "roadtrip_prefix": "IONIQ"})
        result = await coord._async_find_active_roadtrip(cfg)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_prefix_returns_none(self, hass: HomeAssistant):
        coord = _make_coordinator(hass, options_overrides={CONF_ROADTRIP_PREFIX: ""})
        cfg = _make_resolved_config({"roadtrip_prefix": ""})
        result = await coord._async_find_active_roadtrip(cfg)
        assert result is None

    @pytest.mark.asyncio
    async def test_single_matching_event(self, hass: HomeAssistant):
        now = _now_utc()
        event_start = (now + timedelta(hours=6)).isoformat()
        mock_response = {
            "calendar.home": {
                "events": [
                    {"summary": "[IONIQ 90% 4h]", "start": event_start},
                ]
            }
        }
        coord = self._make_coord_with_mock_services(hass, mock_response)
        cfg = _make_resolved_config()
        result = await coord._async_find_active_roadtrip(cfg)

        assert result is not None
        assert result.soc_target == 90
        assert result.lead_hours == 4

    @pytest.mark.asyncio
    async def test_non_matching_event_ignored(self, hass: HomeAssistant):
        now = _now_utc()
        event_start = (now + timedelta(hours=6)).isoformat()
        mock_response = {
            "calendar.home": {
                "events": [
                    {"summary": "Birthday party", "start": event_start},
                ]
            }
        }
        coord = self._make_coord_with_mock_services(hass, mock_response)
        cfg = _make_resolved_config()
        result = await coord._async_find_active_roadtrip(cfg)

        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_events_uses_earliest_start_and_highest_soc(self, hass: HomeAssistant):
        """Two matching events: result uses the earliest start, highest SoC."""
        now = _now_utc()
        earlier = (now + timedelta(hours=4)).isoformat()
        later = (now + timedelta(hours=8)).isoformat()
        mock_response = {
            "calendar.home": {
                "events": [
                    {"summary": "[IONIQ 70%]", "start": later},
                    {"summary": "[IONIQ 90%]", "start": earlier},
                ]
            }
        }
        coord = self._make_coord_with_mock_services(hass, mock_response)
        cfg = _make_resolved_config()
        result = await coord._async_find_active_roadtrip(cfg)

        assert result is not None
        # Earliest start
        assert abs((result.start - (now + timedelta(hours=4))).total_seconds()) < 5
        # Highest SoC
        assert result.soc_target == 90

    @pytest.mark.asyncio
    async def test_all_day_event_parsed(self, hass: HomeAssistant):
        """All-day event with date-only string is handled without crash."""
        mock_response = {
            "calendar.home": {
                "events": [
                    {"summary": "[IONIQ]", "start": "2026-03-22"},
                ]
            }
        }
        coord = self._make_coord_with_mock_services(hass, mock_response)
        cfg = _make_resolved_config()
        # Should not raise; may or may not return an event depending on
        # whether the date is within the lookahead window — just verify no crash.
        await coord._async_find_active_roadtrip(cfg)

    @pytest.mark.asyncio
    async def test_calendar_service_failure_returns_none(self, hass: HomeAssistant):
        """If calendar.get_events raises, return None gracefully."""
        coord = self._make_coord_with_mock_services(
            hass, None, side_effect=Exception("calendar unavailable")
        )
        cfg = _make_resolved_config()
        result = await coord._async_find_active_roadtrip(cfg)
        assert result is None

    @pytest.mark.asyncio
    async def test_service_not_found_returns_none_silently(self, hass: HomeAssistant):
        """ServiceNotFound (calendar integration not loaded) is handled silently."""
        coord = self._make_coord_with_mock_services(
            hass, None, side_effect=ServiceNotFound("calendar", "get_events")
        )
        cfg = _make_resolved_config()
        result = await coord._async_find_active_roadtrip(cfg)
        assert result is None


# ── _async_set_charge_limit ───────────────────────────────────────────────────


class TestAsyncSetChargeLimit:
    """Charge-limit entity dispatches to correct HA service by domain."""

    def _make_coord_with_mock_services(
        self, hass: HomeAssistant
    ) -> tuple[EVCarbonCoordinator, AsyncMock]:
        coord = _make_coordinator(hass)
        mock_async_call = AsyncMock()
        mock_services = MagicMock()
        mock_services.async_call = mock_async_call
        coord.hass = MagicMock()
        coord.hass.services = mock_services
        coord.hass.states = hass.states
        return coord, mock_async_call

    @pytest.mark.asyncio
    async def test_number_domain_calls_set_value(self, hass: HomeAssistant):
        hass.states.async_set("number.charge_limit", "80")
        coord, mock_async_call = self._make_coord_with_mock_services(hass)

        await coord._async_set_charge_limit("number.charge_limit", 90)

        mock_async_call.assert_called_once_with(
            "number",
            "set_value",
            {"entity_id": "number.charge_limit", "value": 90},
            blocking=False,
        )

    @pytest.mark.asyncio
    async def test_select_domain_calls_select_option(self, hass: HomeAssistant):
        hass.states.async_set("select.charge_limit", "80")
        coord, mock_async_call = self._make_coord_with_mock_services(hass)

        await coord._async_set_charge_limit("select.charge_limit", 90)

        mock_async_call.assert_called_once_with(
            "select",
            "select_option",
            {"entity_id": "select.charge_limit", "option": "90"},
            blocking=False,
        )

    @pytest.mark.asyncio
    async def test_unknown_domain_does_not_call_service(self, hass: HomeAssistant):
        hass.states.async_set("sensor.charge_limit", "80")
        coord, mock_async_call = self._make_coord_with_mock_services(hass)

        await coord._async_set_charge_limit("sensor.charge_limit", 90)

        mock_async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_entity_does_not_call_service(self, hass: HomeAssistant):
        """Entity not in hass.states → log warning, no service call."""
        coord, mock_async_call = self._make_coord_with_mock_services(hass)

        await coord._async_set_charge_limit("number.nonexistent", 90)

        mock_async_call.assert_not_called()
