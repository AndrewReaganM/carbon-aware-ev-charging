# Carbon-Aware EV Charging

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)

Automatically charge your EV when the electrical grid is cleanest. This Home Assistant integration monitors real-time CO₂ intensity, computes a rolling statistical signal, and controls your charger — no YAML automation required.

---

## How It Works

Most carbon-aware charging tools use static thresholds (e.g., "charge when CO₂ < 200 g/kWh"). This integration uses a **Z-score** — a measure of how *unusual* the current CO₂ level is relative to the past 7 days:

```
Z-score = (current CO₂ − 7-day mean) / 7-day standard deviation
```

A **negative Z-score** means the grid is cleaner than usual. A **positive Z-score** means dirtier than usual. This approach adapts automatically to your local grid's baseline — it works equally well in a region averaging 50 g/kWh or 500 g/kWh.

The charger turns on when:
- The Z-score is below your chosen sensitivity threshold, **and**
- Fossil fuel generation is below 75% (a hard safety floor)

If carbon data is unavailable or the grid is consistently dirty, built-in **fallback windows** (overnight and midday) ensure your car is still charged.

---

## Features

- **Fully UI-configured** — no YAML editing needed after installation
- **Statistical carbon signal** — Z-score adapts to your grid's typical range
- **Three sensitivity modes** — from aggressive carbon avoidance to charging most of the time
- **Fallback windows** — guaranteed charging overnight (22:00–06:00) and midday (11:00–15:00) even on dirty-grid days
- **Departure-day prep charging** — configurable days and hour so your car is always ready
- **Hysteresis** — prevents rapid on/off switching when hovering near the threshold
- **Dry-run mode** — log decisions without touching the charger, for safe testing
- **Optional LED indicator** — RGB light shows charging state at a glance
- **Optional push notifications** — get notified when charging starts or stops
- **Persisted history** — rolling statistics survive Home Assistant restarts

---

## Prerequisites

Before installing, you need:

1. **A CO₂ intensity sensor** — the [Electricity Maps](https://www.home-assistant.io/integrations/electricity_maps/) integration provides both a CO₂ intensity sensor and a fossil fuel percentage sensor out of the box. Any sensor exposing a numeric CO₂ value (g/kWh) and a fossil fuel percentage will work.

2. **A controllable charger switch** — your charger must be exposed as a `switch` entity in Home Assistant. This integration has been tested with **Emporia** chargers. Other chargers should work as long as they present a switch entity and expose a connection attribute (or an equivalent attribute) indicating whether a car is plugged in.

3. **Home Assistant 2024.1 or later**

---

## Installation

### Via HACS (Recommended)

1. Open HACS in your Home Assistant sidebar.
2. Click the three-dot menu (⋮) in the top right and choose **Custom repositories**.
3. Enter the repository URL:
   ```
   https://github.com/andrewreaganm/carbon-aware-ev-charging
   ```
   Set the category to **Integration**, then click **Add**.
4. Search for **Carbon-Aware EV Charging** in HACS and click **Download**.
5. Restart Home Assistant.

### Manual Installation

1. Download or clone this repository.
2. Copy the `custom_components/carbon_aware_ev_charging` folder into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

---

## Configuration

After installation, go to **Settings → Devices & Services → Add Integration** and search for **Carbon-Aware EV Charging**. The setup wizard walks you through three steps.

### Step 1 — Sensors & Charger

| Field | Description |
|---|---|
| **CO₂ Intensity Sensor** | Entity providing real-time CO₂ intensity in g/kWh (e.g., `sensor.my_region_co2_intensity` from Electricity Maps) |
| **Fossil Fuel % Sensor** | Entity providing the current fossil fuel generation percentage (e.g., `sensor.my_region_grid_fossil_fuel_percentage`) |
| **Charger Switch** | The `switch` entity that controls your EV charger |
| **Connection Attribute** | The charger switch attribute used to detect if a car is plugged in (default: `icon_name`) |
| **Not-Connected Value** | The attribute value when no car is connected (default: `CarNotConnected`) |
| **Charger Power Sensor** *(optional)* | Sensor reporting current charge power in kW; used to populate the charge rate entity |

> **Emporia users:** The connection attribute `icon_name` with value `CarNotConnected` are the correct defaults for Emporia chargers. No changes needed.

### Step 2 — LED Indicator *(optional)*

If you have an RGB light (e.g., a smart bulb or LED strip in the garage), you can configure it here for visual status feedback.

| Field | Description |
|---|---|
| **RGB Indicator Light** | A `light` entity supporting HS colour |
| **LED Effect Selector** | A `select` entity for choosing the light's animation effect |

See [LED Indicator](#led-indicator) for colour meanings.

### Step 3 — Preferences

| Field | Description | Default |
|---|---|---|
| **Carbon Sensitivity** | How strictly to follow carbon signals (`Lenient`, `Moderate`, `Strict`) | `Moderate` |
| **Departure Hour** | Hour of day (0–23) to trigger departure-prep charging | `7` |
| **Departure Days** | Days of the week to activate departure prep | Wednesday, Thursday |
| **Dry Run** | Log decisions only; do not control the charger | Off |
| **Notification Service** *(optional)* | HA notify service (e.g., `notify.mobile_app_my_phone`) | — |

You can change any of these later via **Settings → Devices & Services → Carbon-Aware EV Charging → Configure**, without re-running the full wizard.

---

## Carbon Sensitivity Modes

| Mode | Z-score Threshold | Approximate Charge Frequency |
|---|---|---|
| **Lenient** | < 0.92 | ~82% of hours — skips only the dirtiest peaks |
| **Moderate** | < 0.47 | ~68% of hours — avoids above-average carbon periods |
| **Strict** | < −0.18 | ~43% of hours — only charges during genuinely clean windows |

All modes also enforce a **75% fossil fuel hard floor** — if more than three-quarters of grid generation is from fossil fuels, charging will not start regardless of the Z-score.

**Which mode should I use?**
- Start with **Moderate**. If your car is frequently not charged enough, switch to **Lenient**.
- Use **Strict** if you have generous charging time available and want maximum carbon reduction.

---

## Charging Decision Logic

Every 5 minutes (and on relevant state changes) the integration evaluates:

```
force_off mode?     → Paused      (charger off, LED slow-blinks red)
force_on mode?      → Override    (charger on regardless of carbon, LED amber)
carbon gate open?   → Carbon      (charger on, LED green)
fallback window?    → Scheduled   (charger on, LED red rising)
otherwise           → Paused      (charger off, LED slow-blinks red)
```

**Fallback windows** (when Scheduled charging activates even on dirty-grid days):
- **Overnight:** 22:00–06:00
- **Midday:** 11:00–15:00
- **Departure days:** after your configured departure hour, on your configured departure days

**Hysteresis:** When the charger is already on and the Z-score rises just above the threshold, the integration adds 0.4σ of tolerance before turning off. This prevents the charger rapidly cycling if the signal is hovering near the boundary.

---

## Entities Created

The integration creates a single device with the following entities:

| Entity | Type | Description |
|---|---|---|
| `co2_z_score` | Sensor | Current Z-score (negative = cleaner than usual) |
| `ev_low_carbon_now` | Sensor | `True` when the carbon gate is open |
| `ev_charge_rate_kw` | Sensor | Current charge power in kW |
| `ev_charge_current` | Sensor | Current charge current in amps |
| `ev_connected` | Binary Sensor | `On` when a car is plugged in |
| `ev_charge_mode` | Select | `auto` / `force_on` / `force_off` |
| `ev_carbon_mode` | Select | `Lenient` / `Moderate` / `Strict` |
| `ev_departure_hour` | Number | Hour of day (0–23) for departure prep |

---

## LED Indicator

If configured, the RGB light reflects the current charging state:

| State | Colour | Effect | Meaning |
|---|---|---|---|
| Carbon | Green | Rising | Charging on clean grid |
| Override | Amber | Rising | Charging forced on |
| Scheduled | Red | Rising | Charging in fallback window |
| Paused | Red | Slow blink | Not charging |

---

## Dry-Run Mode

Enable **Dry Run** in the integration options to have the integration evaluate all logic and log its decisions without actually switching the charger or changing the LED. This is useful for validating configuration before going live. Check the Home Assistant logs (filter by `carbon_aware_ev_charging`) to see the decision on each cycle.

---

## Example Dashboard

A ready-made dashboard is included in [`ev_dashboard.yaml`](ev_dashboard.yaml). It provides status glances, Z-score and CO₂ gauges, 48-hour history graphs, an optional Plotly charge-window overlay, and 30-day trend cards.

**To install:** Go to **Settings → Dashboards → Add Dashboard**, choose "From YAML", and paste the contents of the file. Replace the three placeholder entity IDs (`YOUR_CO2_SENSOR`, `YOUR_FOSSIL_SENSOR`, `YOUR_CHARGER_SWITCH`) with your own entities.

<details>
<summary>Dashboard YAML</summary>

```yaml
# EV Charging Dashboard — Carbon-Aware EV Charging Integration
# ─────────────────────────────────────────────────────────────────────────────
# BEFORE USING: Replace the three placeholder entity IDs below with your own.
#   1. YOUR_CO2_SENSOR        → your CO₂ intensity sensor
#   2. YOUR_FOSSIL_SENSOR     → your fossil fuel % sensor
#   3. YOUR_CHARGER_SWITCH    → your charger switch entity
#
# To install: Settings → Dashboards → Add Dashboard → choose "From YAML",
# then paste this file's contents.
# ─────────────────────────────────────────────────────────────────────────────

title: EV Charging
views:
  - title: Overview
    path: ev
    icon: mdi:ev-station
    cards:

      # ── Current Status ────────────────────────────────────────────────────
      - type: glance
        title: Current Status
        show_state: true
        entities:
          - entity: select.ev_charge_mode
            name: Mode
          - entity: binary_sensor.ev_connected
            name: Car
          - entity: YOUR_CHARGER_SWITCH   # ← replace with your charger switch
            name: Charger
          - entity: sensor.ev_low_carbon_now
            name: Carbon OK
            icon: mdi:leaf

      # ── Charging Session ─────────────────────────────────────────────────
      - type: glance
        title: Charging Session
        show_state: true
        entities:
          # Remove ev_charge_rate if you did not configure a power sensor.
          - entity: sensor.ev_charge_rate
            name: Charge Rate
            icon: mdi:lightning-bolt
          - entity: sensor.ev_charge_current
            name: Amps
            icon: mdi:current-ac

      # ── Carbon Signal Gauges ─────────────────────────────────────────────
      - type: vertical-stack
        cards:
          - type: gauge
            title: CO2 Z-Score
            entity: sensor.ev_co2_z_score
            min: -3
            max: 3
            needle: true
            severity:
              green: -3     # cleaner than Strict threshold (-0.18σ)
              yellow: -0.18 # Strict–Moderate band
              red: 0.92     # above Lenient threshold

          - type: gauge
            title: CO2 Intensity
            entity: YOUR_CO2_SENSOR   # ← replace with your CO₂ sensor
            unit: gCO₂/kWh
            min: 0
            max: 600
            needle: true
            severity:
              green: 0
              yellow: 250
              red: 400

          - type: gauge
            title: Fossil Fuel %
            entity: YOUR_FOSSIL_SENSOR   # ← replace with your fossil % sensor
            unit: "%"
            min: 0
            max: 100
            needle: true
            severity:
              green: 0
              yellow: 50
              red: 75

      # ── CO2 Intensity History ────────────────────────────────────────────
      - type: history-graph
        title: CO2 Intensity (48h)
        hours_to_show: 48
        entities:
          - entity: YOUR_CO2_SENSOR   # ← replace with your CO₂ sensor
            name: CO2 Intensity

      # ── Z-Score History ──────────────────────────────────────────────────
      - type: history-graph
        title: CO2 Z-Score (48h)
        hours_to_show: 48
        entities:
          - entity: sensor.ev_co2_z_score
            name: Z-Score

      # ── Charge Window History (optional — requires custom:plotly-graph) ──
      # Install plotly-graph from HACS Frontend to use this card.
      # Green fill = carbon gate open, blue = Z-score, orange = charger on/off.
      - type: custom:plotly-graph
        title: Charge Windows (48h)
        hours_to_show: 48
        refresh_interval: 300
        entities:
          - entity: sensor.ev_low_carbon_now
            name: Carbon OK
            yaxis: y2
            fill: tozeroy
            fillcolor: "rgba(0,200,80,0.15)"
            line:
              color: "rgba(0,200,80,0.4)"
              width: 1
          - entity: sensor.ev_co2_z_score
            name: Z-Score
            yaxis: y
            line:
              color: "rgba(60,120,220,0.9)"
              width: 2
          - entity: YOUR_CHARGER_SWITCH   # ← replace with your charger switch
            name: Charger
            yaxis: y2
            line:
              color: "rgba(255,160,0,0.85)"
              width: 2
        layout:
          yaxis:
            range: [-3, 3]
            title: Z-Score (σ)
          yaxis2:
            range: [0, 1.5]
            overlaying: y
            side: right
            showgrid: false
            showticklabels: false
          shapes:
            - type: line
              x0: 0
              x1: 1
              xref: paper
              y0: -0.18
              y1: -0.18
              line:
                color: "rgba(50,200,80,0.6)"
                width: 1
                dash: dot
            - type: line
              x0: 0
              x1: 1
              xref: paper
              y0: 0.47
              y1: 0.47
              line:
                color: "rgba(255,160,0,0.6)"
                width: 1
                dash: dot
            - type: line
              x0: 0
              x1: 1
              xref: paper
              y0: 0.92
              y1: 0.92
              line:
                color: "rgba(220,60,60,0.6)"
                width: 1
                dash: dot

      # ── 30-Day CO2 Trend ─────────────────────────────────────────────────
      - type: statistics-graph
        title: CO2 Intensity – 30 Day Trend
        days_to_show: 30
        period: day
        stat_types:
          - mean
          - min
          - max
        entities:
          - entity: YOUR_CO2_SENSOR   # ← replace with your CO₂ sensor
            name: CO2 Intensity

      # ── 30-Day Z-Score Trend ─────────────────────────────────────────────
      - type: statistics-graph
        title: CO2 Z-Score – 30 Day Trend
        days_to_show: 30
        period: day
        stat_types:
          - mean
          - min
          - max
        entities:
          - entity: sensor.ev_co2_z_score
            name: Z-Score

      # ── Controls ─────────────────────────────────────────────────────────
      - type: entities
        title: Controls
        entities:
          - entity: select.ev_charge_mode
            name: Charge Mode
          - entity: select.ev_carbon_sensitivity
            name: Carbon Sensitivity
          - entity: number.ev_departure_hour
            name: Departure Prep Hour
```

</details>

---

## Statistics Warmup

The Z-score requires approximately **7 days of CO₂ data** before it becomes meaningful. During this warmup period:

- The Z-score is computed as soon as 2 readings exist in the rolling window, but with very few data points the mean and standard deviation are not yet representative. If only 1 reading (or none) is available, the Z-score reports as unavailable.
- When all readings are identical (stdev = 0), the Z-score is reported as `0.0` (exactly at the mean).
- The carbon gate defaults to `False` while the Z-score is unavailable, so charging falls back to the scheduled windows (overnight and midday).
- The 30-day statistics become accurate after 30 days but are used only for display; they do not affect charging decisions.

---

## Troubleshooting

**The charger isn't turning on even on clean-grid days.**
- Check that `ev_connected` is `on`. If not, verify the **Connection Attribute** and **Not-Connected Value** settings match your charger's actual attributes (look in Developer Tools → States).
- Confirm `ev_low_carbon_now` is `True`. If not, check the Z-score value and your chosen sensitivity mode.
- Make sure the charge mode select is set to `auto`.

**The Z-score shows as unavailable.**
- The integration needs ~7 days of accumulated readings. Check the logs for warmup messages.
- If restarting HA causes temporary unavailability, this is expected — statistics rebuild within a few minutes from persisted history.

**I want to charge right now regardless of carbon.**
- Set the `ev_charge_mode` entity (or the select card in your dashboard) to `force_on`.

**The fossil fuel sensor is from a different source / has different units.**
- Any HA sensor that exposes a 0–100 numeric percentage will work. Just select it in step 1 of the configuration.

---

## Contributing

Bug reports and pull requests are welcome at [github.com/andrewreaganm/carbon-aware-ev-charging](https://github.com/andrewreaganm/carbon-aware-ev-charging).

If you have tested this integration with a charger other than Emporia and it works, please open an issue to let us know so we can update the compatibility list. If it does not work, feel free to create an issue.

---

## License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html).

## Attribution

Logo provided by flaticon.com
