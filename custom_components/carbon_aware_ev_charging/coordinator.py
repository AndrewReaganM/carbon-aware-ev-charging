"""DataUpdateCoordinator for Carbon-Aware EV Charging."""

from __future__ import annotations

import contextlib
import logging
import re
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceNotFound
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CHARGE_MODE_FORCE_OFF,
    CHARGE_MODE_FORCE_ON,
    CONF_CARBON_MODE,
    CONF_CHARGE_MODE,
    CONF_CHARGER_CONNECTED_ATTR,
    CONF_CHARGER_NOT_CONNECTED_VALUE,
    CONF_CHARGER_POWER_SENSOR,
    CONF_CHARGER_SWITCH,
    CONF_CO2_SENSOR,
    CONF_DEPARTURE_DAYS,
    CONF_DEPARTURE_HOUR,
    CONF_DRY_RUN,
    CONF_FALLBACK_WINDOW_1_ENABLED,
    CONF_FALLBACK_WINDOW_1_END,
    CONF_FALLBACK_WINDOW_1_START,
    CONF_FALLBACK_WINDOW_2_ENABLED,
    CONF_FALLBACK_WINDOW_2_END,
    CONF_FALLBACK_WINDOW_2_START,
    CONF_FOSSIL_SENSOR,
    CONF_LED_EFFECT_SELECT,
    CONF_LED_LIGHT,
    CONF_NOTIFY_SERVICE,
    CONF_ROADTRIP_CALENDARS,
    CONF_ROADTRIP_CHARGE_LIMIT_ENTITY,
    CONF_ROADTRIP_DEFAULT_LEAD_HOURS,
    CONF_ROADTRIP_PREFIX,
    CONF_ROADTRIP_SOC_SENSOR,
    DEPARTURE_PREP_HOURS,
    DEQUE_7D,
    DEQUE_30D,
    DOMAIN,
    FOSSIL_HARD_FLOOR,
    HYSTERESIS_SIGMA,
    LED_COLOUR,
    MIN_COOLDOWN_MINUTES,
    MIN_DWELL_MINUTES,
    PREFERENCE_DEFAULTS,
    ROADTRIP_LOOKAHEAD_HOURS,
    SENSOR_UNAVAILABLE_REPAIR_MINUTES,
    STALE_DATA_MINUTES,
    STALE_HARD_CONSECUTIVE,
    STALE_HARD_MINUTES,
    STATE_PAUSED,
    STATUS_DATA_STALE,
    STATUS_DEPARTURE_PREP,
    STATUS_FALLBACK,
    STATUS_FORCED_OFF,
    STATUS_FOSSIL_HIGH,
    STATUS_GRID_DIRTY,
    STATUS_LOW_CARBON,
    STATUS_MAP,
    STATUS_NOT_CONNECTED,
    STATUS_OVERRIDE,
    STATUS_ROADTRIP_PREP,
    STATUS_WAITING_FOR_DATA,
    STORAGE_KEY,
    STORAGE_VERSION,
    THRESHOLDS,
)

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)

_UNAVAILABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})

_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Regex: [PREFIX optional_soc optional_lead]
# Examples: [IONIQ 90% 4h]  [IONIQ 80%]  [IONIQ 6h]  [IONIQ]
_ROADTRIP_TITLE_RE = re.compile(
    r"\[(?P<prefix>[^\]0-9%h]+?)"  # prefix (non-greedy, no digits/% chars)
    r"(?:\s+(?P<soc>\d+)%)?"  # optional: " 90%"
    r"(?:\s+(?P<lead>\d+)h)?"  # optional: " 4h"
    r"\]",
    re.IGNORECASE,
)


def _in_hour_window(hour: int, start: int, end: int) -> bool:
    """Return True if *hour* falls inside a [start, end) window.

    Handles midnight wrap-around: start=22, end=6 → 22..23 + 0..5.
    Returns False when start == end (window disabled).
    """
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraps midnight
    return hour >= start or hour < end


@dataclass
class RoadtripEvent:
    """A parsed roadtrip calendar event driving prep charging."""

    summary: str  # original calendar event title
    start: datetime  # event start time (timezone-aware)
    soc_target: int | None  # parsed SoC target %, or None if not in title
    lead_hours: int  # prep window in hours (from title or default)

    @property
    def prep_start(self) -> datetime:
        """Time at which prep charging should begin."""
        return self.start - timedelta(hours=self.lead_hours)


@dataclass
class EVCarbonData:
    """Snapshot of all coordinator-derived state."""

    co2: float | None = None
    fossil_pct: float | None = None
    z_score: float | None = None
    mean_7d: float | None = None
    stdev_7d: float | None = None
    mean_30d: float | None = None
    stdev_30d: float | None = None
    is_connected: bool = False
    carbon_good: bool = False
    carbon_data_unavailable: bool = True
    data_stale: bool = False
    predicted_state: str = STATE_PAUSED
    should_charge: bool = False
    status_enum: str = "unknown"
    status_reason: str = "Unknown"
    charge_rate_kw: float | None = None
    charge_current_a: int | None = None
    active_roadtrip: RoadtripEvent | None = None


@dataclass
class _ResolvedConfig:
    """Resolved configuration — options override data, with defaults."""

    co2_entity: str
    fossil_entity: str
    charger_entity: str
    connected_attr: str
    not_connected_val: str
    power_entity: str | None
    led_light: str | None
    led_effect_select: str | None
    carbon_mode: str
    charge_mode: str
    departure_hour: int
    departure_days: list[int]
    dry_run: bool
    notify_service: str
    fb1_start: int
    fb1_end: int
    fb2_start: int
    fb2_end: int
    fb1_enabled: bool
    fb2_enabled: bool
    roadtrip_calendars: list[str]
    roadtrip_prefix: str
    roadtrip_default_lead_hours: int
    roadtrip_soc_sensor: str | None
    roadtrip_charge_limit_entity: str | None


@dataclass
class _SensorReadings:
    """Parsed sensor states from HA."""

    co2: float | None
    fossil_pct: float | None
    carbon_data_unavailable: bool
    data_stale: bool
    is_connected: bool
    charger_is_on: bool
    charger_state: Any  # HA State object (needed for dwell/cooldown timing)
    charge_rate_kw: float | None
    charge_current_a: int | None


@dataclass
class _Statistics:
    """Rolling statistics and derived Z-score."""

    mean_7d: float | None
    stdev_7d: float | None
    mean_30d: float | None
    stdev_30d: float | None
    z_score: float | None


@dataclass
class _ChargingDecision:
    """Output of the charging decision engine."""

    predicted_state: str
    should_charge: bool
    carbon_good: bool
    status_enum: str
    status_reason: str
    led_state: str  # predicted_state ignoring connection (for LED colour)
    active_roadtrip: RoadtripEvent | None = field(default=None)


class EVCarbonCoordinator(DataUpdateCoordinator[EVCarbonData]):
    """Coordinator: polls CO2 data, computes Z-score, controls charger and LED."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self._deque_7d: deque[tuple[float, float]] = deque(maxlen=DEQUE_7D)
        self._deque_30d: deque[tuple[float, float]] = deque(maxlen=DEQUE_30D)
        self._last_z_score: float | None = None
        self._was_connected: bool = False
        self._stale_hard_count: int = 0
        self._last_led_state: tuple[str, bool] | None = None
        self._unsub_state_listeners: list = []
        self._co2_unavailable_since: datetime | None = None
        self._fossil_unavailable_since: datetime | None = None

    @callback
    def async_subscribe_state_changes(self) -> None:
        """Subscribe to state changes on monitored entities for reactive refresh."""
        cfg = self.entry.data
        entities = [
            cfg[CONF_CO2_SENSOR],
            cfg[CONF_FOSSIL_SENSOR],
            cfg[CONF_CHARGER_SWITCH],
        ]

        @callback
        def _on_state_change(event: Event[EventStateChangedData]) -> None:
            """Request a coordinator refresh when a monitored entity changes."""
            _LOGGER.debug(
                "[EV] Reactive refresh triggered by %s",
                event.data.get("entity_id"),
            )
            self.hass.async_create_task(self.async_request_refresh())

        self._unsub_state_listeners.append(
            async_track_state_change_event(self.hass, entities, _on_state_change)
        )

    @callback
    def async_unsubscribe_state_changes(self) -> None:
        """Remove all state-change listeners."""
        for unsub in self._unsub_state_listeners:
            unsub()
        self._unsub_state_listeners.clear()

    async def async_config_entry_first_refresh(self) -> None:
        """Load persisted rolling history before first poll, then refresh."""
        stored = await self._store.async_load()
        if stored:
            for item in stored.get("deque_7d", []):
                self._deque_7d.append((item[0], item[1]))
            for item in stored.get("deque_30d", []):
                self._deque_30d.append((item[0], item[1]))
            self._last_z_score = stored.get("last_z_score")
            _LOGGER.debug(
                "[EV] Restored %d 7d and %d 30d history points from storage",
                len(self._deque_7d),
                len(self._deque_30d),
            )

        # If deques are still empty (first install), try backfilling from
        # the HA recorder so we don't have to wait 7 days for useful data.
        if not self._deque_7d:
            await self._async_backfill_from_recorder()

        await super().async_config_entry_first_refresh()

    async def _async_backfill_from_recorder(self) -> None:
        """Seed rolling deques from the recorder's existing CO₂ history."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                state_changes_during_period,
            )
            from sqlalchemy.exc import SQLAlchemyError
        except ImportError:
            _LOGGER.debug("[EV] Recorder not available — skipping history backfill")
            return

        try:
            recorder = get_instance(self.hass)
        except (KeyError, RuntimeError):
            _LOGGER.debug("[EV] Recorder not running — skipping history backfill")
            return

        co2_entity: str = self.entry.data[CONF_CO2_SENSOR]

        now = dt_util.utcnow()
        start_30d = now - timedelta(days=30)
        start_7d = now - timedelta(days=7)

        try:
            history: dict[str, list] = await recorder.async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start_30d,
                now,
                co2_entity,
                True,  # no_attributes — we only need the state value
            )
        except (OSError, RuntimeError, SQLAlchemyError) as err:
            # OSError: filesystem-level failure; RuntimeError: no DB session;
            # SQLAlchemyError: any DB-layer error (wraps sqlite3 errors internally).
            _LOGGER.warning(
                "[EV] Recorder query failed — skipping history backfill: %s",
                err,
                exc_info=True,
            )
            return

        self._load_recorder_states(history.get(co2_entity, []), start_7d)

    def _load_recorder_states(
        self,
        states: list,
        start_7d: datetime,
    ) -> None:
        """Parse recorder State objects into the rolling deques."""
        if not states:
            _LOGGER.debug("[EV] No recorder history found — nothing to backfill")
            return

        count_30d = 0
        count_7d = 0
        cutoff_7d = start_7d.timestamp()
        for state in states:
            raw = state.state if hasattr(state, "state") else str(state)
            if raw in _UNAVAILABLE_STATES:
                continue
            try:
                co2_val = float(raw)
            except (ValueError, TypeError):
                continue
            ts = state.last_updated.timestamp() if hasattr(state, "last_updated") else 0.0
            self._deque_30d.append((ts, co2_val))
            count_30d += 1
            if ts >= cutoff_7d:
                self._deque_7d.append((ts, co2_val))
                count_7d += 1

        if count_30d:
            _LOGGER.info(
                "[EV] Backfilled %d 30d and %d 7d history points from recorder",
                count_30d,
                count_7d,
            )
            self.hass.async_create_background_task(
                self._async_save_history(), "ev_save_history_backfill"
            )

    # ── Main update ───────────────────────────────────────────────────────────

    async def _async_update_data(self) -> EVCarbonData:
        """Fetch state, update stats, compute derived values, control devices."""
        cfg = self._resolve_config()
        sensors = self._read_sensors(cfg)
        self._check_sensor_availability(cfg, sensors)
        stats = self._update_statistics(sensors.co2)
        active_roadtrip = await self._async_find_active_roadtrip(cfg)
        decision = self._evaluate_charging(cfg, sensors, stats, active_roadtrip)
        await self._control_devices(cfg, sensors, decision, stats)

        _LOGGER.info(
            "[EV] status=%s predicted=%s should_charge=%s mode=%s car=%s "
            "carbon=%s z_score=%s unavailable=%s stale=%s dry_run=%s roadtrip=%s",
            decision.status_enum,
            decision.predicted_state,
            decision.should_charge,
            cfg.charge_mode,
            sensors.is_connected,
            decision.carbon_good,
            stats.z_score,
            sensors.carbon_data_unavailable,
            sensors.data_stale,
            cfg.dry_run,
            active_roadtrip.summary if active_roadtrip else None,
        )

        return EVCarbonData(
            co2=sensors.co2,
            fossil_pct=sensors.fossil_pct,
            z_score=stats.z_score,
            mean_7d=stats.mean_7d,
            stdev_7d=stats.stdev_7d,
            mean_30d=stats.mean_30d,
            stdev_30d=stats.stdev_30d,
            is_connected=sensors.is_connected,
            carbon_good=decision.carbon_good,
            carbon_data_unavailable=sensors.carbon_data_unavailable,
            data_stale=sensors.data_stale,
            predicted_state=decision.predicted_state,
            should_charge=decision.should_charge,
            status_enum=decision.status_enum,
            status_reason=decision.status_reason,
            charge_rate_kw=sensors.charge_rate_kw,
            charge_current_a=sensors.charge_current_a,
            active_roadtrip=active_roadtrip,
        )

    # ── Extracted logic ───────────────────────────────────────────────────────

    def _resolve_config(self) -> _ResolvedConfig:
        """Merge entry.data and entry.options with correct precedence."""
        cfg = self.entry.data
        opts = self.entry.options

        def _pref(key: str) -> Any:
            """Options override data, with fallback to PREFERENCE_DEFAULTS."""
            return opts.get(key, cfg.get(key, PREFERENCE_DEFAULTS[key]))

        departure_days_raw = _pref(CONF_DEPARTURE_DAYS)

        return _ResolvedConfig(
            co2_entity=cfg[CONF_CO2_SENSOR],
            fossil_entity=cfg[CONF_FOSSIL_SENSOR],
            charger_entity=cfg[CONF_CHARGER_SWITCH],
            connected_attr=cfg.get(CONF_CHARGER_CONNECTED_ATTR, "icon_name"),
            not_connected_val=cfg.get(CONF_CHARGER_NOT_CONNECTED_VALUE, "CarNotConnected"),
            power_entity=cfg.get(CONF_CHARGER_POWER_SENSOR),
            led_light=cfg.get(CONF_LED_LIGHT),
            led_effect_select=cfg.get(CONF_LED_EFFECT_SELECT),
            carbon_mode=_pref(CONF_CARBON_MODE),
            charge_mode=_pref(CONF_CHARGE_MODE),
            departure_hour=int(_pref(CONF_DEPARTURE_HOUR)),
            departure_days=[int(d) for d in departure_days_raw],
            dry_run=bool(_pref(CONF_DRY_RUN)),
            notify_service=_pref(CONF_NOTIFY_SERVICE),
            fb1_start=int(_pref(CONF_FALLBACK_WINDOW_1_START)),
            fb1_end=int(_pref(CONF_FALLBACK_WINDOW_1_END)),
            fb2_start=int(_pref(CONF_FALLBACK_WINDOW_2_START)),
            fb2_end=int(_pref(CONF_FALLBACK_WINDOW_2_END)),
            fb1_enabled=bool(_pref(CONF_FALLBACK_WINDOW_1_ENABLED)),
            fb2_enabled=bool(_pref(CONF_FALLBACK_WINDOW_2_ENABLED)),
            roadtrip_calendars=list(_pref(CONF_ROADTRIP_CALENDARS)),
            roadtrip_prefix=str(_pref(CONF_ROADTRIP_PREFIX)).strip(),
            roadtrip_default_lead_hours=int(_pref(CONF_ROADTRIP_DEFAULT_LEAD_HOURS)),
            roadtrip_soc_sensor=_pref(CONF_ROADTRIP_SOC_SENSOR) or None,
            roadtrip_charge_limit_entity=_pref(CONF_ROADTRIP_CHARGE_LIMIT_ENTITY) or None,
        )

    def _read_sensors(self, cfg: _ResolvedConfig) -> _SensorReadings:
        """Read and parse all sensor states from HA."""
        co2_state = self.hass.states.get(cfg.co2_entity)
        fossil_state = self.hass.states.get(cfg.fossil_entity)
        charger_state = self.hass.states.get(cfg.charger_entity)

        co2: float | None = None
        fossil_pct: float | None = None
        carbon_data_unavailable = True

        _LOGGER.debug(
            "[EV] raw sensor states: co2_entity=%s state=%r  "
            "fossil_entity=%s state=%r  charger_entity=%s state=%r",
            cfg.co2_entity,
            co2_state.state if co2_state else "MISSING",
            cfg.fossil_entity,
            fossil_state.state if fossil_state else "MISSING",
            cfg.charger_entity,
            charger_state.state if charger_state else "MISSING",
        )

        if co2_state and co2_state.state not in _UNAVAILABLE_STATES:
            try:
                co2 = float(co2_state.state)
                carbon_data_unavailable = False
            except ValueError:
                pass

        if fossil_state and fossil_state.state not in _UNAVAILABLE_STATES:
            try:
                fossil_pct = float(fossil_state.state)
            except ValueError:
                carbon_data_unavailable = True  # fossil data broken → treat as unavailable

        if co2 is None or fossil_pct is None:
            carbon_data_unavailable = True

        # Tiered staleness check
        data_stale = False
        hard_stale = False
        now = dt_util.utcnow()
        soft_threshold = now - timedelta(minutes=STALE_DATA_MINUTES)
        hard_threshold = now - timedelta(minutes=STALE_HARD_MINUTES)
        for _entity, _state in (
            (cfg.co2_entity, co2_state),
            (cfg.fossil_entity, fossil_state),
        ):
            if _state is not None and _state.state not in _UNAVAILABLE_STATES:
                if _state.last_updated < hard_threshold:
                    _LOGGER.warning(
                        "[EV] Sensor %s is hard-stale (last_updated=%s)",
                        _entity,
                        _state.last_updated.isoformat(),
                    )
                    data_stale = True
                    hard_stale = True
                elif _state.last_updated < soft_threshold:
                    _LOGGER.warning(
                        "[EV] Sensor %s is soft-stale (last_updated=%s)",
                        _entity,
                        _state.last_updated.isoformat(),
                    )
                    data_stale = True

        # Hard unavailable requires N consecutive hard-stale polls
        if hard_stale:
            self._stale_hard_count += 1
        else:
            self._stale_hard_count = 0

        if self._stale_hard_count >= STALE_HARD_CONSECUTIVE:
            carbon_data_unavailable = True

        is_connected = False
        if charger_state:
            is_connected = charger_state.attributes.get(cfg.connected_attr) != cfg.not_connected_val

        # Charger aux sensors
        charge_rate_kw: float | None = None
        if cfg.power_entity:
            ps = self.hass.states.get(cfg.power_entity)
            if ps and ps.state not in _UNAVAILABLE_STATES:
                with contextlib.suppress(ValueError):
                    charge_rate_kw = round(float(ps.state) / 1000, 2)

        charge_current_a: int | None = None
        if charger_state:
            raw = charger_state.attributes.get("charging_rate")
            if raw is not None:
                with contextlib.suppress(TypeError, ValueError):
                    charge_current_a = int(raw)

        charger_is_on = charger_state is not None and charger_state.state == "on"

        return _SensorReadings(
            co2=co2,
            fossil_pct=fossil_pct,
            carbon_data_unavailable=carbon_data_unavailable,
            data_stale=data_stale,
            is_connected=is_connected,
            charger_is_on=charger_is_on,
            charger_state=charger_state,
            charge_rate_kw=charge_rate_kw,
            charge_current_a=charge_current_a,
        )

    def _check_sensor_availability(
        self,
        cfg: _ResolvedConfig,
        sensors: _SensorReadings,
    ) -> None:
        """Raise or dismiss HA Repair issues for prolonged sensor unavailability."""
        now = dt_util.utcnow()
        threshold = timedelta(minutes=SENSOR_UNAVAILABLE_REPAIR_MINUTES)

        for entity_id, value, attr in (
            (cfg.co2_entity, sensors.co2, "_co2_unavailable_since"),
            (cfg.fossil_entity, sensors.fossil_pct, "_fossil_unavailable_since"),
        ):
            issue_id = f"sensor_unavailable_{entity_id}"
            since: datetime | None = getattr(self, attr)

            if value is None:
                # Sensor is unavailable — start tracking if not already
                if since is None:
                    setattr(self, attr, now)
                elif now - since >= threshold:
                    async_create_issue(
                        self.hass,
                        DOMAIN,
                        issue_id,
                        is_fixable=False,
                        severity=IssueSeverity.WARNING,
                        translation_key="sensor_unavailable",
                        translation_placeholders={
                            "entity_id": entity_id,
                            "minutes": str(SENSOR_UNAVAILABLE_REPAIR_MINUTES),
                        },
                    )
            else:
                # Sensor recovered — clear tracking and dismiss any issue
                if since is not None:
                    setattr(self, attr, None)
                    async_delete_issue(self.hass, DOMAIN, issue_id)

    def _update_statistics(self, co2: float | None) -> _Statistics:
        """Update rolling deques, compute mean/stdev/z-score."""
        # Time-based prune: discard entries older than the window regardless of count.
        now_ts = dt_util.utcnow().timestamp()
        cutoff_7d = now_ts - 7 * 86_400
        cutoff_30d = now_ts - 30 * 86_400
        while self._deque_7d and self._deque_7d[0][0] < cutoff_7d:
            self._deque_7d.popleft()
        while self._deque_30d and self._deque_30d[0][0] < cutoff_30d:
            self._deque_30d.popleft()

        if co2 is not None:
            ts = dt_util.utcnow().timestamp()
            self._deque_7d.append((ts, co2))
            self._deque_30d.append((ts, co2))
            self.hass.async_create_background_task(
                self._async_save_history(), "ev_save_history_update"
            )

        vals_7d = [v for _, v in self._deque_7d]
        mean_7d: float | None = None
        stdev_7d: float | None = None
        if len(vals_7d) >= 2:
            mean_7d = statistics.mean(vals_7d)
            stdev_7d = statistics.stdev(vals_7d)

        vals_30d = [v for _, v in self._deque_30d]
        mean_30d: float | None = None
        stdev_30d: float | None = None
        if len(vals_30d) >= 2:
            mean_30d = statistics.mean(vals_30d)
            stdev_30d = statistics.stdev(vals_30d)

        _LOGGER.debug(
            "[EV] z_score inputs: co2=%s mean_7d=%s stdev_7d=%s "
            "mean_30d=%s stdev_30d=%s deque_7d_len=%d deque_30d_len=%d",
            co2,
            mean_7d,
            stdev_7d,
            mean_30d,
            stdev_30d,
            len(self._deque_7d),
            len(self._deque_30d),
        )

        z_score: float | None = None
        if co2 is not None and mean_7d is not None:
            # stdev=0 means all readings identical → z_score is 0 by definition
            z_score = round((co2 - mean_7d) / stdev_7d, 2) if stdev_7d else 0.0
            self._last_z_score = z_score
        else:
            _LOGGER.debug(
                "[EV] z_score blocked: co2_none=%s mean_none=%s — holding last=%s",
                co2 is None,
                mean_7d is None,
                self._last_z_score,
            )
            z_score = self._last_z_score  # hold last good value

        return _Statistics(
            mean_7d=mean_7d,
            stdev_7d=stdev_7d,
            mean_30d=mean_30d,
            stdev_30d=stdev_30d,
            z_score=z_score,
        )

    def _evaluate_charging(
        self,
        cfg: _ResolvedConfig,
        sensors: _SensorReadings,
        stats: _Statistics,
        active_roadtrip: RoadtripEvent | None,
    ) -> _ChargingDecision:
        """Determine status_enum; derive predicted_state and should_charge."""
        # Carbon gate (with hysteresis)
        threshold = THRESHOLDS[cfg.carbon_mode]
        effective_threshold = threshold + HYSTERESIS_SIGMA if sensors.charger_is_on else threshold
        carbon_good = (
            not sensors.carbon_data_unavailable
            and stats.z_score is not None
            and sensors.fossil_pct is not None
            and stats.z_score < effective_threshold
            and sensors.fossil_pct < FOSSIL_HARD_FLOOR
        )

        # Time windows
        now = dt_util.now()
        hour = now.hour
        weekday = now.weekday()
        fallback_window = (
            cfg.fb1_enabled and _in_hour_window(hour, cfg.fb1_start, cfg.fb1_end)
        ) or (cfg.fb2_enabled and _in_hour_window(hour, cfg.fb2_start, cfg.fb2_end))
        departure_prep_start = (cfg.departure_hour - DEPARTURE_PREP_HOURS) % 24
        # When the prep window crosses midnight (e.g. departure=01:00 → window=[22,01)),
        # hours after midnight belong to the *next* calendar day.  Check yesterday's
        # weekday for the post-midnight portion so the user only needs to configure the
        # actual departure day — not both sides of midnight.
        wraps_midnight = departure_prep_start > cfg.departure_hour
        if wraps_midnight and hour < cfg.departure_hour:
            # Post-midnight portion: the calendar day has already ticked over, so the
            # relevant departure day is yesterday.
            departure_day_match = (weekday - 1) % 7 in cfg.departure_days
        else:
            departure_day_match = weekday in cfg.departure_days
        departure_prep = departure_day_match and _in_hour_window(
            hour, departure_prep_start, cfg.departure_hour
        )

        # Roadtrip prep: active when now is within the prep window and SoC target not yet met.
        roadtrip_active = False
        if active_roadtrip is not None:
            in_prep_window = (
                active_roadtrip.prep_start
                <= dt_util.as_utc(now)
                < dt_util.as_utc(active_roadtrip.start)
            )
            soc_target_met = False
            if (
                in_prep_window
                and active_roadtrip.soc_target is not None
                and cfg.roadtrip_soc_sensor
            ):
                soc_state = self.hass.states.get(cfg.roadtrip_soc_sensor)
                if soc_state and soc_state.state not in _UNAVAILABLE_STATES:
                    with contextlib.suppress(ValueError):
                        current_soc = float(soc_state.state)
                        soc_target_met = current_soc >= active_roadtrip.soc_target
            roadtrip_active = in_prep_window and not soc_target_met

        # ── Single decision chain ─────────────────────────────────────────
        # Compute the grid-level decision first (ignoring connection) so the
        # LED can always show what WOULD happen if the car were plugged in.
        # Then overlay the connection check for the actual status.
        #
        # Priority: force_off > force_on > carbon > roadtrip > departure > fallback > …
        # Roadtrip sits above departure_prep so a calendar event always wins over the
        # weekly recurring schedule.
        if cfg.charge_mode == CHARGE_MODE_FORCE_OFF:
            status_enum = STATUS_FORCED_OFF
            status_reason = "Paused — forced off"
        elif cfg.charge_mode == CHARGE_MODE_FORCE_ON:
            status_enum = STATUS_OVERRIDE
            status_reason = "Charging — forced on"
        elif carbon_good:
            status_enum = STATUS_LOW_CARBON
            status_reason = f"Charging — grid is clean ({stats.z_score}σ)"
        elif roadtrip_active and active_roadtrip is not None:
            soc_str = f" → {active_roadtrip.soc_target}%" if active_roadtrip.soc_target else ""
            status_enum = STATUS_ROADTRIP_PREP
            status_reason = f'Charging — roadtrip prep for "{active_roadtrip.summary}"{soc_str}'
        elif departure_prep:
            day_name = _DAY_NAMES[weekday]
            status_enum = STATUS_DEPARTURE_PREP
            status_reason = f"Charging — departure prep {day_name} {cfg.departure_hour:02d}:00"
        elif sensors.carbon_data_unavailable and fallback_window:
            status_enum = STATUS_FALLBACK
            status_reason = "Charging — fallback window"
        elif sensors.carbon_data_unavailable and sensors.data_stale:
            status_enum = STATUS_DATA_STALE
            status_reason = "Paused — sensor data is stale"
        elif sensors.carbon_data_unavailable:
            status_enum = STATUS_WAITING_FOR_DATA
            status_reason = "Paused — waiting for data"
        elif sensors.fossil_pct is not None and sensors.fossil_pct >= FOSSIL_HARD_FLOOR:
            status_enum = STATUS_FOSSIL_HIGH
            status_reason = f"Paused — fossil fuel too high ({round(sensors.fossil_pct)}%)"
        else:
            status_enum = STATUS_GRID_DIRTY
            status_reason = f"Paused — grid too dirty ({stats.z_score}σ)"

        # LED colour: roadtrip uses cyan, everything else follows predicted_state
        if status_enum == STATUS_ROADTRIP_PREP:
            led_state = "roadtrip"
        else:
            led_state, _ = STATUS_MAP[status_enum]

        # Override for connection status
        if not sensors.is_connected:
            status_enum = STATUS_NOT_CONNECTED
            status_reason = "Not connected"

        # Derive everything else from the authoritative status
        predicted_state, chargeable = STATUS_MAP[status_enum]
        should_charge = chargeable and sensors.is_connected

        return _ChargingDecision(
            predicted_state=predicted_state,
            should_charge=should_charge,
            carbon_good=carbon_good,
            status_enum=status_enum,
            status_reason=status_reason,
            led_state=led_state,
            active_roadtrip=active_roadtrip if roadtrip_active else None,
        )

    async def _control_devices(
        self,
        cfg: _ResolvedConfig,
        sensors: _SensorReadings,
        decision: _ChargingDecision,
        stats: _Statistics,
    ) -> None:
        """Control charger switch, LED indicator, and send notifications."""
        try:
            await self._async_control_devices_inner(cfg, sensors, decision, stats)
        except ServiceNotFound as exc:
            # On first boot, platforms like switch/light/select may not be
            # loaded yet.  Log and let the next poll retry.
            _LOGGER.debug("[EV] Service not yet available, will retry: %s", exc)

        # Track connection state for reconnect detection (always, even in dry-run)
        self._was_connected = sensors.is_connected

    async def _async_control_devices_inner(
        self,
        cfg: _ResolvedConfig,
        sensors: _SensorReadings,
        decision: _ChargingDecision,
        stats: _Statistics,
    ) -> None:
        """Inner device-control logic (may raise ServiceNotFound on boot)."""
        want_on = decision.should_charge
        is_on = sensors.charger_is_on

        # ── Charger switch ────────────────────────────────────────────────
        if not cfg.dry_run:
            if want_on and not is_on:
                # Cooldown: prevent turn-on shortly after turn-off
                just_reconnected = sensors.is_connected and not self._was_connected
                cooldown_met = True
                if (
                    sensors.charger_state is not None
                    and cfg.charge_mode != CHARGE_MODE_FORCE_ON
                    and not just_reconnected
                ):
                    off_elapsed_min = (
                        dt_util.utcnow() - sensors.charger_state.last_changed
                    ).total_seconds() / 60
                    cooldown_met = off_elapsed_min >= MIN_COOLDOWN_MINUTES
                    if not cooldown_met:
                        _LOGGER.debug(
                            "[EV] Cooldown active: charger off for %.1f min, need %d min",
                            off_elapsed_min,
                            MIN_COOLDOWN_MINUTES,
                        )

                if cooldown_met:
                    # If this is a roadtrip prep turn-on, set the charge limit first.
                    if (
                        decision.active_roadtrip is not None
                        and decision.active_roadtrip.soc_target is not None
                        and cfg.roadtrip_charge_limit_entity
                    ):
                        with contextlib.suppress(Exception):
                            await self._async_set_charge_limit(
                                cfg.roadtrip_charge_limit_entity,
                                decision.active_roadtrip.soc_target,
                            )
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": cfg.charger_entity},
                        blocking=False,
                    )
                    if cfg.notify_service:
                        try:
                            z = stats.z_score
                            fossil = round(sensors.fossil_pct or 0)
                            await self._async_notify(
                                cfg.notify_service,
                                "🌿 EV Low-Carbon Charging Started",
                                f"{decision.predicted_state.title()} mode"
                                f" — Z-score {z}σ, {fossil}% fossil",
                            )
                        except Exception:
                            _LOGGER.exception("[EV] Failed to send charge-start notification")
            elif not want_on and is_on:
                # Dwell: prevent turn-off within MIN_DWELL_MINUTES of turn-on
                min_dwell_met = True
                if sensors.charger_state is not None:
                    elapsed_min = (
                        dt_util.utcnow() - sensors.charger_state.last_changed
                    ).total_seconds() / 60
                    min_dwell_met = elapsed_min >= MIN_DWELL_MINUTES or not sensors.is_connected

                if min_dwell_met:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": cfg.charger_entity},
                        blocking=False,
                    )
                    if cfg.notify_service and sensors.is_connected:
                        try:
                            await self._async_notify(
                                cfg.notify_service,
                                "⏸ EV Charging Paused",
                                decision.status_reason,
                            )
                        except Exception:
                            _LOGGER.exception("[EV] Failed to send charge-paused notification")

        # ── LED indicator (idempotent — only write on state change) ───────
        # Colour reflects what WOULD happen if the car were plugged in;
        # effect distinguishes connected (flowing) vs disconnected (flashing).
        led_key = (decision.led_state, sensors.is_connected)
        if led_key != self._last_led_state:
            if cfg.led_light:
                hs_colour = LED_COLOUR.get(decision.led_state, [0, 100])
                await self.hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": cfg.led_light, "brightness": 128, "hs_color": hs_colour},
                    blocking=False,
                )
            if cfg.led_effect_select:
                effect = "Middle Rising" if sensors.is_connected else "Slow Blink"
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": cfg.led_effect_select, "option": effect},
                    blocking=False,
                )
            self._last_led_state = led_key

    # ── Helpers ───────────────────────────────────────────────────────────────

    # ── Roadtrip Prep ─────────────────────────────────────────────────────────

    @staticmethod
    def parse_roadtrip_title(
        title: str,
        prefix: str,
        default_lead_hours: int,
    ) -> tuple[int | None, int] | None:
        """Parse a roadtrip event title.

        Returns ``(soc_target, lead_hours)`` when the title matches the
        configured prefix, or ``None`` if it does not match.

        Title format: ``[PREFIX optional_soc% optional_lead_h]``
        Examples:
          ``[IONIQ 90% 4h]`` → (90, 4)
          ``[IONIQ 80%]``    → (80, default_lead_hours)
          ``[IONIQ 6h]``     → (None, 6)
          ``[IONIQ]``        → (None, default_lead_hours)
        """
        if not prefix:
            return None
        match = _ROADTRIP_TITLE_RE.search(title)
        if match is None:
            return None
        matched_prefix = match.group("prefix").strip()
        if matched_prefix.lower() != prefix.lower():
            return None
        soc_raw = match.group("soc")
        lead_raw = match.group("lead")
        soc_target = int(soc_raw) if soc_raw is not None else None
        lead_hours = int(lead_raw) if lead_raw is not None else default_lead_hours
        return soc_target, lead_hours

    async def _async_find_active_roadtrip(self, cfg: _ResolvedConfig) -> RoadtripEvent | None:
        """Query configured calendars and return the driving roadtrip event.

        Strategy: collect all matching events that start within the next
        ROADTRIP_LOOKAHEAD_HOURS, then pick the one with the **earliest**
        start time that also has the **highest** SoC target — i.e. charge
        early enough for the soonest event, to the level required by the
        highest-demand event across all overlapping events.
        """
        if not cfg.roadtrip_calendars or not cfg.roadtrip_prefix:
            return None

        now = dt_util.utcnow()
        end = now + timedelta(hours=ROADTRIP_LOOKAHEAD_HOURS)

        # HA calendar.get_events service returns a dict keyed by entity_id.
        # service_response is available from HA 2023.7+.
        try:
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": cfg.roadtrip_calendars,
                    "start_date_time": now.isoformat(),
                    "end_date_time": end.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
        except ServiceNotFound:
            # calendar integration not loaded or entity not yet set up — skip silently.
            return None
        except Exception:
            _LOGGER.debug(
                "[EV] calendar.get_events failed — skipping roadtrip check", exc_info=True
            )
            return None

        if not isinstance(response, dict):
            return None

        matching: list[RoadtripEvent] = []
        for _cal_id, cal_data in response.items():
            events = cal_data.get("events", []) if isinstance(cal_data, dict) else []
            for event in events:
                summary = event.get("summary", "")
                parsed = self.parse_roadtrip_title(
                    summary, cfg.roadtrip_prefix, cfg.roadtrip_default_lead_hours
                )
                if parsed is None:
                    continue
                soc_target, lead_hours = parsed
                start_raw = event.get("start")
                if not start_raw:
                    continue
                try:
                    # HA returns ISO8601 strings; handle both date and datetime forms.
                    start_dt = dt_util.parse_datetime(start_raw)
                    if start_dt is None:
                        # All-day event: "YYYY-MM-DD"
                        start_dt = dt_util.as_utc(
                            datetime.fromisoformat(start_raw).replace(
                                tzinfo=dt_util.DEFAULT_TIME_ZONE
                            )
                        )
                    else:
                        start_dt = dt_util.as_utc(start_dt)
                except (ValueError, TypeError):
                    _LOGGER.debug("[EV] Could not parse event start %r — skipping", start_raw)
                    continue

                matching.append(
                    RoadtripEvent(
                        summary=summary,
                        start=start_dt,
                        soc_target=soc_target,
                        lead_hours=lead_hours,
                    )
                )

        if not matching:
            return None

        # Pick earliest start; break ties by highest SoC target.
        earliest_start = min(e.start for e in matching)
        # Among events in the active prep window of the soonest event, find
        # the highest SoC demand so we charge to the right level.
        highest_soc: int | None = None
        driving_event: RoadtripEvent | None = None
        for evt in matching:
            if evt.soc_target is not None and (highest_soc is None or evt.soc_target > highest_soc):
                highest_soc = evt.soc_target
        # Build a synthetic "merged" event: earliest start + max SoC target.
        # The lead_hours from the earliest event drives when prep begins.
        anchor = min(matching, key=lambda e: e.start)
        driving_event = RoadtripEvent(
            summary=anchor.summary,
            start=earliest_start,
            soc_target=highest_soc,
            lead_hours=anchor.lead_hours,
        )

        now_utc = dt_util.utcnow()
        in_window = driving_event.prep_start <= now_utc < driving_event.start
        _LOGGER.debug(
            "[EV] Roadtrip event detected: summary=%r start=%s soc=%s lead=%dh prep_start=%s (%s)",
            driving_event.summary,
            driving_event.start.isoformat(),
            driving_event.soc_target,
            driving_event.lead_hours,
            driving_event.prep_start.isoformat(),
            "IN PREP WINDOW"
            if in_window
            else "waiting — prep starts at " + driving_event.prep_start.isoformat(),
        )
        return driving_event

    async def _async_set_charge_limit(
        self,
        entity_id: str,
        soc_target: int,
    ) -> None:
        """Set the charge limit on the car entity (number or select)."""
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("[EV] Charge-limit entity %s not found", entity_id)
            return
        domain = entity_id.split(".")[0]
        if domain == "number":
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": entity_id, "value": soc_target},
                blocking=False,
            )
        elif domain == "select":
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": str(soc_target)},
                blocking=False,
            )
        else:
            _LOGGER.warning(
                "[EV] Charge-limit entity %s has unsupported domain %r (expected number or select)",
                entity_id,
                domain,
            )

    async def _async_save_history(self) -> None:
        """Persist rolling deques so warmup survives HA restarts."""
        await self._store.async_save(
            {
                "deque_7d": list(self._deque_7d),
                "deque_30d": list(self._deque_30d),
                "last_z_score": self._last_z_score,
            }
        )

    async def _async_notify(self, service: str, title: str, message: str) -> None:
        """Fire a push notification via a configured notify service."""
        parts = service.split(".", 1)
        if len(parts) != 2:
            _LOGGER.warning("[EV] Invalid notify_service value: %r", service)
            return
        domain, svc = parts
        await self.hass.services.async_call(
            domain,
            svc,
            {"title": title, "message": message},
            blocking=False,
        )
