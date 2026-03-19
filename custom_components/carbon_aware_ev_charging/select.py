"""Select entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entity import EVChargerBaseEntity
from .const import (
    CARBON_MODES,
    CHARGE_MODES,
    CONF_CARBON_MODE,
    CONF_CHARGE_MODE,
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
    async_add_entities(
        [
            EvChargeModeSelect(coordinator, entry),
            EvCarbonModeSelect(coordinator, entry),
        ]
    )


class EvChargeModeSelect(EVChargerBaseEntity, SelectEntity):
    """Control whether the integration charges automatically, always, or never."""

    _attr_options = CHARGE_MODES
    _attr_icon = "mdi:ev-station"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_charge_mode"
        self._attr_name = "EV Charge Mode"

    @property
    def current_option(self) -> str:  # type: ignore[override]
        return self._entry.options.get(CONF_CHARGE_MODE, PREFERENCE_DEFAULTS[CONF_CHARGE_MODE])

    @property
    def available(self) -> bool:  # type: ignore[override]
        return super().available

    async def async_select_option(self, option: str) -> None:
        await self._async_update_option(CONF_CHARGE_MODE, option)


class EvCarbonModeSelect(EVChargerBaseEntity, SelectEntity):
    """Select the carbon sensitivity threshold (Lenient / Moderate / Strict)."""

    _attr_options = CARBON_MODES
    _attr_icon = "mdi:leaf-circle"

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ev_carbon_mode"
        self._attr_name = "EV Carbon Sensitivity"

    @property
    def current_option(self) -> str:  # type: ignore[override]
        return self._entry.options.get(CONF_CARBON_MODE, PREFERENCE_DEFAULTS[CONF_CARBON_MODE])

    @property
    def available(self) -> bool:  # type: ignore[override]
        return super().available

    async def async_select_option(self, option: str) -> None:
        await self._async_update_option(CONF_CARBON_MODE, option)
