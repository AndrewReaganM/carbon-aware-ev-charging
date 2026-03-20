"""Binary sensor entities for Carbon-Aware EV Charging."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import EVChargerBaseEntity
from .const import DOMAIN, ENTITY_ID_CONNECTED, ENTITY_ID_LOW_CARBON_NOW
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EvConnectedBinarySensor(coordinator, entry),
            EvLowCarbonNowBinarySensor(coordinator, entry),
        ]
    )


class EvConnectedBinarySensor(EVChargerBaseEntity, BinarySensorEntity):
    """True when the EV is plugged into the charger."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator: EVCarbonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_CONNECTED}"
        self._attr_name = "EV Connected"

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        return self.coordinator.data.is_connected


class EvLowCarbonNowBinarySensor(EVChargerBaseEntity, BinarySensorEntity):
    """True when the grid is clean enough to charge."""

    _attr_icon = "mdi:leaf"

    def __init__(self, coordinator: EVCarbonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_LOW_CARBON_NOW}"
        self._attr_name = "EV Low Carbon Now"

    @property
    def available(self) -> bool:
        # Always available — returns False during warmup instead of unavailable.
        return True

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        return self._data.carbon_good

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.last_update_success:
            return {}
        return {
            "predicted_state": self._data.predicted_state,
            "should_charge": self._data.should_charge,
            "carbon_data_unavailable": self._data.carbon_data_unavailable,
            "data_stale": self._data.data_stale,
            "fossil_pct": self._data.fossil_pct,
        }
