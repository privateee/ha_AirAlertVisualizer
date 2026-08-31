# DroneVisualizer — Home Assistant add-on

Reads Ukrainian air-threat Telegram channels, parses drones / missiles /
guided bombs, deduplicates reports across channels, and shows them on a live
map with the message feed. Opens as a sidebar panel through HA **ingress**
(uses HA auth — no extra port to expose).

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add
   `https://github.com/privateee/ha_AirAlertVisualizer`, then **⋮ → Check for
   updates** and scroll to the "DroneVisualizer" section at the bottom.
   The add-on Dockerfile `pip install`s the app straight from this git URL
   (`DRONEVIS_REF` in `dronevisualizer/build.yaml` pins the branch/tag). To
   fork it, repoint `repository.yaml` and `dronevisualizer/{config,build}.yaml`.
2. Install **DroneVisualizer**, **Start**, open it from the sidebar.

## Options

| Option | Meaning |
|---|---|
| `channels` | Telegram channels to read (public `t.me/s/` preview, no login) |
| `poll_interval_seconds` | how often to fetch new posts |
| `backfill_pages` | history depth on first run (~16-20 posts/page) |
| `area_label` / `area_center_lat` / `area_center_lon` / `area_radius_km` | the map area of interest; the UI filters to it |
| `map_theme` | `dark` or `light` (a ☀/🌙 button also toggles it per browser) |
| `tile_url` | day basemap tiles |
| `tile_url_dark` | night basemap; leave empty to invert the day tiles with a CSS filter (no extra CDN, works offline) |
| `retain_days` | drop raw posts / clusters older than this; a VACUUM runs weekly |
| `log_level` | `debug` / `info` / `warning` / `error` |
| `log_format` | `text` (default) or `json` — one JSON object per log line for aggregators |

| `alarm_threats` | which threat types raise `binary_sensor.dronevis_alarm`. Slugs (`banderol`, `kalibr`, `x101`, `x22`, `cruise_missile`, `kinzhal`, `iskander`, `ballistic`, `shahed`, `jet_uav`, `recon_uav`, `kab`, `aircraft`) or a family name (`ballistic` → Kinzhal + Iskander + ballistic) |
| `alarm_min_confidence` | ignore low-confidence parses for the alarm |
| `mqtt` | `auto` (use the Mosquitto broker add-on if installed), `true`, or `false` |

The SQLite database lives in the add-on's `/data`, so it survives restarts
and updates.

## Sensors (MQTT discovery)

With the **Mosquitto broker** add-on installed, DroneVisualizer publishes a
`DroneVisualizer` device with:

| Entity | Meaning |
|---|---|
| `binary_sensor.dronevis_<type>` | one per threat type — on when a cluster of that type has its position **or destination** inside your configured area. Attributes: `count`, `nearest_km`, `nearest_bearing`, `nearest_place`, `heading_to`, `sources`, `confidence`, `updated` |
| `binary_sensor.dronevis_danger` | any threat type on |
| `binary_sensor.dronevis_alarm` | on only for the `alarm_threats` you listed, above `alarm_min_confidence`. Attribute `message` = a ready-to-speak string |
| `sensor.dronevis_active` | active cluster count |
| `sensor.dronevis_nearest_km` | distance to the nearest active threat |
| `sensor.dronevis_last_update` | timestamp of the latest report |

Import `blueprints/automation/dronevis_alert.yaml` for a ready critical-push /
TTS automation driven by `binary_sensor.dronevis_alarm`.

`GET /api/ha` (inside the ingress panel) returns the same snapshot as JSON.

## Health & maintenance

* `GET /api/health` — status (`ok` / `degraded`), version, last-ingest lag,
  last error, DB size, row counts, MQTT connection state.
* `POST /api/reparse` rebuilds every event/cluster from the stored posts;
  `POST /api/reparse?since_hours=6` rebuilds only the recent window (the map
  stays populated). Old data is left untouched.
* Retention pruning + a weekly `VACUUM` run automatically (see `retain_days`).

## Notes

* Everything runs offline except the map tiles and the Telegram fetch.
