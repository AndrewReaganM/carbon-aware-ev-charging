"""Binary sensor entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from functools import cached_property
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EvConnectedBinarySensor(coordinator, entry),
        EvLowCarbonNowBinarySensor(coordinator, entry),
    ])


class EvConnectedBinarySensor(
    CoordinatorEntity[EVCarbonCoordinator], BinarySensorEntity
):
    """True when the EV is plugged into the charger."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:car-electric"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_connected"
        self._attr_name = "EV Connected"
        self._entry = entry

    @property
    def available(self) -> bool:  # type: ignore[override]
        return super().available

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Carbon-Aware EV Charging",
        )

    @cached_property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        return self.coordinator.data.is_connected


class EvLowCarbonNowBinarySensor(
    CoordinatorEntity[EVCarbonCoordinator], BinarySensorEntity
):
    """True when the grid is clean enough to charge."""

    _attr_icon = "mdi:leaf"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_low_carbon_now"
        self._attr_name = "EV Low Carbon Now"
        self._entry = entry

    @property
    def available(self) -> bool:  # type: ignore[override]
        # Always available — returns False during warmup instead of unavailable.
        return True

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Carbon-Aware EV Charging",
        )

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        return self.coordinator.data.carbon_good

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "predicted_state": self.coordinator.data.predicted_state,
            "should_charge": self.coordinator.data.should_charge,
            "carbon_data_unavailable": self.coordinator.data.carbon_data_unavailable,
            "data_stale": self.coordinator.data.data_stale,
            "fossil_pct": self.coordinator.data.fossil_pct,
        }
