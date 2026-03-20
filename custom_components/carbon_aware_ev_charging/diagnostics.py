"""Diagnostics support for Carbon-Aware EV Charging."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EVCarbonCoordinator

TO_REDACT = {"notify_service"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics data for a config entry."""
    coordinator: EVCarbonCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data

    return {
        "config": {k: v for k, v in entry.data.items() if k not in TO_REDACT},
        "options": {k: ("**REDACTED**" if k in TO_REDACT else v) for k, v in entry.options.items()},
        "coordinator": {
            "last_z_score": coordinator._last_z_score,
            "deque_7d_size": len(coordinator._deque_7d),
            "deque_30d_size": len(coordinator._deque_30d),
            "stale_hard_count": coordinator._stale_hard_count,
            "was_connected": coordinator._was_connected,
            "last_update_success": coordinator.last_update_success,
            "co2_unavailable_since": (
                coordinator._co2_unavailable_since.isoformat()
                if coordinator._co2_unavailable_since
                else None
            ),
            "fossil_unavailable_since": (
                coordinator._fossil_unavailable_since.isoformat()
                if coordinator._fossil_unavailable_since
                else None
            ),
        },
        "current_data": {
            "co2": data.co2 if data else None,
            "fossil_pct": data.fossil_pct if data else None,
            "z_score": data.z_score if data else None,
            "mean_7d": data.mean_7d if data else None,
            "stdev_7d": data.stdev_7d if data else None,
            "mean_30d": data.mean_30d if data else None,
            "stdev_30d": data.stdev_30d if data else None,
            "is_connected": data.is_connected if data else None,
            "carbon_good": data.carbon_good if data else None,
            "predicted_state": data.predicted_state if data else None,
            "should_charge": data.should_charge if data else None,
            "status_enum": data.status_enum if data else None,
            "status_reason": data.status_reason if data else None,
            "data_stale": data.data_stale if data else None,
            "carbon_data_unavailable": data.carbon_data_unavailable if data else None,
        },
    }
