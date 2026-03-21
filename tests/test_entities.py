"""Unit tests for all entity platforms (sensor, binary_sensor, select, number).

Entities are instantiated with mock coordinators so these tests run without a
full HA instance. They focus on property correctness and state-mutation methods.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.carbon_aware_ev_charging.binary_sensor import (
    EvConnectedBinarySensor,
    EvLowCarbonNowBinarySensor,
)
from custom_components.carbon_aware_ev_charging.const import (
    CARBON_MODE_MODERATE,
    CARBON_MODE_STRICT,
    CARBON_MODES,
    CHARGE_MODE_AUTO,
    CHARGE_MODE_FORCE_ON,
    CHARGE_MODES,
    CHARGING_STATUSES,
    CONF_CARBON_MODE,
    CONF_CHARGE_MODE,
    CONF_DEPARTURE_HOUR,
    CONF_DRY_RUN,
    STATE_CARBON,
    STATE_PAUSED,
)
from custom_components.carbon_aware_ev_charging.coordinator import EVCarbonData
from custom_components.carbon_aware_ev_charging.number import EvDepartureHourNumber
from custom_components.carbon_aware_ev_charging.select import EvCarbonModeSelect, EvChargeModeSelect
from custom_components.carbon_aware_ev_charging.sensor import (
    EvChargeCurrentSensor,
    EvChargeRateKwSensor,
    EvChargingStatusSensor,
    EvRoadtripEventSensor,
    EvZScoreSensor,
)
from custom_components.carbon_aware_ev_charging.switch import EvOptionSwitch

# ── Helpers ────────────────────────────────────────────────────────────────────


def _coord(data: EVCarbonData, success: bool = True) -> MagicMock:
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = success
    coord.async_request_refresh = AsyncMock()
    return coord


def _entry(options: dict | None = None, entry_id: str = "test") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.options = options or {}
    return entry


# ── EvZScoreSensor ─────────────────────────────────────────────────────────────


def test_z_score_sensor_value_and_available() -> None:
    data = EVCarbonData(z_score=-0.50, mean_7d=200.0, stdev_7d=8.66, co2=155.0)
    sensor = EvZScoreSensor(_coord(data), _entry())

    assert sensor.native_value == pytest.approx(-0.50)
    assert sensor.available is True


def test_z_score_sensor_unavailable_when_none() -> None:
    data = EVCarbonData(z_score=None)
    sensor = EvZScoreSensor(_coord(data), _entry())

    assert sensor.available is False


def test_z_score_sensor_unavailable_when_coordinator_failed() -> None:
    data = EVCarbonData(z_score=-0.3)
    sensor = EvZScoreSensor(_coord(data, success=False), _entry())

    assert sensor.available is False


def test_z_score_sensor_extra_attrs() -> None:
    data = EVCarbonData(
        z_score=0.10,
        mean_7d=200.0,
        stdev_7d=9.0,
        mean_30d=195.0,
        stdev_30d=11.0,
        co2=201.0,
    )
    sensor = EvZScoreSensor(_coord(data), _entry())
    attrs = sensor.extra_state_attributes

    assert attrs["mean_7d"] == pytest.approx(200.0)
    assert attrs["stdev_7d"] == pytest.approx(9.0)
    assert attrs["mean_30d"] == pytest.approx(195.0)
    assert attrs["co2"] == pytest.approx(201.0)


# ── EvLowCarbonNowBinarySensor ─────────────────────────────────────────────────


def test_low_carbon_now_true() -> None:
    data = EVCarbonData(carbon_good=True, predicted_state=STATE_CARBON)
    sensor = EvLowCarbonNowBinarySensor(_coord(data), _entry())

    assert sensor.is_on is True


def test_low_carbon_now_false() -> None:
    data = EVCarbonData(carbon_good=False, predicted_state=STATE_PAUSED)
    sensor = EvLowCarbonNowBinarySensor(_coord(data), _entry())

    assert sensor.is_on is False


def test_low_carbon_now_always_available() -> None:
    """Should never be 'unavailable' — returns False during warmup instead."""
    data = EVCarbonData(carbon_good=False)
    sensor = EvLowCarbonNowBinarySensor(_coord(data, success=False), _entry())

    assert sensor.available is True
    assert sensor.is_on is False


def test_low_carbon_now_extra_attrs() -> None:
    data = EVCarbonData(
        carbon_good=False,
        predicted_state=STATE_PAUSED,
        should_charge=False,
        carbon_data_unavailable=True,
        fossil_pct=55.0,
    )
    sensor = EvLowCarbonNowBinarySensor(_coord(data), _entry())
    attrs = sensor.extra_state_attributes

    assert attrs["predicted_state"] == STATE_PAUSED
    assert attrs["should_charge"] is False
    assert attrs["carbon_data_unavailable"] is True
    assert attrs["fossil_pct"] == pytest.approx(55.0)


# ── EvChargeRateKwSensor ───────────────────────────────────────────────────────


def test_charge_rate_kw_value() -> None:
    data = EVCarbonData(charge_rate_kw=6.9)
    sensor = EvChargeRateKwSensor(_coord(data), _entry())

    assert sensor.native_value == pytest.approx(6.9)
    assert sensor.available is True


def test_charge_rate_kw_unavailable_when_none() -> None:
    data = EVCarbonData(charge_rate_kw=None)
    sensor = EvChargeRateKwSensor(_coord(data), _entry())

    assert sensor.available is False


# ── EvChargeCurrentSensor ──────────────────────────────────────────────────────


def test_charge_current_value() -> None:
    data = EVCarbonData(charge_current_a=16)
    sensor = EvChargeCurrentSensor(_coord(data), _entry())

    assert sensor.native_value == 16
    assert sensor.available is True


def test_charge_current_unavailable_when_none() -> None:
    data = EVCarbonData(charge_current_a=None)
    sensor = EvChargeCurrentSensor(_coord(data), _entry())

    assert sensor.available is False


# ── EvRoadtripEventSensor ──────────────────────────────────────────────────────


def test_roadtrip_event_sensor_idle_is_none() -> None:
    """When no roadtrip is active, native_value is None (renders as 'unknown')."""
    data = EVCarbonData(active_roadtrip=None)
    sensor = EvRoadtripEventSensor(_coord(data), _entry())

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_roadtrip_event_sensor_active_shows_prep_start() -> None:
    """When a roadtrip is active, native_value is the prep_start datetime."""
    from datetime import datetime

    from custom_components.carbon_aware_ev_charging.coordinator import RoadtripEvent

    event = RoadtripEvent(
        summary="[IONIQ 90% 4h]",
        start=datetime(2026, 3, 21, 9, 0, tzinfo=UTC),
        soc_target=90,
        lead_hours=4,
    )
    data = EVCarbonData(active_roadtrip=event)
    sensor = EvRoadtripEventSensor(_coord(data), _entry())

    assert sensor.native_value == datetime(2026, 3, 21, 5, 0, tzinfo=UTC)
    attrs = sensor.extra_state_attributes
    assert attrs["summary"] == "[IONIQ 90% 4h]"
    assert attrs["soc_target"] == 90
    assert attrs["lead_hours"] == 4
    assert attrs["event_start"] == "2026-03-21T09:00:00+00:00"
    assert attrs["prep_start"] == "2026-03-21T05:00:00+00:00"


def test_roadtrip_event_sensor_unavailable_on_coord_failure() -> None:
    data = EVCarbonData(active_roadtrip=None)
    sensor = EvRoadtripEventSensor(_coord(data, success=False), _entry())

    assert sensor.available is False


# ── EvConnectedBinarySensor ────────────────────────────────────────────────────


def test_ev_connected_on() -> None:
    data = EVCarbonData(is_connected=True)
    bs = EvConnectedBinarySensor(_coord(data), _entry())

    assert bs.is_on is True


def test_ev_connected_off() -> None:
    data = EVCarbonData(is_connected=False)
    bs = EvConnectedBinarySensor(_coord(data), _entry())

    assert bs.is_on is False


def test_ev_connected_off_when_coordinator_failed() -> None:
    data = EVCarbonData(is_connected=True)  # data says True
    bs = EvConnectedBinarySensor(_coord(data, success=False), _entry())

    assert bs.is_on is False  # returns False when update failed


# ── EvChargingStatusSensor ──────────────────────────────────────────────────────────


def test_charging_status_is_enum_sensor() -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    sensor = EvChargingStatusSensor(_coord(EVCarbonData()), _entry())
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.options == CHARGING_STATUSES


def test_charging_status_not_connected() -> None:
    data = EVCarbonData(
        is_connected=False,
        status_enum="not_connected",
        status_reason="Not connected",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "not_connected"


def test_charging_status_carbon_good() -> None:
    data = EVCarbonData(
        is_connected=True,
        carbon_good=True,
        predicted_state=STATE_CARBON,
        status_enum="low_carbon",
        status_reason="Charging — grid is clean (-0.5σ)",
        z_score=-0.5,
        fossil_pct=30.0,
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "low_carbon"


def test_charging_status_forced_on() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state="override",
        status_enum="override",
        status_reason="Charging — forced on",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "override"


def test_charging_status_forced_off() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state=STATE_PAUSED,
        status_enum="forced_off",
        status_reason="Paused — forced off",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "forced_off"


def test_charging_status_grid_dirty() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state=STATE_PAUSED,
        status_enum="grid_dirty",
        status_reason="Paused — grid too dirty (1.2σ)",
        z_score=1.2,
        fossil_pct=40.0,
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "grid_dirty"


def test_charging_status_fossil_high() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state=STATE_PAUSED,
        status_enum="fossil_high",
        status_reason="Paused — fossil fuel too high (80%)",
        z_score=-0.3,
        fossil_pct=80.0,
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "fossil_high"


def test_charging_status_departure_prep() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state="scheduled",
        status_enum="departure_prep",
        status_reason="Charging — departure prep Wed 07:00",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "departure_prep"


def test_charging_status_fallback() -> None:
    data = EVCarbonData(
        is_connected=True,
        predicted_state="scheduled",
        status_enum="fallback",
        status_reason="Charging — fallback window",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "fallback"


def test_charging_status_waiting_for_data() -> None:
    data = EVCarbonData(
        is_connected=True,
        carbon_data_unavailable=True,
        status_enum="waiting_for_data",
        status_reason="Paused — waiting for data",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    assert sensor.native_value == "waiting_for_data"


def test_charging_status_unavailable_on_coord_failure() -> None:
    data = EVCarbonData(status_reason="Charging — grid is clean")
    sensor = EvChargingStatusSensor(_coord(data, success=False), _entry())
    assert sensor.native_value == "unavailable"
    assert sensor.available is True  # always reports available


def test_charging_status_extra_attrs() -> None:
    data = EVCarbonData(
        predicted_state=STATE_CARBON,
        should_charge=True,
        is_connected=True,
        z_score=-0.5,
        fossil_pct=30.0,
        status_enum="low_carbon",
        status_reason="Charging — grid is clean (-0.5σ)",
    )
    sensor = EvChargingStatusSensor(_coord(data), _entry())
    attrs = sensor.extra_state_attributes
    assert attrs["status_reason"] == "Charging — grid is clean (-0.5σ)"
    assert attrs["predicted_state"] == STATE_CARBON
    assert attrs["should_charge"] is True
    assert attrs["is_connected"] is True
    assert attrs["z_score"] == pytest.approx(-0.5)
    assert attrs["fossil_pct"] == pytest.approx(30.0)


# ── EvChargeModeSelect ─────────────────────────────────────────────────────────


def test_charge_mode_select_options() -> None:
    select = EvChargeModeSelect(_coord(EVCarbonData()), _entry())
    assert select.options == CHARGE_MODES


def test_charge_mode_select_current_option_default() -> None:
    select = EvChargeModeSelect(_coord(EVCarbonData()), _entry())
    assert select.current_option == CHARGE_MODE_AUTO


def test_charge_mode_select_current_option_from_entry() -> None:
    entry = _entry({CONF_CHARGE_MODE: CHARGE_MODE_FORCE_ON})
    select = EvChargeModeSelect(_coord(EVCarbonData()), entry)
    assert select.current_option == CHARGE_MODE_FORCE_ON


async def test_charge_mode_select_async_select() -> None:
    coord = _coord(EVCarbonData())
    entry = _entry({CONF_CHARGE_MODE: CHARGE_MODE_AUTO})
    select = EvChargeModeSelect(coord, entry)
    select.hass = MagicMock()
    select.hass.config_entries.async_update_entry = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option(CHARGE_MODE_FORCE_ON)

    update_call = select.hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["options"][CONF_CHARGE_MODE] == CHARGE_MODE_FORCE_ON
    coord.async_request_refresh.assert_called_once()


# ── EvCarbonModeSelect ─────────────────────────────────────────────────────────


def test_carbon_mode_select_options() -> None:
    select = EvCarbonModeSelect(_coord(EVCarbonData()), _entry())
    assert select.options == CARBON_MODES


def test_carbon_mode_select_default() -> None:
    select = EvCarbonModeSelect(_coord(EVCarbonData()), _entry())
    assert select.current_option == CARBON_MODE_MODERATE


async def test_carbon_mode_select_async_select() -> None:
    coord = _coord(EVCarbonData())
    entry = _entry({CONF_CARBON_MODE: CARBON_MODE_MODERATE})
    select = EvCarbonModeSelect(coord, entry)
    select.hass = MagicMock()
    select.hass.config_entries.async_update_entry = MagicMock()
    select.async_write_ha_state = MagicMock()

    await select.async_select_option(CARBON_MODE_STRICT)

    update_call = select.hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["options"][CONF_CARBON_MODE] == CARBON_MODE_STRICT
    coord.async_request_refresh.assert_called_once()


# ── EvDepartureHourNumber ──────────────────────────────────────────────────────


def test_departure_hour_default_value() -> None:
    number = EvDepartureHourNumber(_coord(EVCarbonData()), _entry())
    assert number.native_value == pytest.approx(5.0)


def test_departure_hour_value_from_entry() -> None:
    entry = _entry({CONF_DEPARTURE_HOUR: 7})
    number = EvDepartureHourNumber(_coord(EVCarbonData()), entry)
    assert number.native_value == pytest.approx(7.0)


def test_departure_hour_min_max() -> None:
    number = EvDepartureHourNumber(_coord(EVCarbonData()), _entry())
    assert number.native_min_value == 0
    assert number.native_max_value == 23


async def test_departure_hour_set_value() -> None:
    coord = _coord(EVCarbonData())
    entry = _entry({CONF_DEPARTURE_HOUR: 5})
    number = EvDepartureHourNumber(coord, entry)
    number.hass = MagicMock()
    number.hass.config_entries.async_update_entry = MagicMock()
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(8.0)

    update_call = number.hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["options"][CONF_DEPARTURE_HOUR] == 8
    coord.async_request_refresh.assert_called_once()


# ── EvOptionSwitch (dry-run) ───────────────────────────────────────────────────


def test_dry_run_switch_default_off() -> None:
    """Default preference is False → switch is off."""
    switch = EvOptionSwitch(
        _coord(EVCarbonData()), _entry(), key=CONF_DRY_RUN, name="EV Dry Run", icon="mdi:test-tube"
    )
    assert switch.is_on is False


def test_dry_run_switch_on_from_options() -> None:
    """When options has dry_run=True → switch is on."""
    switch = EvOptionSwitch(
        _coord(EVCarbonData()),
        _entry({CONF_DRY_RUN: True}),
        key=CONF_DRY_RUN,
        name="EV Dry Run",
        icon="mdi:test-tube",
    )
    assert switch.is_on is True


async def test_dry_run_switch_turn_on() -> None:
    """Turning on writes dry_run=True to options and refreshes the coordinator."""
    coord = _coord(EVCarbonData())
    entry = _entry({CONF_DRY_RUN: False})
    switch = EvOptionSwitch(coord, entry, key=CONF_DRY_RUN, name="EV Dry Run", icon="mdi:test-tube")
    switch.hass = MagicMock()
    switch.hass.config_entries.async_update_entry = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_on()

    update_call = switch.hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["options"][CONF_DRY_RUN] is True
    coord.async_request_refresh.assert_called_once()


async def test_dry_run_switch_turn_off() -> None:
    """Turning off writes dry_run=False to options and refreshes the coordinator."""
    coord = _coord(EVCarbonData())
    entry = _entry({CONF_DRY_RUN: True})
    switch = EvOptionSwitch(coord, entry, key=CONF_DRY_RUN, name="EV Dry Run", icon="mdi:test-tube")
    switch.hass = MagicMock()
    switch.hass.config_entries.async_update_entry = MagicMock()
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    update_call = switch.hass.config_entries.async_update_entry.call_args
    assert update_call.kwargs["options"][CONF_DRY_RUN] is False
    coord.async_request_refresh.assert_called_once()
