"""Carbon-Aware EV Charging integration setup."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import DOMAIN, PLATFORMS, STORAGE_KEY, STORAGE_VERSION
from .coordinator import EVCarbonCoordinator

_LOGGER = logging.getLogger(__name__)
_MANIFEST = json.loads((Path(__file__).parent / "manifest.json").read_text())


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries to the current schema version."""
    if entry.version > 1:
        _LOGGER.error("Cannot downgrade from config version %s", entry.version)
        return False
    # VERSION 1 is the current schema — nothing to migrate.
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Carbon-Aware EV Charging from a config entry."""
    coordinator = EVCarbonCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # React immediately to CO2/fossil/charger state changes instead of
    # waiting for the next 5-minute poll.
    coordinator.async_subscribe_state_changes()
    entry.async_on_unload(coordinator.async_unsubscribe_state_changes)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register a logical device so all entities are grouped in the UI.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Carbon-Aware EV Charging",
        manufacturer="Carbon-Aware EV Charging",
        model=_MANIFEST.get("version", "unknown"),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persistent storage when the config entry is deleted."""
    store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")
    await store.async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh the coordinator when options change (no full reload)."""
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_refresh()
