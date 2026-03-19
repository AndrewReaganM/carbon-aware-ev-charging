"""Switch entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from functools import cached_property
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FALLBACK_WINDOW_1_ENABLED,
    CONF_FALLBACK_WINDOW_2_ENABLED,
    DOMAIN,
)
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EvFallbackWindowSwitch(
            coordinator, entry,
            key=CONF_FALLBACK_WINDOW_1_ENABLED,
            name="EV Fallback Window 1 Enabled",
            icon="mdi:weather-night",
        ),
        EvFallbackWindowSwitch(
            coordinator, entry,
            key=CONF_FALLBACK_WINDOW_2_ENABLED,
            name="EV Fallback Window 2 Enabled",
            icon="mdi:weather-sunny",
        ),
    ])


class EvFallbackWindowSwitch(
    CoordinatorEntity[EVCarbonCoordinator], SwitchEntity
):
    """Toggle to enable/disable a fallback charging window."""

    def __init__(
        self,
        coordinator: EVCarbonCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
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

    @property
    def is_on(self) -> bool:
        return bool(self._entry.options.get(self._key, True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._key: True},
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, self._key: False},
        )
        await self.coordinator.async_request_refresh()
