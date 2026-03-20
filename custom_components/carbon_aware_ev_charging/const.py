"""Constants for the Carbon-Aware EV Charging integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.selector import SelectOptionDict

DOMAIN = "carbon_aware_ev_charging"

# ── Config entry keys (entity IDs — stored in entry.data) ────────────────────
CONF_CO2_SENSOR = "co2_sensor"
CONF_FOSSIL_SENSOR = "fossil_sensor"
CONF_CHARGER_SWITCH = "charger_switch"
CONF_CHARGER_CONNECTED_ATTR = "charger_connected_attr"
CONF_CHARGER_NOT_CONNECTED_VALUE = "charger_not_connected_value"
CONF_CHARGER_POWER_SENSOR = "charger_power_sensor"
CONF_LED_LIGHT = "led_light"
CONF_LED_EFFECT_SELECT = "led_effect_select"

# ── Config entry keys (preferences — stored in entry.options) ─────────────────
# NOTE: Some of these values also serve as entity unique_id suffixes for dynamic
# entities (switches, fallback-window numbers).  Do not rename them without a
# config-entry migration — see ENTITY_ID_* constants below for the full picture.
CONF_CARBON_MODE = "carbon_mode"
CONF_CHARGE_MODE = "charge_mode"
CONF_DEPARTURE_HOUR = "departure_hour"
CONF_DEPARTURE_DAYS = "departure_days"
CONF_DRY_RUN = "dry_run"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_FALLBACK_WINDOW_1_START = "fallback_window_1_start"
CONF_FALLBACK_WINDOW_1_END = "fallback_window_1_end"
CONF_FALLBACK_WINDOW_1_ENABLED = "fallback_window_1_enabled"
CONF_FALLBACK_WINDOW_2_START = "fallback_window_2_start"
CONF_FALLBACK_WINDOW_2_END = "fallback_window_2_end"
CONF_FALLBACK_WINDOW_2_ENABLED = "fallback_window_2_enabled"

# ── Carbon sensitivity modes ──────────────────────────────────────────────────
CARBON_MODE_LENIENT = "Lenient"
CARBON_MODE_MODERATE = "Moderate"
CARBON_MODE_STRICT = "Strict"
CARBON_MODES = [CARBON_MODE_LENIENT, CARBON_MODE_MODERATE, CARBON_MODE_STRICT]

# Z-score thresholds per mode (normal-distribution rank in parens)
THRESHOLD_LENIENT = 0.92  # ~82 % of hours pass
THRESHOLD_MODERATE = 0.47  # ~68 % of hours pass
THRESHOLD_STRICT = -0.18  # ~43 % of hours pass

THRESHOLDS: dict[str, float] = {
    CARBON_MODE_LENIENT: THRESHOLD_LENIENT,
    CARBON_MODE_MODERATE: THRESHOLD_MODERATE,
    CARBON_MODE_STRICT: THRESHOLD_STRICT,
}

# Extra σ added to threshold when charger is already running (prevents flapping)
HYSTERESIS_SIGMA = 0.4

# Fossil fuel hard floor — gate stays closed above this %
FOSSIL_HARD_FLOOR = 75.0

# Rolling deque sizes (5-minute poll cadence)
READINGS_PER_DAY = 288
DEQUE_7D = 7 * READINGS_PER_DAY  # 2 016
DEQUE_30D = 30 * READINGS_PER_DAY  # 8 640

# Minimum charger dwell time before turning off (minutes)
MIN_DWELL_MINUTES = 15

# Minimum cooldown after turning off before turning back on (minutes)
MIN_COOLDOWN_MINUTES = 10

# Hours before departure to begin prep charging (bounded window)
DEPARTURE_PREP_HOURS = 3

# Sensor staleness thresholds
# Soft stale: log warning, flag in UI, but still use data for carbon gate
STALE_DATA_MINUTES = 30
# Hard unavailable: after this many minutes AND consecutive polls, treat as truly unavailable
STALE_HARD_MINUTES = 60
STALE_HARD_CONSECUTIVE = 3  # consecutive polls exceeding STALE_HARD_MINUTES

# Minutes a sensor must stay unavailable before raising an HA Repair issue
SENSOR_UNAVAILABLE_REPAIR_MINUTES = 30

# Default fallback windows (hours, 0-23)
DEFAULT_FALLBACK_WINDOW_1_START = 22  # overnight window: 22:00–06:00
DEFAULT_FALLBACK_WINDOW_1_END = 6
DEFAULT_FALLBACK_WINDOW_2_START = 11  # midday window: 11:00–15:00
DEFAULT_FALLBACK_WINDOW_2_END = 15

# ── Charge modes ──────────────────────────────────────────────────────────────
CHARGE_MODE_AUTO = "auto"
CHARGE_MODE_FORCE_ON = "force_on"
CHARGE_MODE_FORCE_OFF = "force_off"
CHARGE_MODES = [CHARGE_MODE_AUTO, CHARGE_MODE_FORCE_ON, CHARGE_MODE_FORCE_OFF]

# ── Predicted charging states ─────────────────────────────────────────────────
STATE_CARBON = "carbon"
STATE_SCHEDULED = "scheduled"
STATE_OVERRIDE = "override"
STATE_PAUSED = "paused"

CHARGEABLE_STATES = (STATE_CARBON, STATE_SCHEDULED, STATE_OVERRIDE)

# ── Charging status enum values (for device_class: enum sensor) ───────────────
STATUS_NOT_CONNECTED = "not_connected"
STATUS_FORCED_OFF = "forced_off"
STATUS_OVERRIDE = "override"
STATUS_LOW_CARBON = "low_carbon"
STATUS_DEPARTURE_PREP = "departure_prep"
STATUS_FALLBACK = "fallback"
STATUS_DATA_STALE = "data_stale"
STATUS_WAITING_FOR_DATA = "waiting_for_data"
STATUS_FOSSIL_HIGH = "fossil_high"
STATUS_GRID_DIRTY = "grid_dirty"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"

CHARGING_STATUSES: list[str] = [
    STATUS_NOT_CONNECTED,
    STATUS_FORCED_OFF,
    STATUS_OVERRIDE,
    STATUS_LOW_CARBON,
    STATUS_DEPARTURE_PREP,
    STATUS_FALLBACK,
    STATUS_DATA_STALE,
    STATUS_WAITING_FOR_DATA,
    STATUS_FOSSIL_HIGH,
    STATUS_GRID_DIRTY,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
]

# Maps status_enum → (predicted_state, chargeable).  Single source of truth so
# the decision chain and the state/chargeability derivation can never diverge.
STATUS_MAP: dict[str, tuple[str, bool]] = {
    STATUS_NOT_CONNECTED: (STATE_PAUSED, False),
    STATUS_FORCED_OFF: (STATE_PAUSED, False),
    STATUS_OVERRIDE: (STATE_OVERRIDE, True),
    STATUS_LOW_CARBON: (STATE_CARBON, True),
    STATUS_DEPARTURE_PREP: (STATE_SCHEDULED, True),
    STATUS_FALLBACK: (STATE_SCHEDULED, True),
    STATUS_DATA_STALE: (STATE_PAUSED, False),
    STATUS_WAITING_FOR_DATA: (STATE_PAUSED, False),
    STATUS_FOSSIL_HIGH: (STATE_PAUSED, False),
    STATUS_GRID_DIRTY: (STATE_PAUSED, False),
    STATUS_UNAVAILABLE: (STATE_PAUSED, False),
    STATUS_UNKNOWN: (STATE_PAUSED, False),
}

# ── LED HS colours per state ──────────────────────────────────────────────────
LED_COLOUR: dict[str, list[int]] = {
    STATE_CARBON: [120, 80],
    STATE_OVERRIDE: [35, 100],
    STATE_SCHEDULED: [0, 100],
    STATE_PAUSED: [0, 100],
}

# ── Persistent storage ────────────────────────────────────────────────────────
STORAGE_KEY = f"{DOMAIN}.rolling_stats"
STORAGE_VERSION = 1

# ── Entity unique ID suffixes ─────────────────────────────────────────────────
# Central registry — each value is appended to ``{entry.entry_id}_`` to form the
# entity's unique_id.  NEVER change an existing value; doing so would orphan the
# entity in HA and lose its history.  Dynamic entities (fallback-window numbers,
# switches) use the corresponding CONF_* string as their suffix — those constants
# are equally frozen for the same reason.
ENTITY_ID_Z_SCORE = "co2_z_score"
ENTITY_ID_CHARGING_STATUS = "ev_charging_status"
ENTITY_ID_CHARGE_RATE_KW = "ev_charge_rate_kw"
ENTITY_ID_CHARGE_CURRENT = "ev_charge_current"
ENTITY_ID_CONNECTED = "ev_connected"
ENTITY_ID_LOW_CARBON_NOW = "ev_low_carbon_now"
ENTITY_ID_CHARGE_MODE = "ev_charge_mode"
ENTITY_ID_CARBON_MODE = "ev_carbon_mode"
ENTITY_ID_DEPARTURE_HOUR = "ev_departure_hour"

# ── HA platform list ──────────────────────────────────────────────────────────
PLATFORMS = ["sensor", "binary_sensor", "select", "number", "switch"]

# ── Day-of-week options for selectors ─────────────────────────────────────────
DAY_OPTIONS: list[SelectOptionDict] = [
    SelectOptionDict(value="0", label="Monday"),
    SelectOptionDict(value="1", label="Tuesday"),
    SelectOptionDict(value="2", label="Wednesday"),
    SelectOptionDict(value="3", label="Thursday"),
    SelectOptionDict(value="4", label="Friday"),
    SelectOptionDict(value="5", label="Saturday"),
    SelectOptionDict(value="6", label="Sunday"),
]

# ── Preference defaults (options that are user-configurable) ──────────────────
# Single source of truth for coordinator, config_flow, and entity files.
PREFERENCE_DEFAULTS: dict[str, Any] = {
    CONF_CARBON_MODE: CARBON_MODE_MODERATE,
    CONF_CHARGE_MODE: CHARGE_MODE_AUTO,
    CONF_DEPARTURE_HOUR: 5,
    CONF_DEPARTURE_DAYS: ["0", "1", "2", "3", "4"],  # Mon–Fri
    CONF_DRY_RUN: False,
    CONF_NOTIFY_SERVICE: "",
    CONF_FALLBACK_WINDOW_1_START: DEFAULT_FALLBACK_WINDOW_1_START,
    CONF_FALLBACK_WINDOW_1_END: DEFAULT_FALLBACK_WINDOW_1_END,
    CONF_FALLBACK_WINDOW_1_ENABLED: True,
    CONF_FALLBACK_WINDOW_2_START: DEFAULT_FALLBACK_WINDOW_2_START,
    CONF_FALLBACK_WINDOW_2_END: DEFAULT_FALLBACK_WINDOW_2_END,
    CONF_FALLBACK_WINDOW_2_ENABLED: True,
}
