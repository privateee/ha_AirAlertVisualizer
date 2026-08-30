# DroneVisualizer — Home Assistant add-on

Reads Ukrainian air-threat Telegram channels, parses drones / missiles /
guided bombs, deduplicates reports across channels, and shows them on a live
map with the message feed. Opens as a sidebar panel through HA **ingress**
(uses HA auth — no extra port to expose).

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add
   `https://github.com/privateee/ha_AirAlertVisualizer`.
   The add-on Dockerfile `pip install`s the app straight from this git URL
   (`DRONEVIS_REF` in `addon/build.yaml` pins the branch/tag). If you fork it,
   repoint `repository.yaml`, `addon/config.yaml` and `addon/build.yaml`.
2. Install **DroneVisualizer** from the store, **Start**, open it from the
   sidebar.

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
| `log_level` | `debug` / `info` / `warning` / `error` |

The SQLite database lives in the add-on's `/data`, so it survives restarts
and updates.

## Notes

* Everything runs offline except the map tiles and the Telegram fetch.
* No Home Assistant entities are created — this is a UI panel, not an
  integration. (A sensor for "active threats in area" could be added later
  via the HA API.)
