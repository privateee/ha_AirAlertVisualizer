# Changelog

All notable changes to DroneVisualizer. Dates are UTC.

## 1.0.0 — 2026-10-11

First stable release: live map + feed of Ukrainian air-threat Telegram
channels (drones, missiles, КАБ, ballistic), tracks with course and danger to
you / your area, Home Assistant add-on with MQTT sensors and events, phone
layout for the HA companion app. From here on, config keys, HA entity names
and `/api/*` responses change only with a major version.

- **Fix: replies were saved as the message they quote.** t.me shows a
  reply's quoted text first and the scraper took it, so every reply (about
  half of kpszsu's posts) became a copy of an older report and its new one
  was lost - the main reason far fewer marks showed up. Each post now keeps
  its own text and the id of the post it replies to.
- **Replies continue the quoted track.** A channel replying to its own
  report means that drone moved on ("Вінниччина: … Козятина" ↩ "… через
  Хмельниччину у напрямку Рівненщини"), so the reply joins that track even
  where distance rules alone wouldn't; replies starting "Також…" / "Ще
  (один)…" add more drones and stay separate.
- **After the update the last ~12 pages of every channel are re-read once
  in the background**, stored posts with wrong (quoted) text are corrected,
  and the last 48 h are re-parsed.
- **Channel names in any form**: `kievreal1`, `@kievreal1`,
  `t.me/kievreal1`, `https://t.me/s/kievreal1` - anything but the bare name
  used to fetch an empty page silently. A channel with no posts at all now
  logs a warning.
- One wrong / broken channel never stops the others (covered by a test).

## 0.9.22 — 2026-10-10

- **New: city news channels (`parse.news_channels`, default `[kievreal1]`).**
  kievreal1 mixes drone tracking with news, aftermath, power outages,
  politics and ads. For these channels only short tracking posts are read:
  news/aftermath/ads are skipped (they stay in the feed), long articles are
  skipped, and bare places / short status lines count as sightings
  ("Вишгород", "Чабани зниження", "Іванків уважно"). On 235 real posts:
  135 give sightings, no news post produces a marker - previously an
  Ukrenergo article became an *all clear* for Kyiv and debris "у
  Дніпровському районі" landed on Dnipro city.
- **Every channel:** footers ("Надіслати новину @bot / 👉ПІДПИСАТИСЯ",
  "Дивитись ГРАФІКИ"), promo / support links and siren on/off lines are
  stripped before parsing - also inside a one-line edited post. "новину"
  used to match the village "Нове".
- **Fix: region words were typo-corrected into cities** ("з півночі
  Чернігівщини на північ Київщини" became a Чернігів → Київ track).
- **Fix: in a multi-line post the first report line was taken as a header**
  and became every later line's position.
- Gazetteer: Ворзель, Вишеньки, Проців, Мотижин, Ясногородка (Vyshhorod
  raion), Хотів, Страхолісся, Чубинське, Клавдієво-Тарасове (OpenStreetMap
  coordinates), typo "Наливайлівка"; generated villages "Нове" / "Сад" no
  longer match the everyday words.

## 0.9.21 — 2026-10-10

- **Fix: "на Київ з півночі" / "Київ: … в напрямку міста з півночі" put the
  drone on the city centre**, so the course prediction ran a cone on south
  past Kyiv ("0 км … віддаляється"). "The city" now means the header's city
  and it's the *destination*; a target + side of approach places the mark
  15 km out on that side ("Київ (з півночі)"), heading in. "Над містом"
  still means over the city.
- **No forecast cones for drones** (Shahed, jet, recon, and untyped
  war_monitor sightings): a place name every few minutes made the wedge
  mostly noise. The "probably here now" estimate goes with it. Missiles and
  КАБ keep their cones. Danger / ETA is still computed for drones.
- **No projection from a whole-city fix** (Київ, Харків, Одеса…): no cone,
  heading arrow or "now" mark - it only says "somewhere in the city".
  District fixes (Позняки, ДВРЗ…) are unaffected.

## 0.9.20 — 2026-10-09

- **Fix: the feed was empty with a short window.** 0.9.19 made the feed show
  only the map's window, so at "5 min" it was usually blank. Back to the
  original rule: the feed always covers at least the last 6 hours (longer
  windows widen it), whatever the map shows.

## 0.9.19 — 2026-10-09

Parser and gazetteer fixes from an analysis of ~4000 real posts (the last
~1000 of each channel). On that history: 17% fewer posts with no map event,
+217 located reports, +30% with a heading, more impacts / descents caught.

- **Fix: Zaporizhzhia (the city) could never be matched** — the oblast word
  pattern `запориж…` blocked the city's own name. Oblast words are now
  adjectives only.
- **Places the channels name but the gazetteer lacked:** villages are added
  only when the posts actually mention them (`build_gazetteer.py
  --add-seen posts.json`): Чорнобиль, Прип'ять, Теплик, Теплодар, Затока,
  Лісники, Круглик, Бобрик, Почайна, Наливайківка, Коблеве, Томашпіль,
  Кринички … (126 entries; homonyms resolve by the post's oblast, then
  proximity). Nicknames: Троя → Троєщина, Кроп → Кропивницький, Пуща,
  Заспа, Петрівці → Нові Петрівці; all case forms of Кривий Ріг.
- **Plural town names in the genitive**: "з Черкас", "біля Прилук",
  "Петрівців", "у Черкасах".
- **District adjectives → the district's centre town:** "у Миколаївському
  районі", "Броварський р-н", "Белоцерковском районе".
- **Oblast adjectives (uk + ru)** are the oblast, not its capital: "у
  Черкаській області", "в Сумской области", "на Полтавскую область".
- **Launch origins** give the direction of approach: Крим, Чорне / Азовське
  море, Каспій, Білорусь ("рб").
- **Directions:** "курс Теплик" (no "на"), "у північно-західному напрямку"
  (kpszsu's arrow lines), "в напрямку н.п. Теплик" / "смт …".
- **Statuses:** "приліт" was never seen as an impact (the folded text has no
  "і"); slang "жахнуло / влупило / бахнуло" = impact, "падає / впаде на" =
  descending, "Мінус" next to a place = all clear there.
- **Keyboard typos** — one neighbouring key, one extra letter or two swapped
  ("Ьориспіль", "Святощин", "Виноградао") — resolve when every reading points
  to one place near the area. A *missing* letter is never guessed (that is
  how "Лісники" would become a different village).
- ~~Feed follows the window~~ (reverted in 0.9.20 - it left the feed empty). It used to show at least 6 hours whatever the
  window, so switching to 5 minutes seemed to change nothing; the activity
  graph follows it too.
- Fix: a multi-line post's header ("Загальна по мопедам:") could become a
  phantom track at an oblast named further down.

## 0.9.18 — 2026-10-09

- **Course prediction** (`dronevis/predict.py`). Heading is a recency-weighted
  mean of the last three observed legs (one jittery town-centre fix no longer
  swings it), speed is the *observed* ground speed when the reports span
  enough time (clamped to a plausible band; jet UAVs may be believed up to
  900 km/h, their usual 400–600 stays the default), and the heading
  uncertainty is kept. A stated destination still wins.
- **Map: where it can be, and where it probably is now.** The fixed 28 km
  heading dash is replaced by a cone as wide as the heading is uncertain and
  as long as speed × a per-type horizon (never more than ~60 km), plus a
  dashed ring at the dead-reckoned position *now*. Live view only.
- **Danger to you (or the area centre).** Each track gets a closest-approach
  check against "my location" (sent only to the add-on's own server) or the
  area centre: popup says "⚠ toward you · ~7 min", "moving away" or "passes
  ~12 km" — the old popup ETA was straight-line distance ÷ speed, shown even
  for a target flying away. High / medium tracks get a red / amber ring, the
  header shows the most urgent one (tap to jump to it), and a track turning
  high beeps like a new one. Tracks with no report for `active_minutes`
  (30) and impacts are never a danger.
- **Altitude feeds danger:** a clear drop in reported altitude counts as
  descending even without "зниження"; descending within 25 km is high.
  Popup shows the trend (↓ / ↑).
- **HA:** `sensor.dronevis_threat_level` (none / low / medium / high) and
  `sensor.dronevis_eta` (minutes). Threat sensors and the alarm now also
  count a track outside the area that is on course to reach it within
  `alarm.predict_minutes` (new option, default 15; add-on:
  `alarm_predict_minutes`, 0 = off). Per-track `level` / `toward` /
  `eta_min` in attributes and in `event.dronevis_detected`.
- **Jet UAV top speed 900 km/h** in track chaining, so a fast dash isn't
  split into two tracks.
- **Resizable feed on a phone.** Drag the feed's handle to any height — it
  stays where you let go (remembered). Near the bottom it closes, near the
  top it goes fully up, a quick flick goes all the way; a tap still toggles
  peek ⇄ your last height. The posts at the end stay reachable at any height.
  The sheet now covers the map's zoom buttons instead of the other way
  round, and the attribution sits above the history slider.
- **Fix: a fresh install couldn't fetch anything.** selectolax 1.0 removed
  the parser backend we imported (`selectolax.parser`); switched to the
  Lexbor backend, same API on 0.3.x and 1.x.
- **Faster map redraws:** canvas renderer instead of one SVG node per
  marker / track / dot; the history slider redraws at most once per frame.
- **Lighter feed refresh:** one delegated click handler instead of two
  listeners per post; rows inserted in a single DOM operation.
- **DB index on `event.raw_message_id`:** the feed's event lookup on every
  poll and the cascade delete in prune / reparse were full table scans.
- Dedupe: the event's source post is looked up once per event, not once per
  candidate cluster.

## 0.9.17 — 2026-09-30

- **Search folds behind 🔍** to the right of the feed's activity graph. It
  opens focused; closing it clears the filter, so a hidden search never
  silently filters the feed.
- **⤒ keep the feed at the newest post** (on by default, remembered).
  Every refresh brings the feed back to the top, so scrolling down to read
  no longer means missing what arrived meanwhile. Off: the feed keeps your
  place. Paused while a marker's "show in feed" jump is active.

## 0.9.16 — 2026-09-28

- **Fix: attacks reported only with emoji never reached the map.** Channels
  often put the threat class *only* in a marker emoji — kpszsu
  "🏍 На Мінський масив!" (🏍 = jet UAV, 🛵 = drone), war_monitor
  "🅿️1х Троєщина", "🎮1х Совки 1,8км", "🔻Зниження Либідська",
  "🔄2х сектор Васильків". The emoji were stripped as decoration, the
  lines had no threat word left and were dropped — a whole evening attack
  on Kyiv produced 0 tracks. They're now read as the threat class (🔄 also
  as circling). Emoji that always come with the threat spelled out
  (💣, ☄️, 🛸, 🛫) need no mapping.
- **Target altitude and descent.** "Чайки 400м", "Теремки 3,2км",
  "на висоті 300 м" are parsed as altitude (a distance such as "за 10 км
  від Києва" is not); "зниження" / "снижается" sets a new `descending`
  status. Both are stored per report and per track, shown in the popup
  ("↓ знижується · висота 400 м") and in the feed tags. Status words are
  now translated.
- **Tracking one channel's fixes.** A channel's own consecutive fixes must
  be reachable at the threat's speed (5 km floor, `dedupe.same_channel_floor_km`);
  out of reach is a second object even inside the 20 km cross-channel
  radius, and two places in the same post are always two objects. A fix
  within reach sticks to that channel's own track.
- Gazetteer: Совки, Либідська, Проспект Науки, Биківня, Видубичі, Погреби
  (village), "Солома" for Солом'янка.
- After an update the last 48 h of stored posts are re-parsed once, so
  parser fixes reach the existing map.

## 0.9.15 — 2026-09-26

- **Fix: "show in feed →" on a phone slid the whole app up and stranded it**
  (header and sheet handle off-screen, nothing closable until switching HA
  pages). Two causes:
  - `scrollIntoView()` scrolls every ancestor; on iOS that includes the
    page, even with `overflow: hidden`. The feed list is now scrolled
    directly, and `#main`/`body` use `overflow: clip` so nothing can
    scroll the page (the sheet parked below the fold counted as scrollable
    overflow). A scroll listener snaps the page back as a last resort.
  - `#msgs` lacked `min-height: 0`, so a long post made the list outgrow
    the sheet instead of scrolling inside it.
- The feed keeps its place across polls: rebuilding the list used to throw
  the reader (and a "show in feed" jump) back to the top every refresh. At
  the very top it stays at the top, so new posts still show.

## 0.9.14 — 2026-09-26

- **Fix: phones kept running the broken 0.9.12 code after updating.** The
  HA app's WebView had cached the old `app.js`, and 0.9.13's `no-cache`
  header can't evict a copy cached before it existed. The page now links
  `app.js?v=<version>` / `style.css?v=<version>`, so every add-on update
  is a new URL the WebView must download; and if the running script is
  still older than the server (`/api/config` now reports `version`), the
  page reloads itself once.
- The popup's Sources fold no longer listens to the `toggle` event at all
  and never redraws the popup: the choice is saved from a tap on its
  summary. The 0.9.12 freeze (endless popup re-render, then no taps or
  panning) can't come back through this path.

## 0.9.13 — 2026-09-26

- **Fix (0.9.12 regression): on a phone, tapping a marker froze the page** —
  the popup couldn't be closed and nothing but map panning responded.
  Inserting the popup's open "Sources" `<details>` fires a `toggle` event;
  its handler called `popup.update()`, which rebuilt the lazy content,
  which fired `toggle` again — an endless re-render loop (~350/s). The
  handler now reacts only to a real fold/unfold by the user.
- The page's own files (html/js/css) are now served `Cache-Control:
  no-cache`, so browsers and the HA app revalidate them on every load and
  pick up an add-on update instead of running a stale `app.js`.

## 0.9.12 — 2026-09-26

- **Fix: Refresh/Fetch sometimes didn't show new marks** until the HA page
  was switched away and back. Causes fixed:
  - live mode hid clusters whose last post looked "in the future" to a
    device clock running slightly behind the server;
  - a manual Fetch during a background poll was silently skipped and
    returned "0 new" — it now waits for the poll and then ingests;
  - out-of-order responses (a slow poll finishing after a manual refresh)
    could overwrite newer data — stale responses are now dropped;
  - timers are frozen while the HA companion app is backgrounded — the page
    refreshes as soon as it becomes visible again if its data is >15 s old;
  - all `/api/` responses are sent `Cache-Control: no-store` and fetched
    with `cache: "no-store"`.
- An open popup now stays open (and pinned) across data updates instead of
  closing on every poll.
- Popup "Sources (N)" is foldable — folded by default on mobile, remembered
  per layout.
- Feed channel chips fold behind "Sources (N)" — folded by default on
  mobile; shows "(on/total)" while some channels are filtered out.
- Fetch button shows a busy state and reports how many new posts arrived.
- Popup content is built lazily on open (faster redraws with many tracks).

## 0.9.11 — 2026-09-22

- **New `event.dronevis_detected` sensor.** Fires once per newly-seen
  report, even while a `binary_sensor` is already on — the existing
  `binary_sensor.dronevis_alarm` only re-triggers on an off→on edge, so a
  second shahed group reported while the first is still active previously
  never refired anything. `event_type` is the threat slug; attributes
  carry place, count, destination, distance/bearing, sources and
  confidence. Silent on the first publish after a restart, so already-active
  threats don't all fire at once. Pattern borrowed from
  [ha-aerial-danger](https://github.com/denysdovhan/ha-aerial-danger)'s
  event entity.
- `compute_state()` now also returns per-cluster detail
  (`threats.<slug>.cluster_detail`), not just the type-level rollup.

## 0.9.10 — 2026-09-21

- **Stadia Maps setup help where you configure it.** Home Assistant add-on:
  every option now has a name and description in the Configuration tab
  (new `translations/en.yaml`), with step-by-step Stadia setup on
  `tile_url` / `tile_url_dark` - including "paste the whole URL, not just the
  key" and "restart the add-on afterwards". Standalone / Docker / macOS:
  new "Map tiles (Stadia Maps setup)" section in the README (config.yaml,
  `DRONEVIS_TILE_URL[_DARK]`, docker-compose).

## 0.9.9 — 2026-09-12

**Region picker: every oblast, not just Kyiv and Dnipro.** The area dropdown
now offers all 24 Ukrainian oblasts - Kyiv + oblast and Dnipro as before,
"All Ukraine", and every other oblast as "`<Oblast>` + nearby" with a 180 km
radius that reaches into its immediate neighbours (not just the oblast's
own borders).

- This ships as a **built-in fallback**, so it works for the Home Assistant
  add-on too: the add-on has no `config.yaml`/`config.example.yaml` in its
  container at all, so it previously only ever saw "All Ukraine" as an
  area unless you counted its own single custom one. `config.example.yaml`'s
  `areas:` block is now exactly what `dronevis/config.py` falls back to
  when nothing else defines one — editing your own `areas.defined` (in a
  standalone/Docker `config.yaml`) still fully overrides it as before.
- The add-on's `area_label`/`area_center_lat/lon`/`area_radius_km` options
  now add your own area **alongside** the built-in list instead of
  overwriting the "Kyiv + oblast" preset — so it stays selectable even if
  you've pointed your own area somewhere else. If your area exactly matches
  the "Kyiv + oblast" default (i.e. you haven't changed it), nothing extra
  is added.

## 0.9.8 — 2026-09-12

Parsing fixes for "summary-by-type" batch posts - one message with a header
declaring the threat type ("Загальна по мопедам:" / "Общая по мопедам:")
followed by many lines, one group per line, most with no threat word of
their own. A real 14-line report like this was only producing 7-8 correct
events before; now all of them come through correctly, in **both Ukrainian
and Russian**:

- **Lines inherit the header's threat type.** "3 курсом на Ковель" under
  "Загальна по мопедам:" now tags as `shahed` instead of `unknown`; a line
  that states its own type ("1 іскандер на Суми") still keeps that.
- **The header line itself no longer becomes a phantom event** — a bare
  threat-word mention with no count, place, source or destination is a
  section label, not a report.
- **New verbless positional phrasings recognised:** "в районі X" / "у
  районі X", "південніше/північніше/західніше/східніше X" (and the Russian
  "южнее/севернее/западнее/восточнее X"), "крутиться/кружляє/барражує" for
  circling, and a bare "курс західний/южный/etc." heading statement (mapped
  to the compass bearing directly, not inverted like "coming from X").
- **Gazetteer fixes:**
  - 4-letter vowel-ending place names ("Мена") now decline correctly - the
    engine required 5+ letters before.
  - Added the Russian *and* Ukrainian genitive/accusative forms for "Біла
    Церква" ("Белой Церкви" / "білої церкви") and a proper "Нова Одеса"
    entry (Mykolaiv obl.) with its own declined forms, which previously
    resolved to plain "Одеса" - a ~130 km location error.
  - Fixed a case-ending false positive: "південніше"/"північніше" (etc.)
    could get misread as the real village "Південне", stealing the line
    from whatever place was actually named. Also removed an overly short
    "Черкаси" alias that similarly hijacked "Черкаської області".

## 0.9.7 — 2026-09-12

- **Default map tiles switched from `tile.openstreetmap.org` to Stadia Maps.**
  OpenStreetMap's own tile server is for casual browser use only - its Tile
  Usage Policy blocks apps once traffic looks automated, and DroneVisualizer
  was getting 403'd ("Access blocked" tiles across the whole map). New
  installs need one free step: sign up at
  <https://client.stadiamaps.com/signup/>, grab an API key, and paste it over
  `YOUR_STADIA_API_KEY` in `tile_url` / `tile_url_dark` (`config.yaml`, the
  add-on options, or `DRONEVIS_TILE_URL[_DARK]`). Night mode now uses
  Stadia's own dark style by default instead of a CSS-inverted light map
  (still available by leaving `tile_url_dark` empty).

## 0.9.6 — 2026-08-31

- **Mobile: tapping a map marker no longer also raises the feed.** The marker
  popup already shows the report text, so shoving the feed sheet up on top of
  it was redundant. Now a marker tap just opens the popup; tapping the popup
  ("show in feed →") is what raises the feed and scrolls to that message.
  Source links in the popup keep opening Telegram as before. Desktop is
  unchanged — the always-visible feed still scrolls to the message on a
  marker tap.

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
