# EV Carbon-Optimized Charging — Project Overview

## What This Is

A Home Assistant automation suite that charges an electric vehicle preferentially
during low-carbon grid periods, using a Z-score statistical signal derived from
7-day rolling mean/stdev of real-time CO2 intensity data. Includes dashboards,
configurable sensitivity modes, dry-run testing, and LED status feedback.

---

## File Structure

```
ev.yaml                   Main charging automation
ev_helpers.yaml           HA helper entity definitions (package)
ev_sensors.yaml           Statistics platform sensors (package)
templates.yaml            All template sensors — single file to avoid merge conflicts
ev_dashboard.yaml         Operational monitoring dashboard
ev_analysis_dashboard.yaml  Statistical analysis dashboard (requires custom:plotly-graph)
configuration.yaml        HA main config — integrates packages and dashboard references
```

---

## Key Entity IDs

| Entity | Role |
|---|---|
| `sensor.32_79_96_48_co2_intensity` | Real-time CO2 intensity (Electricity Maps integration) |
| `sensor.32_79_96_48_grid_fossil_fuel_percentage` | Real-time fossil fuel % (Electricity Maps) |
| `switch.car_charger` | Physical charger switch |
| `binary_sensor.ev_connected` | Derived from charger icon attribute; `on` when car is plugged in |
| `sensor.co2_intensity_7d_mean` | 7-day rolling mean (statistics platform) |
| `sensor.co2_intensity_7d_stdev` | 7-day rolling stdev (statistics platform) |
| `sensor.co2_intensity_30d_mean` | 30-day rolling mean (statistics platform) |
| `sensor.co2_intensity_30d_stdev` | 30-day rolling stdev (statistics platform) |
| `sensor.co2_intensity_z_score` | Z-score: `(co2 - 7d_mean) / 7d_stdev` |
| `sensor.ev_low_carbon_now` | Boolean gate: z_score < mode_threshold AND fossil% < 75 |
| `input_select.ev_charge_mode` | `auto` or `force_on` |
| `input_select.ev_carbon_mode` | `Lenient`, `Moderate`, or `Strict` |
| `input_number.ev_departure_hour` | Hour of day to force-charge before departure |
| `input_boolean.ev_charger_dry_run` | When on, automation logs but does not switch charger |
| `light.garage_lights_rgb_indicator` | LED indicator light |
| `select.garage_lights_led_effect` | LED animation effect selector |

---

## Automation Logic (`ev.yaml`)

Runs every 5 minutes and on state changes to mode, connection, or carbon signal.

### Decision Variables (computed in a single `variables:` block)
- `charge_mode` — current value of `input_select.ev_charge_mode`
- `car_connected` — is a car plugged in
- `carbon_good` — is the carbon gate open (`ev_low_carbon_now == True`)
- `fallback_window` — true between 22:00–06:00 or 11:00–15:00 (ensures charging on bad-grid days)
- `departure_prep` — true on Wed/Thu after departure hour (configurable)
- `predicted_state` — one of `carbon`, `scheduled`, `override`, `paused` (see below)
- `should_charge` — `predicted_state` is chargeable AND `car_connected`

### `predicted_state` Priority
```
force_off  → paused
force_on   → override
carbon_good → carbon
fallback/departure → scheduled
else        → paused
```

### LED Colours
| State | Colour | Effect |
|---|---|---|
| `carbon` | Green `[120, 80]` | Middle Rising (while charging) |
| `override` | Amber `[35, 100]` | Middle Rising |
| `scheduled` | Red `[0, 100]` | Middle Rising |
| `paused` | Red `[0, 100]` | Slow Blink |

---

## Carbon Sensitivity Modes (`input_select.ev_carbon_mode`)

| Mode | Z-score threshold | Approx. charge frequency |
|---|---|---|
| `Lenient` | < 0.92 | ~82% of hours — skips only high-carbon spikes |
| `Moderate` | < 0.47 | ~68% of hours — default, avoids dirtier periods |
| `Strict` | < -0.18 | ~43% of hours — only genuinely clean grid |

Additionally, `sensor.ev_low_carbon_now` applies a hard floor: fossil fuel % must
be below 75% regardless of Z-score.

---

## Z-Score Sensor Design Notes

`sensor.co2_intensity_z_score` has two layers of protection against reload spikes:

1. **Availability guard** — requires `stdev > 5` AND `mean > 50`. During HA YAML
   reloads, statistics sensors briefly return `0` before loading history from the DB.
   `mean > 50` catches `float("0")` passing the old `>= 0` guard; `stdev > 5`
   catches near-zero transient values that would produce extreme Z-scores.

2. **State guard** — the state template repeats the same check and falls back
   to `this.state` (holds last good value) if inputs are implausible, because
   availability and state templates are not evaluated atomically in HA.

---

## Dashboard Summary

### `ev_dashboard.yaml`
Operational view with:
- Glance card (mode, connection, charger, carbon gate, dry-run)
- Three gauges: Z-score (-3 to +3 with mode threshold severity), CO2 intensity, fossil %
- CO2 intensity vs 7d mean history graph (48h)
- Z-score history chart (plotly, y-clamped ±3, threshold reference lines)
- Charge windows chart (plotly, Z-score + carbon gate fill + charger state overlay)
- 30-day statistics-graph trends for CO2 and Z-score
- Controls card (mode select, sensitivity, departure hour, dry-run toggle)

### `ev_analysis_dashboard.yaml`
Statistical analysis view (requires `custom:plotly-graph` from HACS):
- **Distributions tab**: CO2 histogram (30d), Z-score histogram (30d) with threshold lines,
  CO2 bucketed counts (30d), CO2 vs fossil % dual-axis overlay; stat boxes showing 30d mean/stdev
- **Patterns tab**: CO2 by hour box plot (7d), Z-score by hour box plot (7d) with threshold lines,
  30-day statistics-graph reference

---

## Known Issues / Quirks

- `custom:plotly-graph` histograms require `type: linear` on the x-axis to prevent
  numeric values being interpreted as Unix timestamps.
- `template:` is a top-level singleton in HA — using it inside packages causes
  silent merge conflicts (last one wins). All templates live in `templates.yaml`
  and are included via a single top-level `template: !include templates.yaml`.
- The statistics platform sensors (`7d_mean`, `7d_stdev`) need ~7 days of data
  before the Z-score becomes meaningful. During warmup, `ev_low_carbon_now`
  defaults to `False` (safe fallback to schedule logic).
