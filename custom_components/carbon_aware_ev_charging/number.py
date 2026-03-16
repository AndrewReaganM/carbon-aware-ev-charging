"""Number entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from functools import cached_property

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEPARTURE_HOUR, DOMAIN
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EvDepartureHourNumber(coordinator, entry)])


class EvDepartureHourNumber(
    CoordinatorEntity[EVCarbonCoordinator], NumberEntity
):
    """Hour-of-day at which departure-prep charging activates."""

    _attr_native_min_value = 0
    _attr_native_max_value = 23
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:clock-start"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ev_departure_hour"
        self._attr_name = "EV Departure Hour"
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
    def native_value(self) -> float:
        return float(self._entry.options.get(CONF_DEPARTURE_HOUR, 5))

    async def async_set_native_value(self, value: float) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_DEPARTURE_HOUR: int(value)},
        )
        await self.coordinator.async_request_refresh()
