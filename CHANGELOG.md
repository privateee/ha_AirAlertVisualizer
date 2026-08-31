# Changelog

All notable changes to DroneVisualizer. Dates are UTC.

## 0.9.5 — 2026-08-31

- **Add-on icon.** `dronevisualizer/icon.png` (the app's radar mark) now shows
  in the Home Assistant add-on store and on the add-on page.
- **Fixed: the feed sheet was impossible to close on a phone.** Tapping a map
  bubble raised the sheet to show that report; tapping again (on the handle)
  raised it *further* instead of closing. The sheet is now a simple two-state
  toggle — the handle opens and closes it, tapping the map closes it, and it
  leaves a strip of map visible so that target always exists.

## 0.9.4 — 2026-08-31

- **Desktop header fits one line again.** A collapsed "Threats" section no
  longer claims a full row — it only spreads out when you open it — so on a
  normal-width window the whole bar (filters + actions) sits on a single row.
- **New collapsible "Options" section** for the secondary buttons
  (my-location / alert-sound / language), mirroring "Threats". Collapsed by
  default; its state is remembered per browser.

## 0.9.3 — 2026-08-31

- **Legend folded into the threat filter.** The separate Legend panel only
  repeated the family groups already shown by the threat chips. It is gone;
  the colour-coded groups now double as the legend, wrapped in a collapsible
  "Threats" section (open/closed state remembered per browser).
- **Fixed a timer leak.** Toggling *Live* (or reconnecting after the server
  restarts) installed an extra "freshness" interval each time. It is now
  installed once and keeps ticking while *Live* is paused, so the freshness
  pill still ages to amber/red when polling is off.
- Minor: the feed-sheet label is localised on first paint; dropped a stray
  duplicate CSS comment.

## 0.9.2 — 2026-08-31

Mobile fixes for the 0.9.0 UI additions.

- **Top bar fits a phone again.** The freshness indicator is now a compact
  pill (`0s` / `2m` / `1.5h`, colour carries the meaning; full text on hover
  and on desktop), the "Live" label collapses to just its checkbox on narrow
  screens, and `📍 / 🔔 / language` moved into the filters drawer. The action
  row also scrolls horizontally as a last resort on very small screens, so it
  can never overflow the page.
- **The feed sheet is no longer a dead end.** Tapping the map steps a raised
  sheet back down (full → half → peek), and opening a marker no longer forces
  the sheet back up on the next poll after you have lowered it.

## 0.9.1 — 2026-08-31

- Home Assistant add-on config: drop keys that only restated HA defaults
  (`startup`, `ingress_port`, `ingress_entry`) and the unused `map: [config:rw]`
  mount — the add-on keeps its database in `/data` and takes all configuration
  via `DRONEVIS_*` env vars, so it never touches `/config`. Clears the
  add-on linter errors; no behaviour change.

## 0.9.0 — 2026-08-31

A large parsing / architecture / UI pass. Bundles what was developed as
Phases 1–4.

### Parsing & data
- **Threat sub-types.** The taxonomy moved to a single table in
  `dronevis/parse/threats.py`; cruise and ballistic families now resolve to
  specific types — `banderol`, `kalibr`, `x101`, `x22`, `cruise_missile`,
  `kinzhal`, `iskander`, `ballistic` — with a "specific beats generic within a
  family" rule. 14 filterable types in total.
- **Nationwide gazetteer.** `dronevis/geo/data/gazetteer.seed.json` is the
  curated source; `scripts/build_gazetteer.py` merges GeoNames (towns and
  raion centres, ~1,600 places) into the generated `gazetteer.json`. Cyrillic
  display names are picked by transliteration similarity; oblast is assigned
  by nearest seed centre. Added local street/district nicknames for Mykolaiv,
  Kharkiv and Zaporizhzhia.
- **All-clear handling.** "відбій / чисто / пролетів повз" near a place now
  resolves the open clusters there (`cluster.resolved_at`); resolved clusters
  drop off the map and sensors. `/api/clusters?include_resolved=1` to see them.
- **Summary-post filtering.** Daily-digest / recap posts no longer spawn
  dozens of phantom events.
- **Area by destination.** A threat counts as "in your area" if its position
  **or** its stated destination is inside the area.

### Home Assistant sensors (MQTT discovery)
- A `DroneVisualizer` device is published over MQTT discovery (auto-detects
  the Mosquitto broker add-on, or set `mqtt.host`):
  - `binary_sensor.dronevis_<type>` — one per threat type, on when a cluster
    of that type has its position or destination in your area. Attributes:
    `count`, `nearest_km`, `nearest_bearing`, `nearest_place`, `heading_to`,
    `sources`, `confidence`, `updated`.
  - `binary_sensor.dronevis_danger` — any threat type on.
  - `binary_sensor.dronevis_alarm` — on **only** for the threat types you list
    in `alarm_threats` (default `ballistic`, which expands to Kinzhal +
    Iskander + ballistic) above `alarm_min_confidence`. Attribute `message` is
    a ready-to-speak string.
  - `sensor.dronevis_active`, `sensor.dronevis_nearest_km`,
    `sensor.dronevis_last_update`.
- `blueprints/automation/dronevis_alert.yaml` — a critical-push / TTS
  automation driven by the alarm sensor.
- `GET /api/ha` returns the same snapshot as JSON.

### Architecture & code health
- **Retention.** Raw posts and clusters older than `retain_days` (default 14)
  are pruned every 6 h; SQLite `VACUUM` runs on the `vacuum_days` cadence
  (default 7).
- **`GET /api/health`.** status (`ok` / `degraded`), version, ingest lag, last
  error, DB size, row counts, MQTT connection state.
- **Incremental reparse.** `POST /api/reparse?since_hours=N` (and
  `dronevis reparse --since-hours N`) rebuilds only the recent window instead
  of wiping everything; clusters straddling the cutoff are rebuilt in full so
  trajectory chains stay intact.
- **Structured logging.** `log_format: json` emits one JSON object per line.
- **Packaging.** `pyproject.toml` now derives its version from
  `dronevis.__version__` (single source of truth). `[test]` extra added.
- **CI.** `.github/workflows/ci.yml` — pytest on Python 3.11 / 3.12 plus the
  Home Assistant add-on linter.

### Web UI / UX
- Threat chips grouped by family; a family label toggles the whole group.
- Collapsible colour **legend** in the filters drawer.
- **Freshness pill** — "updated Ns ago", turning amber then red as data goes
  stale.
- **New-threat alert** — map pulse, screen flash and an optional beep
  (🔔 / 🔇 toggle, remembered) when a new cluster appears between polls.
- **"My location"** pin (📍, geolocation, remembered); popups show distance
  and a rough ETA derived from the threat's speed.
- **Marker ↔ feed link** — clicking a map marker highlights and scrolls to its
  feed messages.
- Low-confidence clusters render dimmer; confidence shown in the popup.
- **Activity sparkline** (message volume) above the feed.
- **3-state bottom sheet** — peek → mid → open.
- **Ukrainian / English** UI toggle (EN / UK button), remembered.

### Config / options
New keys (all also settable as `DRONEVIS_*` env vars and as add-on options):
`alarm.threats`, `alarm.min_confidence`, `alarm.in_area_only`,
`alarm.active_minutes`, `mqtt.*`, `database.retain_days`,
`database.vacuum_days`, `log_format`.

### Upgrade notes
- The add-on now installs the app from the **`v0.9.0` git tag**
  (`DRONEVIS_REF` in `dronevisualizer/build.yaml`); HA will offer a rebuild.
- The SQLite schema migrates in place (adds `cluster.resolved_at`).
- MQTT sensors appear automatically once the Mosquitto broker add-on is
  installed; otherwise everything works exactly as before.
