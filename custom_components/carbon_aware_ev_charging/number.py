"""Number entities for Carbon-Aware EV Charging."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import EVChargerBaseEntity
from .const import (
    CONF_DEPARTURE_HOUR,
    CONF_FALLBACK_WINDOW_1_END,
    CONF_FALLBACK_WINDOW_1_START,
    CONF_FALLBACK_WINDOW_2_END,
    CONF_FALLBACK_WINDOW_2_START,
    DOMAIN,
    ENTITY_ID_DEPARTURE_HOUR,
    PREFERENCE_DEFAULTS,
)
from .coordinator import EVCarbonCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EvDepartureHourNumber(coordinator, entry),
            EvFallbackWindowNumber(
                coordinator,
                entry,
                key=CONF_FALLBACK_WINDOW_1_START,
                name="EV Fallback Window 1 Start",
                icon="mdi:weather-night",
            ),
            EvFallbackWindowNumber(
                coordinator,
                entry,
                key=CONF_FALLBACK_WINDOW_1_END,
                name="EV Fallback Window 1 End",
                icon="mdi:weather-night",
            ),
            EvFallbackWindowNumber(
                coordinator,
                entry,
                key=CONF_FALLBACK_WINDOW_2_START,
                name="EV Fallback Window 2 Start",
                icon="mdi:weather-sunny",
            ),
            EvFallbackWindowNumber(
                coordinator,
                entry,
                key=CONF_FALLBACK_WINDOW_2_END,
                name="EV Fallback Window 2 End",
                icon="mdi:weather-sunny",
            ),
        ]
    )


class EvDepartureHourNumber(EVChargerBaseEntity, NumberEntity):
    """Hour-of-day at which departure-prep charging activates."""

    _attr_native_min_value = 0
    _attr_native_max_value = 23
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: EVCarbonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{ENTITY_ID_DEPARTURE_HOUR}"
        self._attr_name = "EV Departure Hour"

    @property
    def native_value(self) -> float:
        return float(
            self._entry.options.get(CONF_DEPARTURE_HOUR, PREFERENCE_DEFAULTS[CONF_DEPARTURE_HOUR])
        )

    async def async_set_native_value(self, value: float) -> None:
        await self._async_update_option(CONF_DEPARTURE_HOUR, int(value))


class EvFallbackWindowNumber(EVChargerBaseEntity, NumberEntity):
    """Configurable hour boundary for a fallback charging window."""

    _attr_native_min_value = 0
    _attr_native_max_value = 23
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX

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
    def native_value(self) -> float:
        return float(self._entry.options.get(self._key, PREFERENCE_DEFAULTS[self._key]))

    async def async_set_native_value(self, value: float) -> None:
        await self._async_update_option(self._key, int(value))
