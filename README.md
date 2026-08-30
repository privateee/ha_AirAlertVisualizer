# DroneVisualizer

Reads Ukrainian air-threat Telegram channels, works out **what** is flying,
**where** it is and **where it is heading**, deduplicates the same object
reported by several channels, and shows it on a live map with the raw message
stream beside it.

Layout: interactive map on the left (threat markers sized by report count,
dashed heading lines), filtered Telegram stream on the right.

Default channels: `war_monitor`, `AerisRimor`, `kpszsu`, `vanek_nikolaev`.

## What it does

- **Ingest** – polls the public web preview `https://t.me/s/<channel>` (no
  login, no API key). One ingestion backend is abstracted behind
  `dronevis/ingest/base.py`; a Telethon/MTProto backend can be dropped in
  later without touching parsing or the UI.
- **Understand the vocabulary** – Ukrainian *and* Russian, slang and typos,
  folded into stable slugs. "дрон / дрони / шахед / шахід / мопед / герань /
  БпЛА / безпілотник" → `shahed`; "реактив / реактиви / реактів / реактивний
  БпЛА / реактивний мопед" → `jet_uav`; plus `cruise_missile`, `ballistic`,
  `kab`, `recon_uav`, `aircraft`. See the table in
  [`dronevis/parse/threats.py`](dronevis/parse/threats.py).
- **Locate** – a seed gazetteer of ~150 Ukrainian toponyms (Kyiv area in
  detail, oblast centres nationwide, plus the Russian/Belarusian border
  regions used as "from" markers). Declension-tolerant matching
  ("Борисполя" → Бориспіль), local nicknames ("соф борщага" → Софіївська
  Борщагівка), oblast-header disambiguation ("Полтавщина: …").
  Expand it with [`scripts/build_gazetteer.py`](scripts/build_gazetteer.py)
  (GeoNames).
- **Direction & heading** – parses `повз X`, `курсом на Y`, `у напрямку`,
  `з боку`, `від`, `подлетают к`, `в сторону`, `довкола` … and derives a
  compass heading from source→position or position→destination.
- **Deduplicate** – events of the same *family*, close in time and space (or
  wording, when un-located), collapse into one cluster carrying the union of
  the reporting channels and the reported **group size** (`3х`, `5 шахедів`),
  with the peak size kept too. Tunable in `config.yaml`.
- **Trajectory chaining** – "5 БпЛА повз Славутич" then "5 на Димер" a few
  minutes later, from the same channel, is treated as one moving group: it
  merges across a larger distance when the jump is *downrange* of the known
  heading and reachable at that threat's cruise speed in the elapsed time. A
  clearly different group size (5 vs 1) blocks the chain.
- **Guess the movement** – a cluster's successive positions are drawn as a
  path; the marker sits at the **latest** fix and the heading comes from the
  last observed leg. "з київського водосховища на київ" places the marker on
  the reservoir with an arrow to Kyiv; "на Київ з півночі" places it on Kyiv
  heading south.
- **Show** – Leaflet map (vendored, works offline); marker badge shows the
  drone count (or report count), size scales with group size; movement paths
  and dashed heading lines; a time-window selector (5 min → 48 h) and a
  history scrubber; the filtered Telegram
  stream on the right with per-message threat tags.

## Quick start (Windows 11)

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.yaml config.yaml
.venv\Scripts\python -m dronevis run
```

Open <http://127.0.0.1:8750>. The first run backfills a few pages per channel,
then polls every 60 s.

### Launcher (Windows 11)

Prefer a window with buttons? Double-click **`DroneVisualizer.bat`**
(or `DroneVisualizer.vbs` for no console flash). It opens a small panel:

* **Start / Stop** the server
* **Open in browser**
* live server log
* detects an already-running instance, and offers a one-click
  `pip install -r requirements.txt` on first run if packages are missing

Run **`Create Desktop Shortcut.bat`** once to drop a `DroneVisualizer`
icon on your Desktop. (Only `python` needs to be installed; the launcher
uses `.venv` if it exists, otherwise your system Python.)

### Other commands

```bat
.venv\Scripts\python -m dronevis ingest                 :: fetch once, exit
.venv\Scripts\python -m dronevis reparse                :: rebuild events/clusters from stored posts
.venv\Scripts\python -m dronevis parse "шахед курсом на Київ"   :: debug one message
.venv\Scripts\python -m dronevis stats
```

## Run it as a container / Home Assistant add-on

Any deployment can be configured entirely with `DRONEVIS_*` environment
variables (see `dronevis/config.py`) — no config file needed.

**Docker (anywhere, incl. the HA host's Docker):**

```bash
docker compose up -d        # -> http://<host>:8750
```

Persistent data lives in the `dronevis-data` volume (`/data/dronevis.db`).
Edit the `environment:` block in `docker-compose.yml` for channels / area /
theme.

**Home Assistant add-on (sidebar panel via ingress, uses HA auth):**

The `addon/` folder is a ready HA add-on that `pip install`s the app from git.
Point `repository.yaml`, `addon/config.yaml` and `addon/build.yaml` at your
fork, then in HA: **Settings → Add-ons → Store → ⋮ → Repositories**, add your
repo URL, install **DroneVisualizer**, Start, open it from the sidebar. Full
notes in [`addon/DOCS.md`](addon/DOCS.md).

Key env vars: `DRONEVIS_CHANNELS` (comma-sep), `DRONEVIS_POLL_INTERVAL`,
`DRONEVIS_AREA_CENTER` (`lat,lon`), `DRONEVIS_AREA_RADIUS_KM`,
`DRONEVIS_MAP_THEME`, `DRONEVIS_TILE_URL[_DARK]`, `DRONEVIS_DB_PATH`,
`DRONEVIS_HOST`/`DRONEVIS_PORT`, `DRONEVIS_LOG_LEVEL`.

## Mobile

The UI is responsive and installable ("Add to Home Screen" — it has a web
manifest). On a phone: the map is full-screen, the feed is a **bottom sheet**
that peeks the latest alert and taps open to ~80 %; filters (area / window /
threat types / channels / search) collapse into a **☰ drawer**; tap a feed
item to fly the map to it. Everything is one HTML/CSS/JS bundle — no build
step.

## Configuration

`config.yaml` (git-ignored; copy from `config.example.yaml`) or `DRONEVIS_*`
env vars. Highlights:

| Key | Meaning |
|---|---|
| `sources.channels` | channels to read |
| `sources.backfill_pages` | history depth on first run (~16–20 posts/page) |
| `poll.interval_seconds` | how often to fetch new posts |
| `areas.default` / `areas.defined` | map area of interest (circle or bbox); UI filters to it |
| `dedupe.time_window_minutes` / `max_span_minutes` / `distance_km` | clustering tightness |
| `dedupe.trajectory` / `speed_kmh` / `speed_slack` / `heading_tolerance_deg` / `count_tolerance` | trajectory-chaining envelope |
| `parse.terse_channels` | channels that post bare toponyms with no threat word |
| `parse.llm.*` | optional local-LLM fallback (off by default) |
| `server.tile_url` / `tile_url_dark` | day / night basemaps; leave `tile_url_dark: ""` to just CSS-dim the light tiles offline |
| `server.map_theme` | `dark` (default) or `light`; the ☀/🌙 button in the header overrides it per-browser |

### Optional LLM fallback

Rules-only by default so it runs on a Raspberry Pi fully offline. If you set
`parse.llm.enabled: true` with `provider: ollama` (or `openai_compatible`),
the model is asked for strict JSON **only** for posts the rules could not turn
into a located threat; extracted place names are re-resolved through the same
gazetteer.

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /api/config` | areas, channels, threat colours, tile config |
| `GET /api/clusters?area=&since=-6h&threats=&channels=&min_conf=` | map data |
| `GET /api/messages?channels=&since=-12h&q=&limit=` | stream panel |
| `GET /api/stats` | row counts, last ingest |
| `POST /api/ingest` | fetch now |
| `POST /api/reparse` | rebuild derived data |

`since` / `until` accept ISO timestamps or relative forms like `-90m`, `-6h`,
`-2d`.

## Tests

```bat
.venv\Scripts\python -m pytest -q
```

Covers the threat vocabulary, toponym resolution, direction/heading
extraction and the deduper. No network.

## Layout

```
dronevis/
  ingest/      t.me/s/ scraper (Source ABC for future backends)
  parse/       normalize · threats · directions · pipeline · llm
  geo/         gazetteer.json + resolver + spherical helpers
  dedupe.py    cross-channel clustering
  areas.py     areas of interest
  service.py   ingest → parse → dedupe orchestration
  api.py       FastAPI app + background poller
  web/         Leaflet UI (vendored, offline-capable)
scripts/build_gazetteer.py   merge GeoNames into the seed gazetteer
launcher.pyw                 Tkinter Start/Stop window (Windows)
DroneVisualizer.bat / .vbs   double-click to open the launcher
Dockerfile / docker-compose.yml   run it anywhere as a container
addon/                       Home Assistant add-on (ingress panel)
pyproject.toml               pip-installable package (dronevis entrypoint)
```

## Notes & limits

- The `t.me/s/` preview gives ~last 1000 posts, no edit/delete signal, and a
  few minutes of latency. Fine for situational awareness, not forensic.
- The seed gazetteer is Kyiv-centric; other regions resolve to oblast centres
  until you expand it. `vanek_nikolaev`'s hyper-local Mykolaiv nicknames are
  mostly unresolved out of the box.
- Heading is a straight-line estimate between named points, not a track.
- Roadmap: Telethon backend, MBTiles offline tiles, Home Assistant add-on
  packaging, per-area gazetteer packs.

## Attribution

Map data © OpenStreetMap contributors. Gazetteer expansion uses GeoNames
(CC BY 4.0). Telegram content belongs to the respective channels.
