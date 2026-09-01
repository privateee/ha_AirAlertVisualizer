# Changelog

## 0.9.6

Mobile: tapping a map marker now just opens the popup - it no longer also
raises the feed (the popup already shows the text). Tap the popup
("show in feed →") to jump to that message in the feed.

## 0.9.5

Added the add-on icon (shows in the store and on the add-on page). Fixed the
feed sheet being impossible to close on a phone — it's now a simple open/close
toggle (handle toggles, tapping the map closes it).

## 0.9.4

Desktop header fits one line again: a collapsed "Threats" section no longer
takes a whole row. Added a matching collapsible "Options" section for the
secondary buttons (location / sound / language), collapsed by default,
state remembered.

## 0.9.3

The separate Legend panel is gone - the colour-coded threat groups now double
as the legend, inside a collapsible "Threats" section (remembers its state).
Fixed a timer leak where toggling *Live* / reconnecting stacked up extra
"freshness" update intervals.

## 0.9.2

Mobile fixes: the top bar fits a phone again (compact freshness pill, "Live"
label collapses, `📍 / 🔔 / language` moved into the filters drawer, action
row scrolls if it must). Tapping the map now lowers a raised feed sheet, and
opening a marker no longer forces the sheet back open after you close it.

## 0.9.1

Config cleanup: removed options that only restated Home Assistant defaults
(`startup`, `ingress_port`, `ingress_entry`) and the unused `config` folder
mount. No behaviour change.

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
