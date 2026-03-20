"""Shared base entity for Carbon-Aware EV Charging."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EVCarbonCoordinator, EVCarbonData


class EVChargerBaseEntity(CoordinatorEntity[EVCarbonCoordinator]):
    """Shared base: ties every entity to the integration device."""

    def __init__(self, coordinator: EVCarbonCoordinator, entry: ConfigEntry) -> None:
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

    async def _async_update_option(self, key: str, value: Any) -> None:
        """Merge one option key and request a coordinator refresh."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, key: value},
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
