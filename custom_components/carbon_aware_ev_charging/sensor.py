"""Sensor entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import EVChargerBaseEntity
from .const import (
    CHARGING_STATUSES,
    CONF_CHARGER_POWER_SENSOR,
    DOMAIN,
    ENTITY_ID_CHARGE_CURRENT,
    ENTITY_ID_CHARGE_RATE_KW,
    ENTITY_ID_CHARGING_STATUS,
    ENTITY_ID_Z_SCORE,
)
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        EvZScoreSensor(coordinator, entry),
        EvChargingStatusSensor(coordinator, entry),
        EvChargeCurrentSensor(coordinator, entry),
    ]
    if entry.data.get(CONF_CHARGER_POWER_SENSOR):
        entities.append(EvChargeRateKwSensor(coordinator, entry))
    async_add_entities(entities)


class EvZScoreSensor(EVChargerBaseEntity, SensorEntity):
    """CO2 intensity Z-score relative to 7-day rolling mean."""

    _attr_native_unit_of_measurement = "σ"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sigma"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_Z_SCORE}"
        self._attr_name = "EV CO2 Z-Score"

    @property
    def native_value(self) -> float | None:
        return self._data.z_score

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.z_score is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "mean_7d": self._data.mean_7d,
            "stdev_7d": self._data.stdev_7d,
            "mean_30d": self._data.mean_30d,
            "stdev_30d": self._data.stdev_30d,
            "co2": self._data.co2,
        }


class EvChargingStatusSensor(EVChargerBaseEntity, SensorEntity):
    """Machine-readable charging status enum with human-readable detail."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CHARGING_STATUSES
    _attr_icon = "mdi:message-text"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_CHARGING_STATUS}"
        self._attr_name = "EV Charging Status"

    @property
    def native_value(self) -> str:
        if not self.coordinator.last_update_success:
            return "unavailable"
        return self._data.status_enum

    @property
    def available(self) -> bool:  # type: ignore[override]
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status_reason": self._data.status_reason,
            "predicted_state": self._data.predicted_state,
            "should_charge": self._data.should_charge,
            "is_connected": self._data.is_connected,
            "z_score": self._data.z_score,
            "fossil_pct": self._data.fossil_pct,
        }


class EvChargeRateKwSensor(EVChargerBaseEntity, SensorEntity):
    """Charging power in kW (derived from the optional power sensor)."""

    _attr_native_unit_of_measurement = "kW"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_CHARGE_RATE_KW}"
        self._attr_name = "EV Charge Rate"

    @property
    def native_value(self) -> float | None:
        return self._data.charge_rate_kw

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.charge_rate_kw is not None
        )


class EvChargeCurrentSensor(EVChargerBaseEntity, SensorEntity):
    """Charging current in A (from charger switch attribute)."""

    _attr_native_unit_of_measurement = "A"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_CHARGE_CURRENT}"
        self._attr_name = "EV Charge Current"

    @property
    def native_value(self) -> int | None:
        return self._data.charge_current_a

    @property
    def available(self) -> bool:  # type: ignore[override]
        return (
            self.coordinator.last_update_success
            and self._data.charge_current_a is not None
        )
