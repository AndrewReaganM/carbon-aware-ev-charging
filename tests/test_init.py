"""Tests for Carbon-Aware EV Charging integration setup/unload lifecycle (__init__.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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

    fake_history = [[1_000_000_000 + i * 300, float(200 + i % 10)] for i in range(50)]

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
    assert coordinator._last_z_score == pytest.approx(-0.25)

    await hass.config_entries.async_unload(entry.entry_id)
