# Dashboard Implementation Plan — Auto-generated Lovelace Dashboard

## Goal

On integration setup, automatically create a sidebar Lovelace dashboard
(`/carbon-ev-charging`) populated with native HA cards referencing the actual
entity IDs from the config entry. No custom card dependencies required.

---

## Entity Inventory

### Owned by this integration (entity IDs derived from `entry.entry_id`)

| Entity | Type | Key data |
|---|---|---|
| `sensor.ev_co2_z_score` | sensor | `native_value` (σ), attrs: mean_7d, stdev_7d, mean_30d, stdev_30d, co2 |
| `sensor.ev_low_carbon_now` | sensor | "True" / "False" + attrs: predicted_state, should_charge, fossil_pct |
| `sensor.ev_charging_status` | sensor | Human-readable status reason string |
| `sensor.ev_charge_current` | sensor | Amps (A) |
| `sensor.ev_charge_rate` | sensor | kW — only present if power sensor configured |
| `binary_sensor.ev_connected` | binary_sensor | Plug state on/off |
| `select.ev_charge_mode` | select | auto / force_on / force_off |
| `select.ev_carbon_sensitivity` | select | Lenient / Moderate / Strict |
| `number.ev_departure_hour` | number | 0–23 |

### External entities (configured during setup, IDs stored in `entry.data`)

| Config key | Typical use |
|---|---|
| `co2_sensor` | CO2 intensity reading (gCO2/kWh) |
| `fossil_sensor` | Grid fossil fuel percentage |
| `charger_switch` | Physical charger on/off |
| `charger_power_sensor` | Optional — charger wattage |

---

## Dashboard Layout

Single Lovelace view, URL path `carbon-ev-charging`, sidebar icon `mdi:ev-station`.

### Sections (top → bottom)

#### 1. Status Banner

`horizontal-stack` of `entity` cards:

- **Charging Status** — `sensor.ev_charging_status` (full-width text)
- **EV Connected** — `binary_sensor.ev_connected` (icon badge)
- **Low Carbon Now** — `sensor.ev_low_carbon_now` (icon badge)

#### 2. Gauges

`horizontal-stack` of `gauge` cards:

| Gauge | Entity | Range | Severity bands |
|---|---|---|---|
| Z-Score | `sensor.ev_co2_z_score` | -3 → +3 | green < mode threshold, yellow < threshold+1, red above |
| CO2 Intensity | `entry.data[co2_sensor]` | 0 → 800 | green < 150, yellow < 400, red above |
| Fossil Fuel % | `entry.data[fossil_sensor]` | 0 → 100 | green < 40, yellow < 75, red above |

#### 3. Controls

`entities` card:

- `select.ev_charge_mode`
- `select.ev_carbon_sensitivity`
- `number.ev_departure_hour`
- `entry.data[charger_switch]` (external — let user toggle charger directly)

#### 4. Live Metrics (24 h)

`history-graph` card, 24-hour window:

- `entry.data[co2_sensor]` — CO2 intensity line
- `sensor.ev_co2_z_score` — Z-score line
- `sensor.ev_low_carbon_now` — gate open/closed overlay

#### 5. Charge Windows (48 h)

`history-graph` card, 48-hour window:

- `entry.data[charger_switch]` — on/off charge state
- `binary_sensor.ev_connected` — connection state
- `sensor.ev_low_carbon_now` — carbon gate

#### 6. Charge Power (24 h) — conditional

Only included if `charger_power_sensor` is configured in `entry.data`.

`history-graph` card, 24-hour window:

- `sensor.ev_charge_rate` — kW
- `sensor.ev_charge_current` — Amps

#### 7. Statistics (7 d)

`statistics-graph` card, 7-day period:

- `sensor.ev_co2_z_score`
- `entry.data[co2_sensor]`

---

## Implementation Steps

### Step 1 — `dashboard.py`: Build the config dict

Create `custom_components/carbon_aware_ev_charging/dashboard.py`.

Single public function:

```python
def build_dashboard_config(entry: ConfigEntry) -> dict:
    """Return a complete Lovelace storage-mode dashboard config dict."""
```

- Resolves real entity IDs from `entry.data` and `entry.entry_id`.
- Builds the card list per the layout above.
- Conditionally includes the charge-power section.
- Returns a dict ready for the Lovelace storage API.

### Step 2 — Dashboard creation in `__init__.py`

In `async_setup_entry`, after forwarding platforms:

```python
await _async_ensure_dashboard(hass, entry)
```

Implementation:

1. Load the existing Lovelace storage dashboards via
   `hass.data["lovelace"]["dashboards"]` or the `lovelace/dashboards/list`
   WS API.
2. If a dashboard with `url_path == "carbon-ev-charging"` already exists,
   **skip creation** (respect user edits).
3. Otherwise, call the Lovelace storage API to create the dashboard and
   populate it with the config from `build_dashboard_config(entry)`.

API surface (HA internals):

```python
from homeassistant.components.lovelace import dashboard as ll_dashboard
from homeassistant.components.lovelace.const import (
    CONF_URL_PATH,
    MODE_STORAGE,
)
```

Fallback: if the Lovelace API isn't accessible (e.g. HA version too old),
log a warning and skip — the integration works fine without the dashboard.

### Step 3 — Dashboard removal on unload

In `async_unload_entry`, **do not** delete the dashboard. Users may have
customised it. Leave it in place. If the integration is fully removed
(async_remove_entry), optionally clean up.

### Step 4 — Add `"lovelace"` to `after_dependencies`

In `manifest.json`, add `"lovelace"` to the `after_dependencies` array
(alongside `"recorder"`). This ensures the Lovelace component is loaded
before we try to create the dashboard.

### Step 5 — Tests

Add `tests/test_dashboard.py`:

- `test_build_dashboard_config_includes_all_sections` — verifies the config
  dict contains the expected cards and entity references.
- `test_build_dashboard_config_conditional_power` — verifies the charge-power
  section is omitted when `charger_power_sensor` is not configured.
- `test_ensure_dashboard_creates_when_missing` — mocks the Lovelace API,
  verifies dashboard creation is called.
- `test_ensure_dashboard_skips_when_exists` — mocks existing dashboard,
  verifies no duplicate creation.
- `test_ensure_dashboard_handles_missing_lovelace` — verifies graceful
  fallback when Lovelace API is unavailable.

### Step 6 — Documentation

Update `README.md`:

- Note that a sidebar dashboard is auto-created on first setup.
- Mention it can be customised or deleted freely from the HA UI.
- Remove the manual dashboard YAML spoiler section (or keep as reference).

---

## Open Questions

1. **Update on options change?** When the user changes carbon mode or departure
   hour via the options flow, the gauge severity bands become stale. Options:
   (a) regenerate the dashboard on reload (may lose user edits), (b) leave
   it — gauge bands are informational, not critical. **Recommendation: (b).**

2. **Multi-entry support?** If someone adds two config entries (two chargers),
   each gets its own dashboard (`carbon-ev-charging-2`). The URL path should
   incorporate the entry ID or title to avoid collisions.

3. **Custom card enhancement?** If `custom:plotly-graph` is detected via
   `hass.data.get("frontend_panels", {})` or HACS, we could swap
   `history-graph` for richer plotly charts. This is a stretch goal — not in
   the initial implementation.
