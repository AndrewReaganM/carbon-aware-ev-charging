"""DataUpdateCoordinator for Carbon-Aware EV Charging."""
from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CARBON_MODE_MODERATE,
    CHARGE_MODE_AUTO,
    CHARGE_MODE_FORCE_OFF,
    CHARGE_MODE_FORCE_ON,
    CHARGEABLE_STATES,
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
    CONF_FOSSIL_SENSOR,
    CONF_LED_EFFECT_SELECT,
    CONF_LED_LIGHT,
    CONF_NOTIFY_SERVICE,
    DEQUE_30D,
    DEQUE_7D,
    DOMAIN,
    FOSSIL_HARD_FLOOR,
    HYSTERESIS_SIGMA,
    LED_COLOUR,
    MIN_DWELL_MINUTES,
    STATE_CARBON,
    STATE_OVERRIDE,
    STATE_PAUSED,
    STATE_SCHEDULED,
    STORAGE_KEY,
    STORAGE_VERSION,
    THRESHOLDS,
)

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)

_UNAVAILABLE_STATES = {"unavailable", "unknown", "none", ""}

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=5)


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
    predicted_state: str = STATE_PAUSED
    should_charge: bool = False
    status_reason: str = "Unknown"
    charge_rate_kw: float | None = None
    charge_current_a: int | None = None


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
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.history import (  # noqa: PLC0415
                state_changes_during_period,
            )
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
        except Exception:
            _LOGGER.debug(
                "[EV] Recorder query failed — skipping history backfill",
                exc_info=True,
            )
            return

        self._load_recorder_states(history.get(co2_entity, []), start_7d)

    def _load_recorder_states(
        self, states: list, start_7d: datetime,
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
            self.hass.async_create_task(self._async_save_history())

    # ── Main update ───────────────────────────────────────────────────────────

    async def _async_update_data(self) -> EVCarbonData:
        """Fetch state, update stats, compute derived values, control devices."""
        cfg = self.entry.data
        opts = self.entry.options

        # Config — entity IDs come from data; preferences prefer options over data.
        co2_entity: str = cfg[CONF_CO2_SENSOR]
        fossil_entity: str = cfg[CONF_FOSSIL_SENSOR]
        charger_entity: str = cfg[CONF_CHARGER_SWITCH]
        connected_attr: str = cfg.get(CONF_CHARGER_CONNECTED_ATTR, "icon_name")
        not_connected_val: str = cfg.get(
            CONF_CHARGER_NOT_CONNECTED_VALUE, "CarNotConnected"
        )
        power_entity: str | None = cfg.get(CONF_CHARGER_POWER_SENSOR)
        led_light: str | None = cfg.get(CONF_LED_LIGHT)
        led_effect_select: str | None = cfg.get(CONF_LED_EFFECT_SELECT)

        carbon_mode: str = opts.get(
            CONF_CARBON_MODE, cfg.get(CONF_CARBON_MODE, CARBON_MODE_MODERATE)
        )
        charge_mode: str = opts.get(CONF_CHARGE_MODE, CHARGE_MODE_AUTO)
        departure_hour: int = int(
            opts.get(CONF_DEPARTURE_HOUR, cfg.get(CONF_DEPARTURE_HOUR, 5))
        )
        departure_days_raw = opts.get(
            CONF_DEPARTURE_DAYS, cfg.get(CONF_DEPARTURE_DAYS, ["2", "3"])
        )
        departure_days: list[int] = [int(d) for d in departure_days_raw]
        dry_run: bool = bool(opts.get(CONF_DRY_RUN, cfg.get(CONF_DRY_RUN, False)))
        notify_service: str = opts.get(
            CONF_NOTIFY_SERVICE, cfg.get(CONF_NOTIFY_SERVICE, "")
        )

        # ── Read sensor states ────────────────────────────────────────────────
        co2_state = self.hass.states.get(co2_entity)
        fossil_state = self.hass.states.get(fossil_entity)
        charger_state = self.hass.states.get(charger_entity)

        co2: float | None = None
        fossil_pct: float | None = None
        carbon_data_unavailable = True

        _LOGGER.debug(
            "[EV] raw sensor states: co2_entity=%s state=%r  "
            "fossil_entity=%s state=%r  charger_entity=%s state=%r",
            co2_entity, co2_state.state if co2_state else "MISSING",
            fossil_entity, fossil_state.state if fossil_state else "MISSING",
            charger_entity, charger_state.state if charger_state else "MISSING",
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

        is_connected = False
        if charger_state:
            is_connected = (
                charger_state.attributes.get(connected_attr) != not_connected_val
            )

        # ── Charger aux sensors ───────────────────────────────────────────────
        charge_rate_kw: float | None = None
        if power_entity:
            ps = self.hass.states.get(power_entity)
            if ps and ps.state not in _UNAVAILABLE_STATES:
                try:
                    charge_rate_kw = round(float(ps.state) / 1000, 2)
                except ValueError:
                    pass

        charge_current_a: int | None = None
        if charger_state:
            raw = charger_state.attributes.get("charging_rate")
            if raw is not None:
                try:
                    charge_current_a = int(raw)
                except (TypeError, ValueError):
                    pass

        # ── Update rolling deques and persist ─────────────────────────────────
        if co2 is not None:
            ts = dt_util.utcnow().timestamp()
            self._deque_7d.append((ts, co2))
            self._deque_30d.append((ts, co2))
            self.hass.async_create_task(self._async_save_history())

        # ── Rolling statistics ─────────────────────────────────────────────────
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

        # ── Z-score with reload-spike guard ───────────────────────────────────
        _LOGGER.debug(
            "[EV] z_score inputs: co2=%s mean_7d=%s stdev_7d=%s "
            "mean_30d=%s stdev_30d=%s deque_7d_len=%d deque_30d_len=%d",
            co2, mean_7d, stdev_7d, mean_30d, stdev_30d,
            len(self._deque_7d), len(self._deque_30d),
        )
        z_score: float | None = None
        if co2 is not None and mean_7d is not None:
            if stdev_7d:
                z_score = round((co2 - mean_7d) / stdev_7d, 2)
            else:
                # stdev=0 means all readings are identical — current value is
                # exactly at the mean, so z_score is 0 by definition.
                z_score = 0.0
            self._last_z_score = z_score
        else:
            _LOGGER.debug(
                "[EV] z_score blocked: co2_none=%s mean_none=%s — holding last=%s",
                co2 is None, mean_7d is None, self._last_z_score,
            )
            z_score = self._last_z_score  # hold last good value

        # ── Carbon gate (with hysteresis) ─────────────────────────────────────
        threshold = THRESHOLDS[carbon_mode]
        charger_is_on = charger_state is not None and charger_state.state == "on"
        effective_threshold = (
            threshold + HYSTERESIS_SIGMA if charger_is_on else threshold
        )
        carbon_good = (
            z_score is not None
            and fossil_pct is not None
            and z_score < effective_threshold
            and fossil_pct < FOSSIL_HARD_FLOOR
        )

        # ── Fallback / departure windows ──────────────────────────────────────
        now = dt_util.now()
        hour = now.hour
        weekday = now.weekday()
        fallback_window = (hour >= 22) or (hour < 6) or (11 <= hour < 15)
        departure_prep = weekday in departure_days and hour >= departure_hour

        # ── Predicted state ───────────────────────────────────────────────────
        if charge_mode == CHARGE_MODE_FORCE_OFF:
            predicted_state = STATE_PAUSED
        elif charge_mode == CHARGE_MODE_FORCE_ON:
            predicted_state = STATE_OVERRIDE
        elif carbon_good:
            predicted_state = STATE_CARBON
        elif carbon_data_unavailable and (fallback_window or departure_prep):
            predicted_state = STATE_SCHEDULED
        else:
            predicted_state = STATE_PAUSED

        should_charge = predicted_state in CHARGEABLE_STATES and is_connected

        # ── Human-readable status reason ─────────────────────────────────────
        _DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if not is_connected:
            status_reason = "Not connected"
        elif charge_mode == CHARGE_MODE_FORCE_OFF:
            status_reason = "Paused — forced off"
        elif charge_mode == CHARGE_MODE_FORCE_ON:
            status_reason = "Charging — forced on"
        elif carbon_good:
            status_reason = f"Charging — grid is clean ({z_score}σ)"
        elif carbon_data_unavailable and departure_prep:
            day_name = _DAY_NAMES[weekday]
            status_reason = (
                f"Charging — departure prep {day_name} {departure_hour:02d}:00"
            )
        elif carbon_data_unavailable and fallback_window:
            status_reason = "Charging — fallback window"
        elif carbon_data_unavailable:
            status_reason = "Paused — waiting for data"
        elif fossil_pct is not None and fossil_pct >= FOSSIL_HARD_FLOOR:
            status_reason = f"Paused — fossil fuel too high ({round(fossil_pct)}%)"
        else:
            status_reason = f"Paused — grid too dirty ({z_score}σ)"

        # ── Min dwell (prevents turn-off within 15 min of turn-on) ───────────
        min_dwell_met = True
        if charger_is_on and not should_charge and charger_state is not None:
            elapsed_min = (
                dt_util.utcnow() - charger_state.last_changed
            ).total_seconds() / 60
            min_dwell_met = elapsed_min >= MIN_DWELL_MINUTES or not is_connected

        # ── Charger control ───────────────────────────────────────────────────
        if not dry_run:
            if should_charge and not charger_is_on:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": charger_entity},
                    blocking=False,
                )
                if notify_service:
                    await self._async_notify(
                        notify_service,
                        "🌿 EV Low-Carbon Charging Started",
                        f"{predicted_state.title()} mode — Z-score {z_score}σ, "
                        f"{round(fossil_pct or 0)}% fossil",
                    )
            elif not should_charge and charger_is_on and min_dwell_met:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": charger_entity},
                    blocking=False,
                )
                if notify_service:
                    await self._async_notify(
                        notify_service,
                        "⏸ EV Charging Paused",
                        f"Grid too dirty for {carbon_mode} mode. "
                        f"Z-score {z_score}σ, {round(fossil_pct or 0)}% fossil.",
                    )

        # ── LED indicator ─────────────────────────────────────────────────────
        if led_light:
            hs_colour = LED_COLOUR.get(predicted_state, [0, 100])
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": led_light, "brightness": 128, "hs_color": hs_colour},
                blocking=False,
            )
        if led_effect_select:
            effect = "Middle Rising" if should_charge else "Slow Blink"
            await self.hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": led_effect_select, "option": effect},
                blocking=False,
            )

        # ── Debug log ─────────────────────────────────────────────────────────
        _LOGGER.info(
            "[EV] predicted=%s should_charge=%s mode=%s car=%s carbon=%s "
            "z_score=%s fallback=%s departure=%s unavailable=%s dry_run=%s",
            predicted_state,
            should_charge,
            charge_mode,
            is_connected,
            carbon_good,
            z_score,
            fallback_window,
            departure_prep,
            carbon_data_unavailable,
            dry_run,
        )

        return EVCarbonData(
            co2=co2,
            fossil_pct=fossil_pct,
            z_score=z_score,
            mean_7d=mean_7d,
            stdev_7d=stdev_7d,
            mean_30d=mean_30d,
            stdev_30d=stdev_30d,
            is_connected=is_connected,
            carbon_good=carbon_good,
            carbon_data_unavailable=carbon_data_unavailable,
            predicted_state=predicted_state,
            should_charge=should_charge,
            status_reason=status_reason,
            charge_rate_kw=charge_rate_kw,
            charge_current_a=charge_current_a,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _async_save_history(self) -> None:
        """Persist rolling deques so warmup survives HA restarts."""
        await self._store.async_save(
            {
                "deque_7d": list(self._deque_7d),
                "deque_30d": list(self._deque_30d),
                "last_z_score": self._last_z_score,
            }
        )

    async def _async_notify(
        self, service: str, title: str, message: str
    ) -> None:
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
