"""Sensor entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from functools import cached_property
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CHARGER_POWER_SENSOR, DOMAIN
from .coordinator import EVCarbonCoordinator, EVCarbonData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        EvZScoreSensor(coordinator, entry),
        EvLowCarbonNowSensor(coordinator, entry),
        EvChargeCurrentSensor(coordinator, entry),
    ]
    if entry.data.get(CONF_CHARGER_POWER_SENSOR):
        entities.append(EvChargeRateKwSensor(coordinator, entry))
    async_add_entities(entities)


class _EvBaseEntity(CoordinatorEntity[EVCarbonCoordinator]):
    """Shared base: ties entity to the integration device."""

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Carbon-Aware EV Charging",
        )

    @property
    def _data(self) -> EVCarbonData:
        return self.coordinator.data


class EvZScoreSensor(_EvBaseEntity, SensorEntity):
    """CO2 intensity Z-score relative to 7-day rolling mean."""

    _attr_native_unit_of_measurement = "σ"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sigma"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_co2_z_score"
        self._attr_name = "EV CO2 Z-Score"

    @cached_property
    def native_value(self) -> float | None:
        return self._data.z_score

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.z_score is not None
        )

    @cached_property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "mean_7d": self._data.mean_7d,
            "stdev_7d": self._data.stdev_7d,
            "mean_30d": self._data.mean_30d,
            "stdev_30d": self._data.stdev_30d,
            "co2": self._data.co2,
        }


class EvLowCarbonNowSensor(_EvBaseEntity, SensorEntity):
    """Boolean gate: True when grid is clean enough to charge."""

    _attr_icon = "mdi:leaf"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_low_carbon_now"
        self._attr_name = "EV Low Carbon Now"

    @cached_property
    def native_value(self) -> str:
        if not self.coordinator.last_update_success:
            return "False"
        return str(self._data.carbon_good)

    @property
    def available(self) -> bool:  # type: ignore[override]
        # Always available — returns False during warmup instead of unavailable.
        return True

    @cached_property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "predicted_state": self._data.predicted_state,
            "should_charge": self._data.should_charge,
            "carbon_data_unavailable": self._data.carbon_data_unavailable,
            "fossil_pct": self._data.fossil_pct,
        }


class EvChargeRateKwSensor(_EvBaseEntity, SensorEntity):
    """Charging power in kW (derived from the optional power sensor)."""

    _attr_native_unit_of_measurement = "kW"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_charge_rate_kw"
        self._attr_name = "EV Charge Rate"

    @cached_property
    def native_value(self) -> float | None:
        return self._data.charge_rate_kw

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.charge_rate_kw is not None
        )


class EvChargeCurrentSensor(_EvBaseEntity, SensorEntity):
    """Charging current in A (from charger switch attribute)."""

    _attr_native_unit_of_measurement = "A"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_charge_current"
        self._attr_name = "EV Charge Current"

    @cached_property
    def native_value(self) -> int | None:
        return self._data.charge_current_a

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.charge_current_a is not None
        )
