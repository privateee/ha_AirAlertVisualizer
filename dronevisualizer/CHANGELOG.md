# Changelog

## 1.1.4

Map: tap a track leg to see the two posts behind it (distance, time, speed).
New 🏠 button hides the home pin, e.g. for screenshots. Feed: with the 🗺️
filter on and nothing on the map, it now says how many posts are hidden
instead of looking empty.

## 1.1.3

Fix: no more impossible jumps (Очаків -> Буча, Одеса -> Донецька обл.) when
a channel replies to a post that listed several drones - the reply now
continues the nearest drone that could have got there. Fix: the Переяслав ->
Вишгород line - monitor_ukr's per-line "з <області>" is now read per line.
Map: track paths are smooth curves that fade from old to new, so you can
see how a drone moved.

## 1.1.2

Fix: tracks no longer draw long lines joining different drones (e.g.
Переяслав -> Вишгород across Kyiv, or Kyiv -> Вороньків): kpszsu's
"Київ: … на Теремки" posts are placed at the district, and a report that
says where it's coming from doesn't join a track flying another way.
"Правий / Лівий берег" are Kyiv's banks (they were drawn in Mariupol / near
Dnipro). Power-news lines from kievreal1 no longer show as sightings.

## 1.1.1

"Close to home" for drones is now 5 km instead of 30 km, so a drone
somewhere around the city no longer keeps the danger amber all night.
Missiles, КАБ and aviation keep 30 km.

## 1.1.0

- **Ballistic / missile threats from Брянськ, Курськ, Бєлгород, Воронеж,
  Крим** now show for your area, with a trajectory to the target and
  distance + flight time next to it. One missile is one track (several
  channels merge, follow-ups update it, "спуск" makes it final). "Відбій"
  ends a threat; a quiet one is marked as a possible false alarm.
- **Bombers in the air** (МіГ-31К, Ту-22М3, Ту-95МС / Ту-160): a timer chip
  from takeoff; relocations are ignored.
- **Your Home Assistant home** is used for danger and ETA (option
  `use_ha_home`); threats within 30 km of home look more urgent. The add-on
  now asks for Home Assistant API access to read it.
- **Faster updates during a threat**: new option
  `poll_fast_interval_seconds` (default 15 s); 60 s while quiet.
- Map: arrows to off-screen tracks (🧭), reset / set default view (⌂ 📌).
  Feed: "only what's on the map" filter (🗺️). Light theme for the whole UI.
- More Kyiv-area places and the slang channels use.

## 1.0.0

First stable release.

Fix: replies were saved as the post they quote, so about half of kpszsu's
reports were lost - the main reason far fewer marks showed up. Replies now
keep their own text and continue the quoted drone's track ("Також…" / "Ще
один…" replies stay separate drones). After the update the recent posts of
every channel are re-read once in the background and corrected. Channel
names can be entered as `kievreal1`, `@kievreal1` or `t.me/kievreal1`; a
wrong name only logs a warning and never stops the other channels.

## 0.9.22

New: city news channels such as **kievreal1** can be added to the channel
list - only their drone tracking posts reach the map; news, power outages,
aftermath and ads are ignored (they still show in the feed). Footers, promo
links and siren on/off lines are ignored for every channel. Fixes: region
words no longer turn into cities ("Чернігівщини" ≠ Чернігів), and lines of a
multi-line post are no longer anchored to the first line's place. More
villages around Kyiv.

## 0.9.21

Fix: "в напрямку Києва з півночі" put the drone on Kyiv's centre and drew a
course cone south past the city. Kyiv is now the target and the drone is
shown on its northern approach, heading in. Drones no longer get forecast
cones at all (missiles and КАБ still do), and nothing projects a course from
a mark on a whole city (Київ, Харків…), only from districts.

## 0.9.20

Fix: the feed was empty when a short map window (e.g. 5 min) was selected.
It again always shows at least the last 6 hours.

## 0.9.19

Better reading of how the channels really write, from ~4000 real posts:
Zaporizhzhia is recognised again; villages the channels name (Чорнобиль,
Теплик, Затока, Лісники, Круглик …) and nicknames (Троя, Кроп, Пуща) are on
the map; district and oblast adjectives ("у Миколаївському районі", "в
Сумской области"), "курс Теплик", "у західному напрямку", launch origins
(Крим, Чорне море, Білорусь), slang ("жахнуло", "падає", "Мінус") and simple
keyboard typos are understood. 17% fewer posts without a map marker, 30% more
with a heading. The feed now shows the same period as the map window.

## 0.9.18

Course prediction and danger: tracks show a cone of where they can be in the
next minutes and where they probably are now; the popup tells you whether a
target is heading toward you (with ETA), passing by, or moving away; dangerous
tracks get a red / amber ring and the most urgent one is shown in the header.
New HA sensors `sensor.dronevis_threat_level` and `sensor.dronevis_eta`, and a
new option `alarm_predict_minutes` (default 15) so a threat on course for your
area counts before it crosses the boundary. A dropping reported altitude
counts as descending. Jet UAVs may be tracked up to 900 km/h.

The feed on a phone is resizable: drag its handle to any height and it stays
there. Fixes a dependency change (selectolax 1.0) that would have stopped a
freshly built add-on from fetching posts. Faster map and feed redraws, and a
database index that speeds up the feed and cleanup.

## 0.9.17

Feed: search is folded behind a 🔍 button next to the activity graph (closing
it clears the filter). New ⤒ toggle (on by default) keeps the feed at the
newest post on every refresh, so you don't miss new messages after scrolling
down; turn it off to keep your place.

## 0.9.16

Fix: attacks reported only with emoji ("🏍 На Мінський масив!", "🅿️1х
Троєщина", "🔻Зниження Либідська") produced no map markers — the emoji
were ignored, so e.g. an evening attack on Kyiv showed 0 tracks. They're now
read as the threat type. New: target altitude ("400м", "3,2км") and
"зниження" (descending) are shown in the popup and feed. One channel's rapid
consecutive reports are chained into one path only when physically
reachable. More Kyiv districts. Recent history is re-parsed once after the
update.

## 0.9.15

Fix: on a phone, "show in feed →" in a marker popup slid the whole app up
under HA's header, leaving nothing to tap to close it. The feed now scrolls
only itself, and the page can no longer be scrolled at all. The feed also
keeps your place when it refreshes instead of jumping back to the top.

## 0.9.14

Fix: on a phone the HA app kept running the cached, broken 0.9.12 script
(popup couldn't be closed, map and buttons stopped responding). The page now
loads its script/styles by version, so every update is fetched fresh, and it
reloads itself once if it finds it's older than the add-on. The popup's
Sources fold was also reworked so it can never redraw itself in a loop.

## 0.9.13

Fix a 0.9.12 regression: on a phone, tapping a marker froze the page (popup
couldn't be closed, only map panning worked) when the popup's "Sources" list
was unfolded. Also: the web UI's files are now always revalidated, so the HA
app picks up updates instead of running a cached old version.

## 0.9.12

Fix: Refresh/Fetch sometimes didn't show new marks until you switched HA pages
back and forth (device clock skew, Fetch skipped during a background poll,
stale out-of-order responses, frozen timers in the background, caching).
Open popups stay open across updates. On mobile, popup sources and the feed's
channel chips are folded behind "Sources (N)". Fetch shows a busy state and
the number of new posts.

## 0.9.11

New `event.dronevis_detected` entity: fires once per newly-seen report, even
while `binary_sensor.dronevis_alarm` is already on (which only re-triggers on
off→on). Use it to get notified about *every* new report, not just the first
one. `event_type` is the threat slug; attributes carry place/count/
destination/distance/sources/confidence.

## 0.9.10

Every option now has help text in the Configuration tab, including step-by-step
Stadia Maps setup for `tile_url` / `tile_url_dark`.

## 0.9.9

The region picker now offers every Ukrainian oblast, not just Kyiv and
Dnipro - each as "`<Oblast>` + nearby" with a 180 km radius reaching into
its neighbours, plus "All Ukraine". Your own `area_label`/`area_center_*`/
`area_radius_km` option is added alongside this list rather than
overwriting "Kyiv + oblast", so both stay selectable.

## 0.9.8

Better parsing of "summary by type" posts (one header line declaring the
threat, then many lines with no threat word of their own) - lines now
correctly inherit the header's type instead of showing as "unknown", the
header line itself no longer shows up as a phantom marker, and several new
phrasings are recognised ("в районі X", "південніше/южнее X", bare "курс
західний"), in both Ukrainian and Russian. Also fixes a couple of gazetteer
mix-ups: "Нова Одеса" no longer resolves to plain "Одеса" (~130 km off), and
"південніше"/"північніше" no longer get misread as the village "Південне".

## 0.9.7

Default map tiles switched from `tile.openstreetmap.org` (casual-browser-only,
started 403-blocking the add-on) to Stadia Maps. One free step needed: sign up
at [client.stadiamaps.com/signup](https://client.stadiamaps.com/signup/), then
paste your API key over `YOUR_STADIA_API_KEY` in the `tile_url` /
`tile_url_dark` options. Night mode now uses Stadia's own dark style instead
of a CSS-inverted light map (leave `tile_url_dark` empty to go back to that).

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
