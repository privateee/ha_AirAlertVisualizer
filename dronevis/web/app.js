"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const WINDOW_MS = {
  "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5, "2h": 2 * 36e5, "3h": 3 * 36e5,
  "6h": 6 * 36e5, "12h": 12 * 36e5, "24h": 24 * 36e5, "48h": 48 * 36e5,
};

// the version this script was loaded as (index.html links app.js?v=<version>)
const JS_VERSION = new URLSearchParams(
  (document.currentScript && document.currentScript.src.split("?")[1]) || "").get("v");

let CFG = null;
let map = null;
let tileLayer = null;
const layer = L.layerGroup();
const fxLayer = L.layerGroup();        // transient "new threat" pulses
let hereMarker = null;
const markerById = new Map();           // cluster id -> its map marker (this render)
let clusters = [];
let lastMsgs = [];
const shownIds = new Set();              // ids of the clusters drawn right now
const candidateTargets = new Set();      // targets whose candidate arcs are drawn
const labelSlots = new Map();            // target -> labels already stacked there
let timer = null;
let freshBoot = false;                  // freshness ticker installed once
// request tokens: a slow response from an older refresh (e.g. the timer tick
// that was already in flight when Fetch was pressed) must not overwrite the
// newer data that has already been drawn
let clustersSeq = 0;
let msgsSeq = 0;
let rendering = false;                  // true while renderClusters rebuilds layers
const NO_STORE = { cache: "no-store" };
let seenIds = null;                     // cluster ids seen on a previous poll
let highIds = null;                     // ... that were already "high" danger
let dangerRef = null;                   // {lat, lon, kind: "here"|"area"} from the server
let audioCtx = null;

const state = {
  area: null,
  window: "3h",
  threatsOff: new Set(),      // slugs the user unchecked
  channelsOff: new Set(),
  q: "",
  asOf: 1,                    // 0..1 across the window; 1 == now
  live: true,
  mapTheme: "dark",
  here: null,                 // {lat, lon} "my location"
  pinned: null,               // cluster id whose feed rows are highlighted
  lastUpdate: 0,
};

function lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }

// ================================================================ i18n
const I18N = {
  en: {
    brand: "DroneVisualizer", area: "Area", window: "Window", live: "Live",
    fetch: "Fetch", feed: "Feed", filterText: "filter text…", now: "now",
    threats: "Threats", options: "Options", showInFeed: "show in feed →", sources: "Sources",
    newPosts: "new posts", noNewPosts: "no new posts",
    myloc: "My location", locating: "locating…",
    locpin: "You", inFeed: "in feed", new: "new", tracks: "tracks",
    updated: "updated", stale: "no updates for", offline: "server offline",
    showOnMap: "◎ show on map", reports: "reports", peak: "peak",
    heading: "heading", drone: "drone", unit: "unit", eta: "ETA",
    away: "away", connecting: "Connecting to the DroneVisualizer server…",
    cantReach: "Can't reach the DroneVisualizer server on this address.",
    startWith: "Start it with:  python -m dronevis run   — then this page reconnects automatically.",
    retry: "retry", altitude: "alt", search: "Search the feed", hidden: "hidden",
    maybeFalse: "possible false alarm", noTarget: "threat, target unknown", resetView: "Back to the default view",
    minU: "min", aviaSince: "in the air since", aviaWindow: "launches usually after",
    homePin: "Home (from Home Assistant)", homeRef: "home",
    pinToggle: "Show the home / location pin (on/off)",
    edgeMarks: "Arrows to tracks outside the view (on/off)", edgeStart: "launch", edgeEnd: "target",
    saveView: "Make this view the default (position and zoom)", viewSaved: "default view saved",
    onMapOn: "Feed: only posts shown on the map (on)",
    onMapEmpty: "Nothing on the map right now - {n} posts hidden by the 🗺️ filter. Tap to show all.",
    onMapOff: "Feed: only posts shown on the map (off)",
    autoTopOn: "Keep the feed at the newest post: on",
    autoTopOff: "Keep the feed at the newest post: off",
    st_moving: "moving", st_circling: "circling", st_descending: "descending",
    st_launch: "launch", st_impact: "impact", st_clear: "clear", st_unknown: "spotted",
    you: "you", towardYou: "toward", movingAway: "moving away", passes: "passes",
    kmh: "km/h", observedSpeed: "observed", from: "from", min: "min",
    lvl_high: "high danger", lvl_medium: "danger", lvl_low: "",
  },
  uk: {
    brand: "DroneVisualizer", area: "Регіон", window: "Період", live: "Наживо",
    fetch: "Оновити", feed: "Стрічка", filterText: "пошук у тексті…", now: "зараз",
    threats: "Загрози", options: "Опції", showInFeed: "показати у стрічці →", sources: "Джерела",
    newPosts: "нових повідомлень", noNewPosts: "нових немає",
    myloc: "Моє місце", locating: "визначення…",
    locpin: "Ви", inFeed: "у стрічці", new: "нових", tracks: "цілей",
    updated: "оновлено", stale: "немає оновлень", offline: "сервер недоступний",
    showOnMap: "◎ показати на мапі", reports: "повідомлень", peak: "макс",
    heading: "курс", drone: "дрон", unit: "од.", eta: "підліт",
    away: "від вас", connecting: "З'єднання із сервером DroneVisualizer…",
    cantReach: "Не вдається під'єднатися до сервера DroneVisualizer.",
    startWith: "Запустіть:  python -m dronevis run   — сторінка під'єднається сама.",
    retry: "спроба", altitude: "висота", search: "Пошук у стрічці", hidden: "сховано",
    maybeFalse: "можлива хибна тривога", noTarget: "загроза, ціль невідома", resetView: "Повернутися до типового вигляду",
    minU: "хв", aviaSince: "у повітрі з", aviaWindow: "пуски зазвичай через",
    homePin: "Дім (з Home Assistant)", homeRef: "дому",
    pinToggle: "Показувати мітку дому / місця (увімк./вимк.)",
    edgeMarks: "Стрілки до цілей поза екраном (увімк./вимк.)", edgeStart: "пуск", edgeEnd: "ціль",
    saveView: "Зробити цей вигляд типовим (позиція і масштаб)", viewSaved: "типовий вигляд збережено",
    onMapOn: "Стрічка: лише те, що на мапі (увімк.)",
    onMapEmpty: "Зараз на мапі нічого немає — {n} повідомлень приховано фільтром 🗺️. Натисніть, щоб показати всі.",
    onMapOff: "Стрічка: лише те, що на мапі (вимк.)",
    autoTopOn: "Тримати стрічку на найновішому: увімк.",
    autoTopOff: "Тримати стрічку на найновішому: вимк.",
    st_moving: "рухається", st_circling: "кружляє", st_descending: "знижується",
    st_launch: "пуск", st_impact: "вибух", st_clear: "відбій", st_unknown: "помічено",
    you: "вас", towardYou: "на", movingAway: "віддаляється", passes: "мине за",
    kmh: "км/г", observedSpeed: "за треком", from: "від", min: "хв",
    lvl_high: "висока небезпека", lvl_medium: "небезпека", lvl_low: "",
  },
};

// "400 м" / "3,2 км" in the current language
function fmtAlt(m) {
  if (m == null) return "";
  const km = lang === "uk" ? "км" : "km", mm = lang === "uk" ? "м" : "m";
  return m >= 1000
    ? `${(m / 1000).toFixed(1).replace(/\.0$/, "").replace(".", lang === "uk" ? "," : ".")} ${km}`
    : `${m} ${mm}`;
}
let lang = lsGet("lang") || ((navigator.language || "").startsWith("uk") ? "uk" : "en");
if (!I18N[lang]) lang = "en";
function t(k) { return (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k; }
function applyI18n() {
  document.documentElement.lang = lang;
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  const lb = $("#lang");
  if (lb) { lb.textContent = lang.toUpperCase(); lb.title = "Language / Мова"; }
  if (CFG) updateChanSummary();
  updateFeedTools();
}

// ⤒ on: every refresh brings the feed back to the newest post (off: it keeps
// your place). On by default - scrolling down to read used to mean missing
// what arrived meanwhile.
function autoTopOn() { return lsGet("autoTop") !== "0"; }

function updateFeedTools() {
  const at = $("#autoTop"), sb = $("#searchBtn"), s = $("#search"), om = $("#onMapBtn");
  if (om) {
    om.setAttribute("aria-pressed", String(onMapOnly()));
    om.title = t(onMapOnly() ? "onMapOn" : "onMapOff");
  }
  if (at) {
    at.setAttribute("aria-pressed", String(autoTopOn()));
    at.title = t(autoTopOn() ? "autoTopOn" : "autoTopOff");
  }
  if (sb && s) {
    sb.setAttribute("aria-expanded", String(!s.hidden));
    sb.title = t("search");
  }
}

// ---------------------------------------------------------------- layout
const WIDE_MQ = window.matchMedia("(min-width: 900px)");
// pref: "auto" (follow screen width) | "pc" | "mobile"
let layoutPref = lsGet("layout") || "auto";

function layoutIsDesktop() {
  return layoutPref === "pc" || (layoutPref === "auto" && WIDE_MQ.matches);
}
const isMobile = () => !layoutIsDesktop();

function applyLayout() {
  const desktop = layoutIsDesktop();
  document.body.classList.toggle("is-desktop", desktop);
  const btn = $("#layout");
  if (btn) {
    const face = { auto: "🖥️/📱", pc: "🖥️", mobile: "📱" }[layoutPref];
    btn.textContent = face;
    btn.title = "Layout: " + layoutPref + " (tap to change)";
  }
  if (map) setTimeout(() => map.invalidateSize({ pan: false }), 60);
}

function cycleLayout() {
  layoutPref = { auto: "pc", pc: "mobile", mobile: "auto" }[layoutPref];
  lsSet("layout", layoutPref);
  document.body.classList.remove("sheet-open", "filters-open");
  applyLayout();
}

WIDE_MQ.addEventListener("change", () => { if (layoutPref === "auto") applyLayout(); });

// -------------------------------------------------------------- bottom sheet
// Peeking (just the handle) or open at any height. Drag the handle to resize:
// the sheet stays where it's let go - near the bottom it closes, near the top
// it goes fully up, and a quick flick goes all the way. A tap toggles between
// peeking and the last open height (remembered).
const SHEET_SNAP_PX = 56;               // released this close to an end -> snap
const SHEET_FLICK = 0.6;                // px/ms - a flick, not a slow drag
// 0 = fully raised .. 1 = all the way down to the peek
let sheetFrac = (() => {
  const v = parseFloat(lsGet("sheetFrac"));
  return v >= 0 && v < 0.9 ? v : 0;
})();
let sheetNoClickUntil = 0;              // swallow the click that ends a drag

const sheetIsOpen = () => document.body.classList.contains("sheet-open");

// how far the sheet travels between fully raised and peeking, in px. The peek
// is var(--sheet-peek) + the iOS safe area, which only CSS can resolve.
function sheetRange() {
  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;visibility:hidden;" +
    "height:calc(var(--sheet-peek) + var(--safe-b))";
  document.body.appendChild(probe);
  const peek = probe.offsetHeight;
  probe.remove();
  return Math.max(0, $("#stream").offsetHeight - peek);
}
function setSheetY(px) {
  document.body.style.setProperty("--sheet-y", Math.round(px) + "px");
}
function applySheet() { if (isMobile()) setSheetY(sheetFrac * sheetRange()); }

function openSheet() { applySheet(); document.body.classList.add("sheet-open"); unscrollPage(); }
function closeSheet() { document.body.classList.remove("sheet-open"); unscrollPage(); }
function toggleSheet() {
  if (performance.now() < sheetNoClickUntil) return;
  sheetIsOpen() ? closeSheet() : openSheet();
}

function wireSheetDrag() {
  const h = $("#sheetHandle");
  let d = null;
  h.addEventListener("pointerdown", (e) => {
    if (!isMobile() || d || (e.pointerType === "mouse" && e.button !== 0)) return;
    const range = sheetRange();
    d = {
      id: e.pointerId, y0: e.clientY, range, moved: false,
      start: sheetIsOpen() ? sheetFrac * range : range,
      y: 0, lastY: e.clientY, lastT: e.timeStamp, v: 0,
    };
    try { h.setPointerCapture(e.pointerId); } catch (_) {}   // keep moves coming off the handle
  });
  h.addEventListener("pointermove", (e) => {
    if (!d || e.pointerId !== d.id) return;
    const dy = e.clientY - d.y0;
    if (!d.moved) {
      if (Math.abs(dy) < 6) return;           // still a tap
      d.moved = true;
      document.body.classList.add("sheet-open", "sheet-dragging");
    }
    const dt = e.timeStamp - d.lastT;
    if (dt > 0) d.v = 0.6 * ((e.clientY - d.lastY) / dt) + 0.4 * d.v;
    d.lastY = e.clientY; d.lastT = e.timeStamp;
    d.y = Math.min(d.range, Math.max(0, d.start + dy));
    setSheetY(d.y);
  });
  const end = (e) => {
    if (!d || e.pointerId !== d.id) return;
    const g = d;
    d = null;
    document.body.classList.remove("sheet-dragging");
    if (!g.moved) return;                     // a tap: the click handler toggles
    sheetNoClickUntil = performance.now() + 350;
    const v = e.timeStamp - g.lastT > 120 ? 0 : g.v;   // paused before letting go
    if (v > SHEET_FLICK || g.y > g.range - SHEET_SNAP_PX) { closeSheet(); return; }
    const y = v < -SHEET_FLICK || g.y < SHEET_SNAP_PX ? 0 : g.y;
    sheetFrac = g.range ? y / g.range : 0;
    lsSet("sheetFrac", sheetFrac.toFixed(3));
    setSheetY(y);
  };
  h.addEventListener("pointerup", end);
  h.addEventListener("pointercancel", end);
  // rotation / HA sidebar: keep the same proportion of the new height
  window.addEventListener("resize", applySheet);
}

// ---------------------------------------------------------------- map theme
function loadMapTheme() {
  try {
    return localStorage.getItem("mapTheme") || CFG.map_theme || "dark";
  } catch (_) {
    return (CFG && CFG.map_theme) || "dark";
  }
}

function applyMapTheme(theme) {
  state.mapTheme = theme;
  try { localStorage.setItem("mapTheme", theme); } catch (_) {}

  const dark = theme === "dark";
  const haveDarkTiles = dark && !!CFG.tile_url_dark;
  const url = haveDarkTiles ? CFG.tile_url_dark : CFG.tile_url;
  const attr = haveDarkTiles ? CFG.tile_attribution_dark : CFG.tile_attribution;

  if (tileLayer) map.removeLayer(tileLayer);
  tileLayer = L.tileLayer(url, {
    attribution: attr, maxZoom: 19, subdomains: "abcd", crossOrigin: true,
  }).addTo(map);
  tileLayer.bringToBack();

  // no dark basemap configured (offline / Pi) -> dim the light tiles via CSS
  map.getContainer().classList.toggle("tiles-invert", dark && !CFG.tile_url_dark);
  document.body.classList.toggle("map-dark", dark);

  // the browser / iOS status bar follows the theme too
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#10141b" : "#f3f5f8");

  const btn = $("#theme");
  if (btn) {
    btn.textContent = dark ? "☀" : "🌙";
    btn.title = dark ? "Switch to light theme" : "Switch to dark theme";
  }
}

// ---------------------------------------------------------------- helpers
function windowMs() { return WINDOW_MS[state.window] || 3 * 36e5; }
function asOfDate() { return new Date(Date.now() - (1 - state.asOf) * windowMs()); }
function fmtClock(d) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function ageStr(min) {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)}m ago`;
  if (min < 1440) return `${(min / 60).toFixed(1)}h ago`;
  return `${(min / 1440).toFixed(1)}d ago`;
}
function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function clusterConfidence(c) {
  return Math.max(0, ...(c.sources || []).map((s) => s.confidence || 0));
}

// ---------------------------------------------------------------- boot
async function init() {
  const resp = await fetch("api/config", NO_STORE);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  CFG = await resp.json();

  // A cached page from before an add-on update would keep running old code
  // (the HA app's WebView is sticky about this). Reload once to pick up the
  // new version; the session flag stops a loop if something still mismatches.
  if (JS_VERSION && CFG.version && JS_VERSION !== CFG.version) {
    let tried = null;
    try { tried = sessionStorage.getItem("dvReload"); } catch (_) {}
    if (tried !== CFG.version) {
      try { sessionStorage.setItem("dvReload", CFG.version); } catch (_) {}
      location.reload();
      return;
    }
  }

  if (!map) {                               // one-time setup, safe to re-enter
    const savedWin = lsGet("window");
    if (savedWin && WINDOW_MS[savedWin]) {
      state.window = savedWin;
      $("#window").value = savedWin;
    }
    const savedArea = lsGet("area");
    state.area = (savedArea && CFG.areas.some((a) => a.key === savedArea)) || savedArea === "all"
      ? savedArea : CFG.default_area;

    const savedHere = lsGet("here");
    if (savedHere) { try { state.here = JSON.parse(savedHere); } catch (_) {} }

    const tbox = $("#threatsBox");
    if (tbox && lsGet("threatsOpen") === "0") tbox.open = false;
    const cbox = $("#chanBox");
    if (cbox) {
      const saved = lsGet(isMobile() ? "chanOpen:m" : "chanOpen:d");
      cbox.open = saved === null ? !isMobile() : saved === "1";
    }
    const obox = $("#optsBox");
    if (obox && lsGet("optsOpen") === "1") obox.open = true;

    applyI18n();
    buildAreas();
    buildThreatChips();
    buildChannelChips();

    const a = CFG.areas.find((x) => x.key === state.area) || CFG.areas[0];
    const center = a.center ||
      (a.bbox ? [(a.bbox[0] + a.bbox[2]) / 2, (a.bbox[1] + a.bbox[3]) / 2] : [50.45, 30.52]);

    applyLayout();
    // canvas, not one SVG node per marker/track/dot: every poll (and every
    // history-slider step) rebuilds all layers, which was slow on phones
    map = L.map("map", { zoomControl: true, preferCanvas: true })
      .setView(center, a.radius_km && a.radius_km < 150 ? 8 : 6);
    // the tile credits are required; Leaflet's own "🇺🇦 Leaflet |" is not, and
    // made the line wrap on a phone
    map.attributionControl.setPrefix(false);
    const home = savedView();
    if (home) map.setView([home.lat, home.lon], home.z, { animate: false });
    addViewControl();
    layer.addTo(map);
    fxLayer.addTo(map);
    map.on("move zoom resize", updateEdgeMarks);
    applyMapTheme(loadMapTheme());
    drawHere();
    applyLayout();                    // again, now that map exists (invalidateSize)
    wire();
  }

  await refresh();
  startTimer();
}

function buildAreas() {
  const sel = $("#area");
  sel.innerHTML = "";
  for (const a of CFG.areas) {
    const o = document.createElement("option");
    o.value = a.key; o.textContent = a.label;
    if (a.key === state.area) o.selected = true;
    sel.appendChild(o);
  }
  const all = document.createElement("option");
  all.value = "all"; all.textContent = lang === "uk" ? "Уся Україна" : "Everything";
  sel.appendChild(all);
}

// threat chips, grouped by family (drone / cruise / ballistic / bomb / …)
function buildThreatChips() {
  const box = $("#threats");
  box.innerHTML = "";
  const byFam = {};
  for (const th of CFG.threats) (byFam[th.family] ||= []).push(th);

  for (const [fam, list] of Object.entries(byFam)) {
    const g = document.createElement("div");
    g.className = "chip-group";
    const lbl = document.createElement("button");
    lbl.className = "chip-group-label";
    lbl.type = "button";
    lbl.textContent = fam;
    // one click on the family label toggles every slug in it
    lbl.addEventListener("click", () => {
      const slugs = list.map((x) => x.slug);
      const anyOn = slugs.some((s) => !state.threatsOff.has(s));
      slugs.forEach((s) => anyOn ? state.threatsOff.add(s) : state.threatsOff.delete(s));
      buildThreatChips();
      loadClusters();
    });
    g.appendChild(lbl);

    for (const th of list) {
      const el = document.createElement("label");
      el.className = "chip" + (state.threatsOff.has(th.slug) ? " off" : "");
      el.innerHTML = `<span class="dot" style="background:${th.color}"></span>${esc(th.short || th.label)}`;
      el.title = th.label;
      el.addEventListener("click", () => {
        state.threatsOff.has(th.slug)
          ? state.threatsOff.delete(th.slug) : state.threatsOff.add(th.slug);
        el.classList.toggle("off");
        loadClusters();
      });
      g.appendChild(el);
    }
    box.appendChild(g);
  }
}

function buildChannelChips() {
  const box = $("#channels");
  box.innerHTML = "";
  for (const ch of CFG.channels) {
    const el = document.createElement("label");
    el.className = "chip" + (state.channelsOff.has(ch) ? " off" : "");
    el.textContent = ch;
    el.addEventListener("click", () => {
      state.channelsOff.has(ch) ? state.channelsOff.delete(ch) : state.channelsOff.add(ch);
      el.classList.toggle("off");
      updateChanSummary();
      loadMessages();
      renderClusters();
    });
    box.appendChild(el);
  }
  updateChanSummary();
}

// The channel chips fold away (by default on a phone, where they took two
// rows of the feed). The summary still says when some channels are hidden,
// so a folded filter is never invisible.
function updateChanSummary() {
  const s = $("#chanSummary");
  if (!s) return;
  const total = CFG.channels.length;
  const on = total - CFG.channels.filter((c) => state.channelsOff.has(c)).length;
  s.textContent = `${t("sources")} (${on === total ? total : on + "/" + total})`;
}

function closeDrawer() { document.body.classList.remove("filters-open"); }

function wire() {
  $("#filtersToggle").addEventListener("click", () => {
    document.body.classList.toggle("filters-open");
  });
  $("#sheetHandle").addEventListener("click", toggleSheet);
  wireSheetDrag();

  $("#area").addEventListener("change", (e) => {
    state.area = e.target.value;
    lsSet("area", state.area);
    const a = CFG.areas.find((x) => x.key === state.area);
    if (a && a.center) map.setView(a.center, a.radius_km && a.radius_km < 150 ? 8 : 6);
    else if (a && a.bbox) map.fitBounds([[a.bbox[0], a.bbox[1]], [a.bbox[2], a.bbox[3]]]);
    closeDrawer();
    loadClusters();
  });
  $("#window").addEventListener("change", (e) => {
    state.window = e.target.value;
    lsSet("window", state.window);
    closeDrawer();
    refresh();
  });
  // dragging the slider fires "input" far more often than the screen redraws;
  // rebuild the map at most once per frame
  let scrubRaf = 0;
  $("#scrub").addEventListener("input", (e) => {
    state.asOf = e.target.value / 100;
    $("#scrubLabel").textContent = state.asOf >= 0.999 ? t("now") : fmtClock(asOfDate());
    if (!scrubRaf) scrubRaf = requestAnimationFrame(() => { scrubRaf = 0; renderClusters(); });
  });
  $("#live").addEventListener("change", (e) => { state.live = e.target.checked; startTimer(); });
  $("#ingestBtn").addEventListener("click", async () => {
    const btn = $("#ingestBtn");
    if (btn.disabled) return;                  // no double-submit
    btn.disabled = true;
    setStatus("fetching…");
    let added = null;
    try {
      // the server now waits for an in-flight background poll instead of
      // skipping, so this returns only once the new posts are really stored
      const r = await fetch("api/ingest", { method: "POST", cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        added = Object.values(d.ingested || {}).reduce((a, b) => a + b, 0);
      }
    } catch (_) {}
    await refresh();
    startTimer();                              // next auto-poll a full interval from now
    btn.disabled = false;
    if (added !== null) {
      setStatus(`${clusters.length} ${t("tracks")} · ` +
        (added ? `+${added} ${t("newPosts")}` : t("noNewPosts")));
    }
  });
  $("#theme").addEventListener("click", () => {
    applyMapTheme(state.mapTheme === "dark" ? "light" : "dark");
  });
  $("#layout").addEventListener("click", cycleLayout);
  $("#threatsBox")?.addEventListener("toggle", (e) => {
    lsSet("threatsOpen", e.target.open ? "1" : "0");
  });
  $("#chanBox")?.addEventListener("toggle", (e) => {
    lsSet(isMobile() ? "chanOpen:m" : "chanOpen:d", e.target.open ? "1" : "0");
  });
  $("#optsBox")?.addEventListener("toggle", (e) => {
    lsSet("optsOpen", e.target.open ? "1" : "0");
  });
  $("#lang").addEventListener("click", () => {
    lang = lang === "uk" ? "en" : "uk";
    lsSet("lang", lang);
    applyI18n(); buildAreas();
    renderClusters(); renderMessages(lastMsgs); updateFreshness();
  });
  $("#sound").addEventListener("click", () => {
    const on = lsGet("sound") !== "off";
    lsSet("sound", on ? "off" : "on");
    $("#sound").textContent = on ? "🔇" : "🔔";
    if (!on) beep();                       // confirm un-mute audibly
  });
  $("#sound").textContent = lsGet("sound") === "off" ? "🔇" : "🔔";
  $("#geo").addEventListener("click", locateMe);
  $("#dangerPill").addEventListener("click", () => {
    const id = +$("#dangerPill").dataset.id;
    const m = markerById.get(id);
    const c = clusters.find((x) => x.id === id);
    if (!m || !c) return;
    if (isMobile()) closeSheet();
    state.pinned = id;
    map.flyTo([c.lat, c.lon], Math.max(map.getZoom(), 9), { duration: 0.5 });
    m.openPopup();
    markPinned(c);
  });

  let deb;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = e.target.value.trim(); loadMessages(); }, 300);
  });
  // search is folded behind 🔍; closing it clears the filter, so a hidden
  // search can never be silently filtering the feed
  $("#searchBtn").addEventListener("click", () => {
    const s = $("#search");
    s.hidden = !s.hidden;
    if (!s.hidden) { s.focus(); }
    else if (s.value) { s.value = ""; state.q = ""; loadMessages(); }
    updateFeedTools();
  });
  $("#onMapBtn").addEventListener("click", () => {
    lsSet("onMapOnly", onMapOnly() ? "0" : "1");
    updateFeedTools();
    renderMessages(lastMsgs);
  });
  $("#autoTop").addEventListener("click", () => {
    const on = !autoTopOn();
    lsSet("autoTop", on ? "1" : "0");
    if (on) $("#msgs").scrollTop = 0;
    updateFeedTools();
  });
  updateFeedTools();

  // one listener for the whole feed (rows are rebuilt on every poll)
  $("#msgs").addEventListener("click", (ev) => {
    if (ev.target.closest(".meta a")) return;           // opens the Telegram post
    const li = ev.target.closest(".msg.locatable");
    const m = li && lastMsgs.find((x) => x.url === li.dataset.url);
    if (m) showMessageOnMap(m);
  });

  map.on("popupclose", () => {
    if (rendering) return;          // renderClusters rebuilding layers, not the user
    if (state.pinned) { state.pinned = null; markPinned(); }
  });

  // HA companion app / a background tab: timers are throttled or frozen while
  // hidden, so coming back used to show the map as it was when you left
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !state.live || !CFG) return;
    if (Date.now() - state.lastUpdate > 15000) { refresh(); startTimer(); }
  });

  // tapping the map closes a raised feed sheet, so it's never a dead end
  map.on("click", closeSheet);
  window.addEventListener("scroll", unscrollPage);
  document.body.addEventListener("scroll", unscrollPage);
  $("#main").addEventListener("scroll", unscrollPage);
}

function startTimer() {
  // keep the "updated Ns ago" pill honest between polls - installed once, and
  // left running when Live is paused so the pill can still go stale/red
  if (!freshBoot) { setInterval(updateFreshness, 5000); freshBoot = true; }

  if (timer) clearInterval(timer);
  if (!state.live) { setStatus("paused"); return; }
  // the server's current pace: fast while a threat is active, slow when quiet
  const every = Math.max(5, pollS || CFG.poll_interval || 60) * 1000;
  timer = setInterval(() => { refresh(); updateFreshness(); }, every);
}
let pollS = 0;                  // the server's poll interval right now (api/clusters)

// ---------------------------------------------------------------- my location
function locateMe() {
  if (!navigator.geolocation) return;
  const b = $("#geo");
  b.classList.add("busy"); b.title = t("locating");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.here = { lat: +pos.coords.latitude.toFixed(4), lon: +pos.coords.longitude.toFixed(4) };
      lsSet("here", JSON.stringify(state.here));
      b.classList.remove("busy"); b.classList.add("on");
      drawHere();
      map.flyTo([state.here.lat, state.here.lon], Math.max(map.getZoom(), 9), { duration: 0.6 });
      loadClusters();
    },
    () => { b.classList.remove("busy"); b.title = t("myloc"); },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 6e5 }
  );
}

function drawHere() {
  if (hereMarker) { map.removeLayer(hereMarker); hereMarker = null; }
  // your own 📍 if you set one, else 🏠 your Home Assistant home - the point
  // danger / ETA are measured from either way
  const home = !state.here && CFG.home ? { lat: CFG.home[0], lon: CFG.home[1] } : null;
  const pt = state.here || home;
  if (!pt || !herePinOn()) return;
  hereMarker = L.marker([pt.lat, pt.lon], {
    icon: L.divIcon({ className: "here-pin", html: home ? "🏠" : "📍", iconSize: [24, 24], iconAnchor: [12, 22] }),
    title: t(home ? "homePin" : "locpin"), interactive: false, keyboard: false,
  }).addTo(map);
}

// ---------------------------------------------------------------- sound
function beep() {
  if (lsGet("sound") === "off") return;
  try {
    audioCtx ||= new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "sine"; o.frequency.value = 880;
    g.gain.value = 0.0001;
    o.connect(g); g.connect(audioCtx.destination);
    const n = audioCtx.currentTime;
    g.gain.exponentialRampToValueAtTime(0.15, n + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, n + 0.35);
    o.start(n); o.stop(n + 0.36);
  } catch (_) {}
}

// ---------------------------------------------------------------- data
async function refresh() {
  await Promise.all([loadClusters(), loadMessages(), loadAviation()]);
}

// ✈ МіГ-31К · 12 хв: missile carriers in the air, counting from takeoff;
// red once inside the type's usual launch window (see aviation.py)
let aviation = [];
async function loadAviation() {
  try {
    const r = await fetch("api/aviation", NO_STORE);
    if (!r.ok) return;
    aviation = (await r.json()).alerts || [];
  } catch (_) { return; }
  renderAviation();
}

function renderAviation() {
  const box = $("#aviation");
  if (!box) return;
  const now = Date.now();
  box.innerHTML = aviation.map((a) => {
    const mins = Math.max(0, (now - new Date(a.since).getTime()) / 60000);
    const [lo, hi] = a.launch_window_min;
    const dur = mins < 60 ? `${Math.floor(mins)} ${t("minU")}`
      : `${Math.floor(mins / 60)}:${String(Math.floor(mins % 60)).padStart(2, "0")}`;
    const tip = `${t("aviaSince")} ${fmtClock(new Date(a.since))} · ${t("aviaWindow")} `
      + `${lo}–${hi} ${t("minU")} · ${a.channels.join(", ")}`;
    return `<a class="avia-chip${mins >= lo && mins <= hi ? " window" : ""}" href="${esc(a.url)}"`
      + ` target="_blank" rel="noopener" title="${esc(tip)}">✈ ${esc(a.label)} · ${dur}</a>`;
  }).join("");
}
setInterval(renderAviation, 20000);

async function loadClusters() {
  const p = new URLSearchParams();
  p.set("area", state.area);
  p.set("since", "-" + state.window);
  const on = CFG.threats.map((th) => th.slug).filter((s) => !state.threatsOff.has(s));
  if (on.length && on.length !== CFG.threats.length) p.set("threats", on.join(","));
  // danger (course, ETA) is assessed against "my location" when set, else
  // the area centre. It only goes to this add-on's own server.
  if (state.here) p.set("ref", `${state.here.lat},${state.here.lon}`);
  const seq = ++clustersSeq;
  try {
    const resp = await fetch("api/clusters?" + p, NO_STORE);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    if (seq !== clustersSeq) return;         // a newer request already drew
    if (data.poll_s && data.poll_s !== pollS) {   // the server sped up / slowed down
      pollS = data.poll_s;
      if (state.live) startTimer();
    }
    const prev = clusters;
    clusters = data.clusters || [];
    dangerRef = data.ref || null;
    detectNew(prev);
    state.lastUpdate = Date.now();
    renderClusters();
    updateFreshness();
    setStatus(`${clusters.length} ${t("tracks")} · ${t("updated")} ${fmtClock(new Date())}`);
  } catch (e) {
    // almost always: the local server isn't running / restarting
    const why = /Failed to fetch|NetworkError|Load failed/.test(String(e))
      ? "server offline?" : String(e.message || e);
    setStatus("cluster load failed (" + why + ") — retrying");
  }
}

async function loadMessages() {
  const p = new URLSearchParams();
  const on = CFG.channels.filter((c) => !state.channelsOff.has(c));
  if (on.length && on.length !== CFG.channels.length) p.set("channels", on.join(","));
  // the stream stays readable even at a 5-minute map window: never less than
  // 6 hours (0.9.19 made it follow the window, which left it empty)
  p.set("since", "-" + (windowMs() < 6 * 36e5 ? "6h" : state.window));
  if (state.q) p.set("q", state.q);
  p.set("limit", "250");
  const seq = ++msgsSeq;
  try {
    const data = await (await fetch("api/messages?" + p, NO_STORE)).json();
    if (seq !== msgsSeq) return;             // superseded by a newer request
    lastMsgs = data.messages || [];
    renderMessages(lastMsgs);
  } catch (e) { /* keep old list */ }
}

// new clusters since the previous poll -> pulse on the map + a soft beep
// ... and when a track turns "high" danger (on course for you, minutes out)
function detectNew(prev) {
  const isHigh = (c) => c.danger && c.danger.level === "high";
  if (seenIds === null) {                 // first load: prime, don't alert
    seenIds = new Set(clusters.map((c) => c.id));
    highIds = new Set(clusters.filter(isHigh).map((c) => c.id));
    return;
  }
  const fresh = clusters.filter((c) => !seenIds.has(c.id) && c.lat != null);
  const escalated = clusters.filter((c) => isHigh(c) && !highIds.has(c.id) && c.lat != null);
  for (const c of clusters) seenIds.add(c.id);
  for (const c of escalated) highIds.add(c.id);
  if ((!fresh.length && !escalated.length) || !state.live || state.asOf < 0.999) return;

  let flashed = false;
  for (const c of new Set([...fresh, ...escalated])) {
    pulse(c.lat, c.lon, isHigh(c) ? "#ff3b30" : c.color);
    flashed = true;
  }
  if (flashed) {
    beep();
    document.body.classList.add("flash");
    setTimeout(() => document.body.classList.remove("flash"), 900);
  }
}

function pulse(lat, lon, color) {
  const ring = L.circleMarker([lat, lon], {
    radius: 8, color, weight: 3, opacity: 0.9, fill: false,
  }).addTo(fxLayer);
  let r = 8, op = 0.9;
  const iv = setInterval(() => {
    r += 4; op -= 0.09;
    if (op <= 0) { clearInterval(iv); fxLayer.removeLayer(ring); return; }
    ring.setRadius(r); ring.setStyle({ opacity: op });
  }, 60);
}

// ---------------------------------------------------------------- render: map
function renderClusters() {
  // Rebuilding every layer closes whatever popup is open, which used to both
  // make it vanish on each poll and clear the pin. Remember it, reopen below.
  const reopenId = state.pinned;
  rendering = true;
  layer.clearLayers();
  rendering = false;
  shownIds.clear();
  candidateTargets.clear();
  labelSlots.clear();
  // At "now" the server's window is the only filter. Comparing post times to
  // this device's clock hid brand-new markers whenever the clock ran even a
  // few seconds behind real time - they only showed up after reloading the
  // page later (e.g. switching HA panels back and forth).
  const live = state.asOf >= 0.999;
  const cutoff = live ? Date.now() : asOfDate().getTime();
  let reopenMarker = null;
  markerById.clear();
  const winMin = windowMs() / 60000;
  const offCh = state.channelsOff;

  for (const c of clusters) {
    if (c.lat == null) continue;
    if (!live && new Date(c.last_posted_at).getTime() > cutoff) continue;
    if (offCh.size && c.channels.every((ch) => offCh.has(ch))) continue;
    // a threat's life (server, predict.py): a track nobody has reported for
    // a while has moved on or gone down - its last spot is a ghost, not drawn
    const life = c.life == null ? 1 : c.life;
    if (live && life <= 0) continue;
    shownIds.add(c.id);

    const age = Math.max(0, (cutoff - new Date(c.last_posted_at).getTime()) / 60000);
    let op = live ? 0.3 + 0.7 * life : Math.max(0.25, 1 - age / winMin);
    // dim uncertain parses: full opacity at conf>=0.8, ~0.45x at conf 0
    const conf = clusterConfidence(c);
    op *= 0.45 + 0.55 * Math.min(1, conf / 0.8);
    // size by the reported group size, falling back to number of reports
    const size = Math.max(c.count || 0, c.event_count || 1);
    const r = 7 + Math.min(16, (size - 1) * 3);
    // badge shows the drone count if we have one, else how many reports
    const badge = c.count != null ? String(c.count) : (c.event_count > 1 ? String(c.event_count) : "");

    // movement guess: the path through successive reported positions
    if (Array.isArray(c.track) && c.track.length >= 2) drawTrack(c.track, c.color, op, c.track_src);

    if (c.status === "circling") {
      L.circle([c.lat, c.lon], { radius: 9000, color: c.color, weight: 1,
        opacity: 0.5 * op, fill: false, dashArray: "3 7", interactive: false }).addTo(layer);
    }
    if (c.missile) drawMissile(c, op);
    if (c.dest_lat != null) {
      L.polyline([[c.lat, c.lon], [c.dest_lat, c.dest_lon]], {
        color: c.color, weight: 2, opacity: 0.7 * op, dashArray: "5 6", interactive: false,
      }).addTo(layer);
      L.circleMarker([c.dest_lat, c.dest_lon], {
        radius: 3, color: c.color, opacity: 0.7 * op, fillOpacity: 0.7 * op, interactive: false,
      }).addTo(layer);
    }
    // where it can be in the next few minutes: a wedge that's as wide as the
    // heading is uncertain and as long as speed x horizon (see predict.py),
    // plus where it probably is right now. Only at "now" - it's a forecast.
    const mo = c.motion || {};
    if (live && mo.cone && mo.cone.length > 2) {
      L.polygon(mo.cone, {
        color: c.color, weight: 1, opacity: 0.45 * op, dashArray: "2 4",
        fillColor: c.color, fillOpacity: 0.12 * op, interactive: false,
      }).addTo(layer);
      if (c.dest_lat == null) {
        L.polyline([[c.lat, c.lon], destPoint(c.lat, c.lon, mo.heading_deg,
          (mo.speed_kmh * mo.horizon_min) / 60)], {
          color: c.color, weight: 1.5, opacity: 0.6 * op, dashArray: "5 6", interactive: false,
        }).addTo(layer);
      }
    } else if (c.dest_lat == null && c.heading_deg != null && c.status !== "circling"
               && mo.projection !== false) {     // not from a whole-city fix ("Київ")
      const to = destPoint(c.lat, c.lon, c.heading_deg, 28);
      L.polyline([[c.lat, c.lon], to], {
        color: c.color, weight: 2, opacity: 0.7 * op, dashArray: "5 6", interactive: false,
      }).addTo(layer);
    }
    if (live && mo.now) {
      L.polyline([[c.lat, c.lon], mo.now], {
        color: c.color, weight: 2, opacity: 0.8 * op, interactive: false,
      }).addTo(layer);
      L.circleMarker(mo.now, {
        radius: 5, color: c.color, weight: 2, opacity: 0.9 * op, dashArray: "2 3",
        fill: false, interactive: false,
      }).addTo(layer);
    }
    // danger to you / the area: a red ring (high) or an amber dashed one
    const lvl = live && c.danger ? c.danger.level : "low";
    if (lvl === "high" || lvl === "medium") {
      L.circleMarker([c.lat, c.lon], {
        radius: r + 6, fill: false, interactive: false,
        color: lvl === "high" ? "#ff3b30" : "#ffb703",
        weight: lvl === "high" ? 3 : 2, opacity: 0.95,
        dashArray: lvl === "high" ? null : "4 4",
      }).addTo(layer);
    }

    const m = L.circleMarker([c.lat, c.lon], {
      radius: r, color: c.color, weight: 2, opacity: op,
      fillColor: c.color, fillOpacity: 0.5 * op,
    }).bindPopup(() => popupHtml(c), { maxWidth: 320 });  // built on open, not per marker per poll
    // marker tap: show the popup + remember this cluster. On desktop the feed
    // scrolls to its message right away; on mobile the feed only rises when the
    // user taps the popup (see wirePopupToFeed) - the popup already shows the
    // message text, so raising the sheet on top of it would just be redundant.
    m.on("click", () => { state.pinned = c.id; markPinned(c); });
    m.on("popupopen", (e) => wirePopup(e.popup));
    if (c.id === reopenId) reopenMarker = m;
    markerById.set(c.id, m);
    if (badge) {
      m.bindTooltip(badge, {
        permanent: true, direction: "center", className: "count-badge",
      });
    }
    m.addTo(layer);
  }
  if (reopenMarker) {
    // reopen without panning the map under the user on every poll
    const pp = reopenMarker.getPopup();
    const autoPan = pp.options.autoPan;
    pp.options.autoPan = false;
    rendering = true;
    reopenMarker.openPopup();
    rendering = false;
    pp.options.autoPan = autoPan;
  } else if (reopenId != null) {
    state.pinned = null;                     // its cluster is gone (resolved / aged out)
  }
  markPinned(clusters.find((x) => x.id === state.pinned) || null);
  // the "only on the map" feed follows what's drawn (area, filters, history)
  if (onMapOnly()) renderMessages(lastMsgs);
  updateEdgeMarks();
  updateDangerPill(live);
}

const LEVEL_RANK = { high: 2, medium: 1, low: 0 };
const kmUnit = () => (lang === "uk" ? "км" : "km");

function refName() {
  if (!dangerRef) return "";
  if (dangerRef.kind === "here") return t("you");
  if (dangerRef.kind === "home") return t("homeRef");
  const a = CFG.areas.find((x) => x.key === state.area);
  return a ? a.label : "";
}

// the most urgent track: highest level, then soonest on-course ETA, then nearest
function worstThreat() {
  let best = null;
  for (const c of clusters) {
    const d = c.danger;
    if (!d || d.level === "low" || c.lat == null) continue;
    if (state.channelsOff.size && c.channels.every((ch) => state.channelsOff.has(ch))) continue;
    const key = [LEVEL_RANK[d.level], -(d.eta_min ?? 1e9), -d.distance_km];
    if (!best || key[0] > best.key[0] || (key[0] === best.key[0] &&
        (key[1] > best.key[1] || (key[1] === best.key[1] && key[2] > best.key[2])))) {
      best = { c, key };
    }
  }
  return best && best.c;
}

// header pill: the most urgent threat to you (or the area) at a glance;
// tapping it shows that track
function updateDangerPill(live) {
  const el = $("#dangerPill");
  if (!el) return;
  const c = live ? worstThreat() : null;
  if (!c) { el.hidden = true; el.dataset.id = ""; return; }
  const d = c.danger;
  const th = (CFG.threats || []).find((x) => x.slug === c.threat_type);
  const name = (th && th.short) || c.threat_label;
  el.textContent = d.toward && d.eta_min != null
    ? `⚠ ${name} ~${d.eta_min < 1 ? "<1" : Math.round(d.eta_min)} ${t("min")}`
    : `⚠ ${name} ${Math.round(d.distance_km)} ${kmUnit()}`;
  el.title = `${t("lvl_" + d.level)} · ${c.place_name || ""}`;
  el.className = "danger-pill " + d.level;
  el.dataset.id = c.id;
  el.hidden = false;
}

function dangerRow(c) {
  const d = c.danger;
  if (!d) return "";
  const who = esc(refName());
  const dist = `${Math.round(d.distance_km)} ${kmUnit()}`;
  let txt;
  if (d.toward && d.eta_min != null) {
    txt = `⚠ ${t("towardYou")} ${who} · ~${d.eta_min < 1 ? "<1" : Math.round(d.eta_min)} ${t("min")} · ${dist}`;
  } else if (d.receding) {
    txt = `${dist} ${t("from")} ${who} · ${t("movingAway")}`;
  } else if (d.cpa_km != null) {
    txt = `${dist} ${t("from")} ${who} · ${t("passes")} ~${Math.round(d.cpa_km)} ${kmUnit()}`;
  } else {
    txt = `${dist} ${t("from")} ${who}`;
  }
  return `<div class="pp-row pp-danger ${d.level}">${dangerRef && dangerRef.kind === "here" ? "📍 " : "◎ "}${txt}</div>`;
}

function popupHtml(c) {
  const mo = c.motion || {};
  const obs = (c.heading_observed ? " · observed" : "") +
    (mo.horizon_min ? ` · ~${mo.speed_kmh} ${t("kmh")}${mo.speed_observed ? " (" + t("observedSpeed") + ")" : ""}` : "");
  const dst = c.dest_name
    ? `<div class="pp-row">→ <b>${esc(c.dest_name)}</b>${c.compass ? " (" + c.compass + obs + ")" : ""}</div>`
    : (c.compass ? `<div class="pp-row">${t("heading")} ${c.compass}${obs}</div>` : "");
  const n = (c.sources || []).length;
  const src = (c.sources || []).map((s) =>
    `<div>${esc(s.channel)} · <a href="${esc(s.url)}" target="_blank" rel="noopener">${fmtClock(new Date(s.posted_at))}</a>${s.count ? " · " + s.count + "×" : ""}${s.line ? " — " + esc(s.line) : ""}</div>`
  ).join("");
  const size = c.count != null
    ? `<b>${c.count}</b> ${c.threat_type === "shahed" || c.threat_type === "jet_uav" ? t("drone") : t("unit")}${c.count === 1 ? "" : "s"}`
      + (c.count_max && c.count_max > c.count ? ` (${t("peak")} ${c.count_max})` : "")
    : "";
  const reports = c.event_count > 1 ? `${c.event_count} ${t("reports")}` : "";
  const conf = clusterConfidence(c);
  const confStr = conf ? ` · ${Math.round(conf * 100)}%` : "";
  // descending: said so, or its reported altitude dropped sharply
  const st = c.status === "descending" || mo.descending
    ? `<b class="pp-desc">↓ ${t("st_descending")}</b>` : esc(t("st_" + c.status));
  const trend = { down: " ↓", up: " ↑" }[mo.alt_trend] || "";
  const alt = c.altitude_m != null ? `${t("altitude")} ${fmtAlt(c.altitude_m)}${trend}` : "";
  const line2 = [size, st, alt, reports].filter(Boolean).join(" · ") + confStr;

  const hereRow = state.asOf >= 0.999 ? dangerRow(c) : "";
  return `<div class="pp-h" style="color:${c.color}">${esc(c.threat_label)}</div>
    <div class="pp-row"><b>${esc(c.place_name || "—")}</b></div>
    <div class="pp-row">${line2}</div>
    ${dst}
    ${hereRow}
    <div class="pp-row" style="color:#666">${esc((c.channels || []).join(", "))} · ${ageStr(c.age_minutes)}</div>
    ${n ? `<details class="pp-src-box"${srcOpen() ? " open" : ""}><summary>${t("sources")} (${n})</summary><div class="pp-src">${src}</div></details>` : ""}
    <div class="pp-feedhint">${t("showInFeed")}</div>`;
}

// small-distance destination point, km -> lat/lon
// The path a track flew, so you can see how it moved: a smooth curve through
// the reported places (Catmull-Rom - it passes through every fix, so the
// smoothing never moves a report), fading from faint (oldest leg) to solid
// (the latest), thin to thick. A manoeuvring jet over the city reads as a
// path instead of a tangle of equal straight lines.
function catmullRom(p0, p1, p2, p3, steps) {
  const out = [];
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps, t2 = t * t, t3 = t2 * t;
    const f = (a, b, c, d) => 0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2
      + (-a + 3 * b - 3 * c + d) * t3);
    out.push([f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1])]);
  }
  return out;
}

function drawTrack(track, color, op, src) {
  const n = track.length - 1;                     // number of legs
  for (let i = 0; i < n; i += 1) {
    const p0 = track[Math.max(0, i - 1)], p1 = track[i], p2 = track[i + 1];
    const p3 = track[Math.min(n, i + 2)];
    const k = (i + 1) / n;                        // 0..1, oldest -> newest
    const pts = [p1, ...catmullRom(p0, p1, p2, p3, 10)];
    L.polyline(pts, {
      color, weight: 1.5 + 1.8 * k, opacity: (0.12 + 0.78 * k) * op,
      lineJoin: "round", lineCap: "round", interactive: false,
    }).addTo(layer);
    // tap a leg: the two posts it joins (a wide invisible line, so it's
    // hittable with a finger; sent to the back so markers still win)
    if (src && src[i] && src[i + 1]) {
      L.polyline(pts, { weight: 16, opacity: 0, lineCap: "round" })
        .bindPopup(legHtml(src[i], src[i + 1], p1, p2), { maxWidth: 300 })
        .addTo(layer).bringToBack();
    }
  }
  // the reported places along the way, fading the same way
  track.slice(0, -1).forEach((pt, i) => {
    const k = (i + 1) / n;
    L.circleMarker(pt, {
      radius: 2.5, color, weight: 0, interactive: false,
      fillColor: color, fillOpacity: (0.15 + 0.5 * k) * op,
    }).addTo(layer);
  });
}

function kmBetween(a, b) {
  const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

// popup for one leg of a track: where it came from, which post moved it, how
// far and how fast that implies
function legHtml(a, b, pa, pb) {
  const km = kmBetween(pa, pb);
  const min = (new Date(b.posted_at) - new Date(a.posted_at)) / 60000;
  const kmh = min > 0.5 ? Math.round(km / (min / 60)) : null;
  const row = (s) => `<div>${esc(s.channel)} · <a href="${esc(s.url)}" target="_blank" rel="noopener">${fmtClock(new Date(s.posted_at))}</a>`
    + `${s.line ? " — " + esc(s.line) : ""}</div>`;
  return `<div class="pp-h">${esc(a.place || "?")} → ${esc(b.place || "?")}</div>
    <div class="pp-row">${Math.round(km)} km · ${Math.round(min)} ${t("minU")}${kmh != null ? ` · ~${kmh} ${t("kmh")}` : ""}</div>
    <div class="pp-src">${row(a)}${row(b)}</div>`;
}

// A missile's possible trajectory: launch area -> target as a gentle arc,
// dashed while it can still change, solid once final ("спуск" / impact), with
// distance and flight time *next to the target* (that's where the viewer
// looks). With no launch area named, faint arcs from the usual ones.
function arcPoints(a, b, n = 24) {
  const [la1, lo1] = a, [la2, lo2] = b;
  const dLat = la2 - la1, dLon = lo2 - lo1;
  // bulge to the left of the direction of flight, ~12% of the length
  const nx = -dLon * 0.12, ny = dLat * 0.12;
  const pts = [];
  for (let i = 0; i <= n; i += 1) {
    const t = i / n, k = 4 * t * (1 - t);
    pts.push([la1 + dLat * t + nx * k, lo1 + dLon * t + ny * k]);
  }
  return pts;
}

function missileLabel(km, min) {
  const kmU = lang === "uk" ? "км" : "km", minU = lang === "uk" ? "хв" : "min";
  const m = min < 1 ? "<1" : Math.round(min);
  return `≈${km} ${kmU} · ~${m} ${minU}`;
}

function drawMissile(c, op) {
  const m = c.missile;
  if (!m || !m.to) return;
  const to = m.to;
  const launches = m.launch ? [m.launch] : (m.candidates || []);
  if (!launches.length) return;
  const dim = m.maybe_false ? 0.45 : 1;              // quiet threat: likely false
  // "Загроза ОТРК, Брянськ" and no target named yet: a threat to the whole
  // region, not to any one point - the launch area and how soon it could
  // arrive, no line until a channel says where it's going
  if (m.to_kind === "area" && m.launch) {
    let label = `${t("noTarget")}${m.min != null ? ` · ~${Math.round(m.min)} ${t("minU")}` : ""}`;
    if (m.maybe_false) label = `${t("maybeFalse")} · ${label}`;
    L.marker([m.launch.lat, m.launch.lon], {
      interactive: false, opacity: dim,
      icon: L.divIcon({ className: "launch-site sure",
        html: `<span style="color:${c.color}">▲</span>${esc(m.launch.name)}`
          + `<br><span class="missile-label-inline" style="border-left:3px solid ${c.color}">${esc(label)}</span>`,
        iconSize: null }),
    }).addTo(layer);
    return;
  }
  // several "спуск" on one target: its candidate arcs once, not stacked
  if (!m.launch) {
    const key = to.join(",");
    if (candidateTargets.has(key)) return;
    candidateTargets.add(key);
  }
  for (const s of launches) {
    const sure = !!m.launch;
    L.polyline(arcPoints([s.lat, s.lon], to), {
      color: c.color, weight: sure ? 2.5 : 1.2, interactive: false,
      opacity: (sure ? 0.85 : 0.45) * op * dim,
      dashArray: m.final && sure ? null : (sure ? "8 6" : "3 7"),
    }).addTo(layer);
    L.marker([s.lat, s.lon], {
      interactive: false,
      icon: L.divIcon({ className: "launch-site" + (sure ? " sure" : ""),
        html: `<span style="color:${c.color}">▲</span>${esc(s.name)}`, iconSize: null }),
    }).addTo(layer);
  }
  L.circleMarker(to, { radius: 14, color: c.color, weight: 2, fill: false,
    opacity: 0.9 * op, interactive: false }).addTo(layer);
  let label;
  if (m.launch && m.km != null) {
    label = missileLabel(m.km, m.min);
  } else {
    const kms = launches.map((s) => s.km), mins = launches.map((s) => s.min);
    const lo = Math.min(...kms), hi = Math.max(...kms);
    label = missileLabel(lo === hi ? lo : `${lo}–${hi}`, Math.max(...mins));
  }
  if (m.final) label = `${t("st_" + (c.status === "impact" ? "impact" : "descending"))} · ${label}`;
  if (m.maybe_false) label = `${t("maybeFalse")} · ${label}`;
  // several missiles on one target: stack their labels instead of overlapping
  const slotKey = to.join(",");
  const slot = labelSlots.get(slotKey) || 0;
  labelSlots.set(slotKey, slot + 1);
  L.marker(to, {
    interactive: false,
    icon: L.divIcon({ className: "missile-label", iconSize: null,
      html: `<span style="position:relative;top:${slot * 24}px;border-left:3px solid ${c.color}">${esc(label)}</span>` }),
  }).addTo(layer);
}

// ⌂ back to the default view; 📌 make the current position + zoom the default
// (remembered, and where the map opens). Without one: the area's own view.
function savedView() {
  try {
    const v = JSON.parse(lsGet("homeView") || "null");
    return v && Number.isFinite(v.lat) && Number.isFinite(v.lon) && Number.isFinite(v.z) ? v : null;
  } catch (_) { return null; }
}

function areaView() {
  const a = CFG.areas.find((x) => x.key === state.area) || CFG.areas[0];
  if (a.bbox) return { bounds: [[a.bbox[0], a.bbox[1]], [a.bbox[2], a.bbox[3]]] };
  return { center: a.center || [50.45, 30.52], z: a.radius_km && a.radius_km < 150 ? 8 : 6 };
}

function resetView() {
  const v = savedView();
  if (v) { map.setView([v.lat, v.lon], v.z, { animate: true, duration: 0.25 }); return; }
  const a = areaView();
  if (a.bounds) map.fitBounds(a.bounds, { animate: true, duration: 0.25 });
  else map.setView(a.center, a.z, { animate: true, duration: 0.25 });
}

function addViewControl() {
  const Ctl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const box = L.DomUtil.create("div", "leaflet-bar view-ctl");
      box.innerHTML = `<a href="#" role="button" data-act="reset" title="${esc(t("resetView"))}">⌂</a>`
        + `<a href="#" role="button" data-act="save" title="${esc(t("saveView"))}">📌</a>`
        + `<a href="#" role="button" data-act="edges" title="${esc(t("edgeMarks"))}"`
        + ` class="${edgeMarksOn() ? "on" : ""}">🧭</a>`
        + `<a href="#" role="button" data-act="pin" title="${esc(t("pinToggle"))}"`
        + ` class="${herePinOn() ? "on" : ""}">🏠</a>`;
      L.DomEvent.disableClickPropagation(box);
      box.addEventListener("click", (ev) => {
        const btn = ev.target.closest("a[data-act]");
        if (!btn) return;
        ev.preventDefault();
        if (btn.dataset.act === "reset") { resetView(); return; }
        if (btn.dataset.act === "pin") {
          lsSet("herePin", herePinOn() ? "0" : "1");
          btn.classList.toggle("on", herePinOn());
          drawHere();
          return;
        }
        if (btn.dataset.act === "edges") {
          lsSet("edgeMarks", edgeMarksOn() ? "0" : "1");
          btn.classList.toggle("on", edgeMarksOn());
          drawEdgeMarks();
          return;
        }
        const c = map.getCenter();
        lsSet("homeView", JSON.stringify({ lat: +c.lat.toFixed(4), lon: +c.lng.toFixed(4), z: map.getZoom() }));
        btn.classList.add("saved");
        setStatus(t("viewSaved"));
        setTimeout(() => btn.classList.remove("saved"), 1200);
      });
      return box;
    },
  });
  map.addControl(new Ctl());
}

// Tracks outside the current view get an arrow on the map's edge, on the line
// from the view's centre toward them, so a zoomed-in map never looks empty
// while something is just off-screen. Close arrows merge (with a count);
// tapping one flies to that track.
let edgeRaf = 0;
function updateEdgeMarks() {
  if (edgeRaf) return;
  edgeRaf = requestAnimationFrame(() => { edgeRaf = 0; drawEdgeMarks(); });
}

function edgeMarksOn() { return lsGet("edgeMarks") !== "0"; }
function herePinOn() { return lsGet("herePin") !== "0"; }

function drawEdgeMarks() {
  const box = $("#edgeMarks");
  if (!box || !map) return;
  box.innerHTML = "";
  if (!edgeMarksOn()) return;
  const size = map.getSize(), W = size.x, H = size.y;
  if (!W || !H) return;
  const pad = 18, cx = W / 2, cy = H / 2;
  const marks = [];
  // each track's position; a missile also has both ends of its trajectory -
  // where it starts (launch area, hollow) and where it ends (target, ringed)
  const points = [];
  for (const c of clusters) {
    if (!shownIds.has(c.id) || c.lat == null) continue;
    points.push({ c, lat: c.lat, lon: c.lon, kind: "pos" });
    const m = c.missile;
    // the drawn trajectory (launch -> target arc), so its ends' marks sit
    // where that line leaves the screen
    const arc = m && m.launch && m.to && m.to_kind !== "area"
      ? arcPoints([m.launch.lat, m.launch.lon], m.to) : null;
    if (m && m.launch) {
      points.push({ c, lat: m.launch.lat, lon: m.launch.lon, kind: "start",
        line: arc && arc.slice().reverse() });
    }
    if (m && m.to && m.to_kind !== "area" && (m.to[0] !== c.lat || m.to[1] !== c.lon)) {
      points.push({ c, lat: m.to[0], lon: m.to[1], kind: "end", line: arc });
    }
  }
  const inside = (q) => q.x >= pad && q.x <= W - pad && q.y >= pad && q.y <= H - pad;
  // where a line (ordered toward the off-screen point) leaves the padded view
  function exitOf(line) {
    const px = line.map((ll) => map.latLngToContainerPoint(ll));
    for (let i = 1; i < px.length; i += 1) {
      const a = px[i - 1], b = px[i];
      if (!inside(a) || inside(b)) continue;
      // clip a -> b to the rectangle: the largest t that keeps it inside
      let tt = 1;
      const dx = b.x - a.x, dy = b.y - a.y;
      if (dx > 0) tt = Math.min(tt, (W - pad - a.x) / dx);
      if (dx < 0) tt = Math.min(tt, (pad - a.x) / dx);
      if (dy > 0) tt = Math.min(tt, (H - pad - a.y) / dy);
      if (dy < 0) tt = Math.min(tt, (pad - a.y) / dy);
      return { x: a.x + dx * tt, y: a.y + dy * tt, ang: Math.atan2(dy, dx) * 180 / Math.PI + 90 };
    }
    return null;                                   // no visible part: use the ray
  }
  for (const pt of points) {
    const c = pt.c;
    const p = map.latLngToContainerPoint([pt.lat, pt.lon]);
    if (p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H) continue;
    const dx = p.x - cx, dy = p.y - cy;
    const s = Math.min((cx - pad) / Math.abs(dx || 1e-9), (cy - pad) / Math.abs(dy || 1e-9));
    let x = cx + dx * s, y = cy + dy * s, ang = Math.atan2(dy, dx) * 180 / Math.PI + 90;
    const ex = pt.line && exitOf(pt.line);
    if (ex) ({ x, y, ang } = ex);
    const near = marks.find((m) => m.kind === pt.kind && Math.hypot(m.x - x, m.y - y) < 26);
    if (near) { near.items.push(c); near.pts.push(pt); continue; }
    marks.push({ x, y, kind: pt.kind, ang, items: [c], pts: [pt] });
  }
  const frag = document.createDocumentFragment();
  for (const m of marks.slice(0, 40)) {
    const c = m.items[0];
    const el = document.createElement("div");
    el.className = `edge-mark ${m.kind}`;
    el.style.left = `${m.x}px`;
    el.style.top = `${m.y}px`;
    el.style.background = m.kind === "start" ? "rgba(16,20,27,.85)" : c.color;
    el.style.borderColor = m.kind === "start" ? c.color : "";
    const what = { start: t("edgeStart"), end: t("edgeEnd"), pos: "" }[m.kind];
    el.title = (what ? what + ": " : "") + m.pts.slice(0, 6).map((p) => (p.kind === "start" ? p.c.missile.launch.name
      : p.kind === "end" ? (p.c.missile.to_name || t("edgeEnd")) : `${p.c.threat_label} · ${p.c.place_name || ""}`)).join(" | ");
    el.innerHTML = `<span class="arr" style="transform: rotate(${m.ang}deg)"></span>${m.items.length > 1 ? m.items.length : ""}`;
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (m.pts.length > 1) {
        map.fitBounds(L.latLngBounds(m.pts.map((p) => [p.lat, p.lon])).pad(0.3),
          { animate: true, duration: 0.25 });
      } else {
        map.setView([m.pts[0].lat, m.pts[0].lon], Math.max(map.getZoom(), m.kind === "start" ? 6 : 9),
          { animate: true, duration: 0.25 });
      }
    });
    frag.appendChild(el);
  }
  box.appendChild(frag);
}

function destPoint(lat, lon, bearing, km) {
  const R = 6371, d = km / R, br = (bearing * Math.PI) / 180;
  const la1 = (lat * Math.PI) / 180, lo1 = (lon * Math.PI) / 180;
  const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(br));
  const lo2 = lo1 + Math.atan2(Math.sin(br) * Math.sin(d) * Math.cos(la1),
    Math.cos(d) - Math.sin(la1) * Math.sin(la2));
  return [(la2 * 180) / Math.PI, (lo2 * 180) / Math.PI];
}

// fly the map to a message's first located event and show nearby markers
function showMessageOnMap(m) {
  const loc = (m.events || []).find((e) => e.lat != null && e.lon != null);
  if (!loc) return;
  if (isMobile()) closeSheet();
  map.flyTo([loc.lat, loc.lon], Math.max(map.getZoom(), 10), { duration: 0.6 });
}

// highlight the feed rows that belong to a clicked map marker. On desktop the
// feed is always visible, so also scroll it to that message; on mobile it stays
// where it is until the user taps the popup.
function markPinned(c) {
  const urls = c ? new Set((c.sources || []).map((s) => s.url)) : null;
  $$("#msgs .msg").forEach((li) => {
    li.classList.toggle("pinned", !!urls && urls.has(li.dataset.url));
  });
  if (c && !isMobile()) scrollToPinned();
}

// Scroll only the feed list. scrollIntoView() also scrolls every ancestor -
// on iOS (HA app) that includes the page itself despite overflow:hidden, which
// slid the whole app up under HA's header with no way to close the sheet.
function scrollToPinned() {
  const first = $("#msgs .msg.pinned");
  const ul = $("#msgs");
  if (!first || !ul) return;
  const top = ul.scrollTop + first.getBoundingClientRect().top - ul.getBoundingClientRect().top - 8;
  ul.scrollTop = Math.max(0, top);           // instant: the sheet's slide is the motion
}

// The app never scrolls as a page - if anything (focus, a browser quirk)
// shifts it, put it back so the header and sheet handle stay reachable.
function unscrollPage() {
  for (const el of [document.scrollingElement, document.body, $("#main")]) {
    if (el && el.scrollTop) el.scrollTop = 0;
  }
}

// The popup's sources list is foldable: open by default on desktop, folded on
// a phone where it used to take most of the screen. The last choice is
// remembered per layout.
function srcKey() { return isMobile() ? "srcOpen:m" : "srcOpen:d"; }
function srcOpen() {
  const v = lsGet(srcKey());
  return v === null ? !isMobile() : v === "1";
}

// Wire a marker popup once per popup instance (its content node is stable,
// the lazy content inside it is rebuilt on every open).
// Remember the Sources fold from a user *tap* on its summary - never from the
// "toggle" event: inserting a <details open> fires "toggle" too, and redrawing
// the popup from there re-rendered it forever and froze phones (0.9.12).
// Nothing here redraws the popup; the wrapper grows with its content.
function wirePopup(popup) {
  const el = popup.getElement() &&
    popup.getElement().querySelector(".leaflet-popup-content");
  if (!el || el.dataset.wired) return;
  el.dataset.wired = "1";
  el.addEventListener("click", (ev) => {
    const sum = ev.target.closest(".pp-src-box > summary");
    if (!sum) return;
    // the click runs before the browser flips `open`, so the new state is !open
    lsSet(srcKey(), sum.parentElement.open ? "0" : "1");
  });
  if (!isMobile()) return;
  // mobile: tapping the popup body raises the feed sheet to its message
  el.classList.add("pp-tap");
  el.addEventListener("click", (ev) => {
    // links open Telegram; the sources fold is its own control
    if (ev.target.closest("a, .pp-src-box")) return;
    openSheet();
    scrollToPinned();
  });
}

// ---------------------------------------------------------------- render: stream
// 🗺️ on: the feed lists only posts behind a mark that's on the map right now
// (siren on/off, news, placeless posts, other areas are hidden)
function onMapOnly() { return lsGet("onMapOnly") === "1"; }

function renderMessages(allMsgs) {
  const ul = $("#msgs");
  const msgs = onMapOnly()
    ? allMsgs.filter((m) => (m.events || []).some((e) => shownIds.has(e.cluster_id)))
    : allMsgs;
  const hc = $("#hiddenCount");
  if (hc) hc.textContent = msgs.length < allMsgs.length ? `${t("hidden")} ${allMsgs.length - msgs.length}` : "";
  // Rebuilding the list resets its scroll, which threw the reader (and a
  // "show in feed" jump) back to the top on every poll. Keep the message at
  // the top of the view where it is; at the very top, stay there for new posts.
  // ⤒ auto-scroll wins - except right after "show in feed" from a marker
  // (state.pinned), which the next poll must not undo
  const toTop = autoTopOn() && !state.pinned;
  let anchor = null;
  if (!toTop && ul.scrollTop > 0) {
    const top = ul.getBoundingClientRect().top;
    const li = $$("#msgs .msg").find((x) => x.getBoundingClientRect().bottom > top);
    if (li) anchor = { url: li.dataset.url, off: li.getBoundingClientRect().top - top };
  }
  ul.innerHTML = "";
  const frag = document.createDocumentFragment();       // one insert, one layout
  const now = Date.now();
  // 🗺️ on and nothing on the map: say so, instead of a blank feed that looks
  // like the log was wiped
  if (!msgs.length && allMsgs.length) {
    const li = document.createElement("li");
    li.className = "feed-empty";
    li.textContent = t("onMapEmpty").replace("{n}", allMsgs.length);
    li.addEventListener("click", () => $("#onMapBtn").click());
    frag.appendChild(li);
  }
  for (const m of msgs) {
    const li = document.createElement("li");
    li.className = "msg";
    li.dataset.url = m.url;
    const ageMin = (now - new Date(m.posted_at).getTime()) / 60000;
    if (ageMin < 10) li.classList.add("hot");
    const located = (m.events || []).some((e) => e.lat != null);
    if (located) li.classList.add("locatable");
    const tags = (m.events || []).map((e) => {
      const bits = [e.count ? e.count + "×" : "", e.place_name || "", e.dest_name ? "→ " + e.dest_name : "", e.heading || "",
        e.status === "descending" ? "↓" : "", e.altitude_m != null ? fmtAlt(e.altitude_m) : ""]
        .filter(Boolean).join(" ");
      return `<span class="tag"><span class="dot" style="background:${e.color}"></span>${esc(e.threat_type)}${bits ? " · " + esc(bits) : ""}</span>`;
    }).join("");
    li.innerHTML = `
      <div class="meta">
        <span>${esc(m.channel)}</span>
        <a href="${esc(m.url)}" target="_blank" rel="noopener">${fmtClock(new Date(m.posted_at))} · ${ageStr(ageMin)}</a>
      </div>
      <div class="body">${esc(m.text)}</div>
      ${tags ? `<div class="tags">${tags}</div>` : ""}`;
    frag.appendChild(li);
  }
  ul.appendChild(frag);
  if (toTop) {
    ul.scrollTop = 0;
  } else if (anchor) {
    const li = $$("#msgs .msg").find((x) => x.dataset.url === anchor.url);
    if (li) ul.scrollTop += li.getBoundingClientRect().top - ul.getBoundingClientRect().top - anchor.off;
  }

  drawTimeline(allMsgs);
  if (state.pinned) {
    const c = clusters.find((x) => x.id === state.pinned);
    markPinned(c);
  }

  // bottom-sheet peek summary
  const hot = allMsgs.filter((m) => (now - new Date(m.posted_at).getTime()) / 60000 < 15).length;
  const latest = allMsgs[0];
  const sum = $("#sheetSummary");
  if (sum) {
    sum.textContent = latest
      ? `${allMsgs.length} ${t("inFeed")}${hot ? " · " + hot + " " + t("new") : ""} — ${latest.channel}: ${latest.text.slice(0, 46)}`
      : t("feed");
  }
}

// tiny activity sparkline: message volume across the feed window
function drawTimeline(msgs) {
  const el = $("#timeline");
  if (!el) return;
  const N = 40;
  const now = Date.now();
  const span = Math.max(windowMs(), 6 * 36e5);
  const buckets = new Array(N).fill(0);
  for (const m of msgs) {
    const age = now - new Date(m.posted_at).getTime();
    if (age < 0 || age > span) continue;
    const i = Math.min(N - 1, Math.floor(((span - age) / span) * N));
    buckets[i]++;
  }
  const max = Math.max(1, ...buckets);
  const W = 240, H = 30, bw = W / N;
  const bars = buckets.map((v, i) => {
    const h = Math.max(v ? 2 : 0, (v / max) * H);
    return `<rect x="${(i * bw).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${(bw - 1).toFixed(1)}" height="${h.toFixed(1)}"/>`;
  }).join("");
  el.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
}

function setStatus(s) { $("#status").textContent = s; }

// prominent "how fresh is this" pill
function updateFreshness() {
  const el = $("#freshness");
  if (!el) return;
  if (!state.lastUpdate) { el.textContent = ""; el.className = "freshness"; return; }
  const s = Math.round((Date.now() - state.lastUpdate) / 1000);
  const poll = Math.max(20, CFG.poll_interval || 60);
  const dur = s < 90 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`;
  const fresh = s < 90;
  // compact on the phone header (colour carries the meaning); full text elsewhere
  el.textContent = isMobile()
    ? dur
    : fresh ? `${t("updated")} ${dur} ${lang === "uk" ? "тому" : "ago"}`
            : `${t("stale")} ${dur}`;
  el.title = fresh ? `${t("updated")} ${dur} ${lang === "uk" ? "тому" : "ago"}`
                   : `${t("stale")} ${dur}`;
  let cls = "freshness ok";
  if (s > poll * 2) cls = "freshness warn";
  if (s > poll * 4) cls = "freshness bad";
  el.className = cls;
}

// ---------------------------------------------------------------- boot loop
function banner(msg) {
  let el = $("#banner");
  if (!msg) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div");
    el.id = "banner";
    document.body.appendChild(el);
  }
  el.textContent = msg;
}

async function boot() {
  let tries = 0;
  for (;;) {
    try {
      banner(t("connecting"));
      await init();
      banner(null);
      return;
    } catch (e) {
      tries++;
      banner(
        t("cantReach") + "\n" + t("startWith") + "\n(" + t("retry") + " " + tries + "…)"
      );
      setStatus(t("offline"));
      await new Promise((r) => setTimeout(r, 4000));
    }
  }
}

boot();
