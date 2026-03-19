"""Switch entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import EVChargerBaseEntity
from .const import (
    CONF_FALLBACK_WINDOW_1_ENABLED,
    CONF_FALLBACK_WINDOW_2_ENABLED,
    DOMAIN,
    PREFERENCE_DEFAULTS,
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


class EvFallbackWindowSwitch(EVChargerBaseEntity, SwitchEntity):
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
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(self._entry.options.get(self._key, PREFERENCE_DEFAULTS[self._key]))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_update_option(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_update_option(self._key, False)
