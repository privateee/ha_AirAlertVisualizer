# Changelog

## 0.9.0

**Home Assistant sensors (MQTT discovery).** With the Mosquitto broker add-on
installed, a `DroneVisualizer` device appears with a `binary_sensor` per threat
type, a `dronevis_danger` roll-up, and a `dronevis_alarm` that only fires for
the threat types in `alarm_threats` (default `ballistic`) above
`alarm_min_confidence`. Plus `sensor.dronevis_active` / `_nearest_km` /
`_last_update`. Import `blueprints/automation/dronevis_alert.yaml` for a
ready critical-push / TTS automation.

**Parsing.** Cruise and ballistic missiles now resolve to specific types
(Kalibr, Kh-101, Kh-22, Banderol, Kinzhal, Iskander, …). Nationwide gazetteer
(~1,600 towns and raion centres). "Відбій / чисто" near a place clears the
threats there. Daily summary posts no longer create phantom markers. A threat
counts as "in your area" if its position **or** its destination is inside it.

**Maintenance.** Old data is pruned automatically (`retain_days`, default 14)
with a weekly VACUUM. New `GET /api/health` endpoint. Optional JSON logging
(`log_format: json`).

**UI.** Threat chips grouped by family, colour legend, a freshness indicator,
new-threat flash + optional beep, a "my location" pin with distance/ETA in
popups, map-marker ↔ feed linking, an activity sparkline, a 3-state feed
sheet, and a Ukrainian / English toggle.

**New options:** `alarm_threats`, `alarm_min_confidence`, `mqtt`,
`retain_days`, `log_format`.

## 0.1.1 – 0.1.5

Add-on packaging fixes: folder/slug match, ingress double-slash routing,
pinned base image, install from a version tag so the Docker layer cache
doesn't serve a stale build.

## 0.1.0

Initial add-on: Telegram air-threat channels on a live ingress map with the
message feed.
