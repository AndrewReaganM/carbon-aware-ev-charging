"""Config flow tests for Carbon-Aware EV Charging."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.carbon_aware_ev_charging.const import (
    CONF_CARBON_MODE,
    CONF_CHARGER_CONNECTED_ATTR,
    CONF_CHARGER_NOT_CONNECTED_VALUE,
    CONF_CHARGER_SWITCH,
    CONF_CO2_SENSOR,
    CONF_DEPARTURE_DAYS,
    CONF_DEPARTURE_HOUR,
    CONF_DRY_RUN,
    CONF_FOSSIL_SENSOR,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

VALID_STEP1 = {
    CONF_CO2_SENSOR: "sensor.co2",
    CONF_FOSSIL_SENSOR: "sensor.fossil",
    CONF_CHARGER_SWITCH: "switch.charger",
    CONF_CHARGER_CONNECTED_ATTR: "icon_name",
    CONF_CHARGER_NOT_CONNECTED_VALUE: "CarNotConnected",
}

VALID_STEP2 = {}  # no LED entities

VALID_STEP3 = {
    CONF_CARBON_MODE: "Moderate",
    CONF_DEPARTURE_HOUR: 5,
    CONF_DEPARTURE_DAYS: ["2", "3"],
    CONF_DRY_RUN: False,
    CONF_NOTIFY_SERVICE: "",
}


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.co2", "200")
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off")


# ── Happy path ─────────────────────────────────────────────────────────────────


async def test_full_config_flow(hass: HomeAssistant) -> None:
    """A complete three-step config flow creates a config entry."""
    _seed_states(hass)

    with patch(
        "custom_components.carbon_aware_ev_charging.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP1)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "led"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP2)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "prefs"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP3)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Carbon-Aware EV Charging"
        assert result["data"][CONF_CO2_SENSOR] == "sensor.co2"
        assert result["options"][CONF_CARBON_MODE] == "Moderate"


# ── Validation — unknown entity ────────────────────────────────────────────────


async def test_invalid_co2_sensor_shows_error(hass: HomeAssistant) -> None:
    """Entering an entity that doesn't exist shows entity_not_found error."""
    hass.states.async_set("sensor.fossil", "40")
    hass.states.async_set("switch.charger", "off")
    # sensor.co2 intentionally NOT set

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **VALID_STEP1,
            CONF_CO2_SENSOR: "sensor.nonexistent",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"].get(CONF_CO2_SENSOR) == "entity_not_found"


async def test_invalid_notify_service_shows_error(hass: HomeAssistant) -> None:
    """notify_service value without 'notify.' prefix triggers an error."""
    _seed_states(hass)

    with patch(
        "custom_components.carbon_aware_ev_charging.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP1)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP2)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**VALID_STEP3, CONF_NOTIFY_SERVICE: "mobile_app_my_phone"},  # missing prefix
        )
        assert result["type"] == FlowResultType.FORM
        assert result["errors"].get(CONF_NOTIFY_SERVICE) == "invalid_notify_service"


# ── Options flow ───────────────────────────────────────────────────────────────


async def test_options_flow_updates_carbon_mode(hass: HomeAssistant) -> None:
    """Options flow lets user change carbon_mode without re-running the wizard."""
    _seed_states(hass)

    with patch(
        "custom_components.carbon_aware_ev_charging.async_setup_entry",
        return_value=True,
    ):
        # Create a config entry first
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP1)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP2)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], VALID_STEP3)
        assert result["type"] == FlowResultType.CREATE_ENTRY

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_CARBON_MODE] == "Moderate"

    with patch(
        "custom_components.carbon_aware_ev_charging.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_CARBON_MODE: "Strict",
                CONF_DEPARTURE_HOUR: 6,
                CONF_DEPARTURE_DAYS: ["1", "2"],
                CONF_DRY_RUN: True,
                CONF_NOTIFY_SERVICE: "",
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_CARBON_MODE] == "Strict"
    assert entry.options[CONF_DRY_RUN] is True
