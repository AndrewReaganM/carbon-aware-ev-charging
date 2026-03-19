"""Select entities for Carbon-Aware EV Charging."""
from __future__ import annotations

from functools import cached_property

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CARBON_MODE_MODERATE,
    CARBON_MODES,
    CHARGE_MODE_AUTO,
    CHARGE_MODES,
    CONF_CARBON_MODE,
    CONF_CHARGE_MODE,
    DOMAIN,
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


class _EvSelectBase(CoordinatorEntity[EVCarbonCoordinator], SelectEntity):
    """Base class for select entities backed by config entry options."""

    def __init__(
        self, coordinator: EVCarbonCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
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

    async def _async_update_option(self, key: str, value: str) -> None:
        """Merge one option key and request a coordinator refresh."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, key: value},
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class EvChargeModeSelect(_EvSelectBase):
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
        return self._entry.options.get(CONF_CHARGE_MODE, CHARGE_MODE_AUTO)

    async def async_select_option(self, option: str) -> None:
        await self._async_update_option(CONF_CHARGE_MODE, option)


class EvCarbonModeSelect(_EvSelectBase):
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
        return self._entry.options.get(CONF_CARBON_MODE, CARBON_MODE_MODERATE)

    async def async_select_option(self, option: str) -> None:
        await self._async_update_option(CONF_CARBON_MODE, option)
