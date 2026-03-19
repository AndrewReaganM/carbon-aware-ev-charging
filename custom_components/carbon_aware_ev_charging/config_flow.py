"""Config flow for Carbon-Aware EV Charging."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,  # type: ignore[reportUnknownVariableType]
    EntitySelector,  # type: ignore[reportUnknownVariableType]
    EntitySelectorConfig,
    NumberSelector,  # type: ignore[reportUnknownVariableType]
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,  # type: ignore[reportUnknownVariableType]
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,  # type: ignore[reportUnknownVariableType]
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CARBON_MODES,
    CONF_CARBON_MODE,
    CONF_CHARGER_CONNECTED_ATTR,
    CONF_CHARGER_NOT_CONNECTED_VALUE,
    CONF_CHARGER_POWER_SENSOR,
    CONF_CHARGER_SWITCH,
    CONF_CO2_SENSOR,
    CONF_DEPARTURE_DAYS,
    CONF_DEPARTURE_HOUR,
    CONF_DRY_RUN,
    CONF_FALLBACK_WINDOW_1_END,
    CONF_FALLBACK_WINDOW_1_ENABLED,
    CONF_FALLBACK_WINDOW_1_START,
    CONF_FALLBACK_WINDOW_2_END,
    CONF_FALLBACK_WINDOW_2_ENABLED,
    CONF_FALLBACK_WINDOW_2_START,
    CONF_FOSSIL_SENSOR,
    CONF_LED_EFFECT_SELECT,
    CONF_LED_LIGHT,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
    PREFERENCE_DEFAULTS,
)

_DAY_OPTIONS = [
    SelectOptionDict(value="0", label="Monday"),
    SelectOptionDict(value="1", label="Tuesday"),
    SelectOptionDict(value="2", label="Wednesday"),
    SelectOptionDict(value="3", label="Thursday"),
    SelectOptionDict(value="4", label="Friday"),
    SelectOptionDict(value="5", label="Saturday"),
    SelectOptionDict(value="6", label="Sunday"),
]


class EVCarbonChargerConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Three-step config flow: sensors → LED → preferences."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    # ── Step 1 — Required sensors ─────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            for key in (CONF_CO2_SENSOR, CONF_FOSSIL_SENSOR, CONF_CHARGER_SWITCH):
                if not self.hass.states.get(user_input[key]):
                    errors[key] = "entity_not_found"

            power_sensor = user_input.get(CONF_CHARGER_POWER_SENSOR)
            if power_sensor and not self.hass.states.get(power_sensor):
                errors[CONF_CHARGER_POWER_SENSOR] = "entity_not_found"

            if not errors:
                self._data.update(user_input)
                return await self.async_step_led()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CO2_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_FOSSIL_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_CHARGER_SWITCH): EntitySelector(
                        EntitySelectorConfig(domain="switch")
                    ),
                    vol.Optional(
                        CONF_CHARGER_CONNECTED_ATTR, default="icon_name"
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(
                        CONF_CHARGER_NOT_CONNECTED_VALUE, default="CarNotConnected"
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(CONF_CHARGER_POWER_SENSOR): EntitySelector(
                        EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )

    # ── Step 2 — Optional LED ─────────────────────────────────────────────────

    async def async_step_led(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            for key in (CONF_LED_LIGHT, CONF_LED_EFFECT_SELECT):
                val = user_input.get(key)
                if val and not self.hass.states.get(val):
                    errors[key] = "entity_not_found"

            if not errors:
                self._data.update(user_input)
                return await self.async_step_prefs()

        return self.async_show_form(
            step_id="led",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_LED_LIGHT): EntitySelector(
                        EntitySelectorConfig(domain="light")
                    ),
                    vol.Optional(CONF_LED_EFFECT_SELECT): EntitySelector(
                        EntitySelectorConfig(domain="select")
                    ),
                }
            ),
            errors=errors,
        )

    # ── Step 3 — Preferences ──────────────────────────────────────────────────

    async def async_step_prefs(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            notify = user_input.get(CONF_NOTIFY_SERVICE, "").strip()
            if notify and not notify.startswith("notify."):
                errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"

            if not errors:
                self._options = {
                    CONF_CARBON_MODE: user_input[CONF_CARBON_MODE],
                    CONF_DEPARTURE_HOUR: int(user_input[CONF_DEPARTURE_HOUR]),
                    CONF_DEPARTURE_DAYS: user_input[CONF_DEPARTURE_DAYS],
                    CONF_FALLBACK_WINDOW_1_ENABLED: user_input[CONF_FALLBACK_WINDOW_1_ENABLED],
                    CONF_FALLBACK_WINDOW_1_START: int(user_input[CONF_FALLBACK_WINDOW_1_START]),
                    CONF_FALLBACK_WINDOW_1_END: int(user_input[CONF_FALLBACK_WINDOW_1_END]),
                    CONF_FALLBACK_WINDOW_2_ENABLED: user_input[CONF_FALLBACK_WINDOW_2_ENABLED],
                    CONF_FALLBACK_WINDOW_2_START: int(user_input[CONF_FALLBACK_WINDOW_2_START]),
                    CONF_FALLBACK_WINDOW_2_END: int(user_input[CONF_FALLBACK_WINDOW_2_END]),
                    CONF_DRY_RUN: user_input[CONF_DRY_RUN],
                    CONF_NOTIFY_SERVICE: notify,
                }
                return self.async_create_entry(
                    title="Carbon-Aware EV Charging",
                    data=self._data,
                    options=self._options,
                )

        return self.async_show_form(
            step_id="prefs",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CARBON_MODE, default=PREFERENCE_DEFAULTS[CONF_CARBON_MODE]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=CARBON_MODES,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_DEPARTURE_HOUR, default=PREFERENCE_DEFAULTS[CONF_DEPARTURE_HOUR]
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_DEPARTURE_DAYS, default=PREFERENCE_DEFAULTS[CONF_DEPARTURE_DAYS]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_DAY_OPTIONS,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_DRY_RUN, default=PREFERENCE_DEFAULTS[CONF_DRY_RUN]): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_ENABLED, default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_1_ENABLED]
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_START,
                        default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_1_START],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_END,
                        default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_1_END],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_ENABLED, default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_2_ENABLED]
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_START,
                        default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_2_START],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_END,
                        default=PREFERENCE_DEFAULTS[CONF_FALLBACK_WINDOW_2_END],
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_NOTIFY_SERVICE, default=PREFERENCE_DEFAULTS[CONF_NOTIFY_SERVICE]
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return EVCarbonChargerOptionsFlow(config_entry)


class EVCarbonChargerOptionsFlow(config_entries.OptionsFlow):
    """Options flow — change preferences without re-running the full wizard."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        opts = self._config_entry.options

        if user_input is not None:
            notify = user_input.get(CONF_NOTIFY_SERVICE, "").strip()
            if notify and not notify.startswith("notify."):
                errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **opts,
                        CONF_CARBON_MODE: user_input[CONF_CARBON_MODE],
                        CONF_DEPARTURE_HOUR: int(user_input[CONF_DEPARTURE_HOUR]),
                        CONF_DEPARTURE_DAYS: user_input[CONF_DEPARTURE_DAYS],
                        CONF_FALLBACK_WINDOW_1_ENABLED: user_input[CONF_FALLBACK_WINDOW_1_ENABLED],
                        CONF_FALLBACK_WINDOW_1_START: int(user_input[CONF_FALLBACK_WINDOW_1_START]),
                        CONF_FALLBACK_WINDOW_1_END: int(user_input[CONF_FALLBACK_WINDOW_1_END]),
                        CONF_FALLBACK_WINDOW_2_ENABLED: user_input[CONF_FALLBACK_WINDOW_2_ENABLED],
                        CONF_FALLBACK_WINDOW_2_START: int(user_input[CONF_FALLBACK_WINDOW_2_START]),
                        CONF_FALLBACK_WINDOW_2_END: int(user_input[CONF_FALLBACK_WINDOW_2_END]),
                        CONF_DRY_RUN: user_input[CONF_DRY_RUN],
                        CONF_NOTIFY_SERVICE: notify,
                    },
                )

        def _opt(key: str) -> Any:
            return opts.get(key, PREFERENCE_DEFAULTS[key])

        current_days = _opt(CONF_DEPARTURE_DAYS)
        # Normalise to list-of-strings for the selector
        current_days_str = [str(d) for d in current_days]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CARBON_MODE,
                        default=_opt(CONF_CARBON_MODE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=CARBON_MODES,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_DEPARTURE_HOUR,
                        default=_opt(CONF_DEPARTURE_HOUR),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_DEPARTURE_DAYS,
                        default=current_days_str,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_DAY_OPTIONS,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(
                        CONF_DRY_RUN,
                        default=_opt(CONF_DRY_RUN),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_ENABLED,
                        default=_opt(CONF_FALLBACK_WINDOW_1_ENABLED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_START,
                        default=_opt(CONF_FALLBACK_WINDOW_1_START),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_1_END,
                        default=_opt(CONF_FALLBACK_WINDOW_1_END),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_ENABLED,
                        default=_opt(CONF_FALLBACK_WINDOW_2_ENABLED),
                    ): BooleanSelector(),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_START,
                        default=_opt(CONF_FALLBACK_WINDOW_2_START),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_FALLBACK_WINDOW_2_END,
                        default=_opt(CONF_FALLBACK_WINDOW_2_END),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=23, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_NOTIFY_SERVICE,
                        default=_opt(CONF_NOTIFY_SERVICE),
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )
