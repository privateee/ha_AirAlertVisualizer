"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const WINDOW_MS = {
  "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5, "2h": 2 * 36e5, "3h": 3 * 36e5,
  "6h": 6 * 36e5, "12h": 12 * 36e5, "24h": 24 * 36e5, "48h": 48 * 36e5,
};

let CFG = null;
let map = null;
let tileLayer = null;
const layer = L.layerGroup();
const fxLayer = L.layerGroup();        // transient "new threat" pulses
let hereMarker = null;
let clusters = [];
let lastMsgs = [];
let timer = null;
let freshBoot = false;                  // freshness ticker installed once
let seenIds = null;                     // cluster ids seen on a previous poll
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
    threats: "Threats", options: "Options", myloc: "My location", locating: "locating…",
    locpin: "You", inFeed: "in feed", new: "new", tracks: "tracks",
    updated: "updated", stale: "no updates for", offline: "server offline",
    showOnMap: "◎ show on map", reports: "reports", peak: "peak",
    heading: "heading", drone: "drone", unit: "unit", eta: "ETA",
    away: "away", connecting: "Connecting to the DroneVisualizer server…",
    cantReach: "Can't reach the DroneVisualizer server on this address.",
    startWith: "Start it with:  python -m dronevis run   — then this page reconnects automatically.",
    retry: "retry",
  },
  uk: {
    brand: "DroneVisualizer", area: "Регіон", window: "Період", live: "Наживо",
    fetch: "Оновити", feed: "Стрічка", filterText: "пошук у тексті…", now: "зараз",
    threats: "Загрози", options: "Опції", myloc: "Моє місце", locating: "визначення…",
    locpin: "Ви", inFeed: "у стрічці", new: "нових", tracks: "цілей",
    updated: "оновлено", stale: "немає оновлень", offline: "сервер недоступний",
    showOnMap: "◎ показати на мапі", reports: "повідомлень", peak: "макс",
    heading: "курс", drone: "дрон", unit: "од.", eta: "підліт",
    away: "від вас", connecting: "З'єднання із сервером DroneVisualizer…",
    cantReach: "Не вдається під'єднатися до сервера DroneVisualizer.",
    startWith: "Запустіть:  python -m dronevis run   — сторінка під'єднається сама.",
    retry: "спроба",
  },
};
let lang = lsGet("lang") || ((navigator.language || "").startsWith("uk") ? "uk" : "en");
if (!I18N[lang]) lang = "en";
function t(k) { return (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k; }
function applyI18n() {
  document.documentElement.lang = lang;
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  const lb = $("#lang");
  if (lb) { lb.textContent = lang.toUpperCase(); lb.title = "Language / Мова"; }
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
  document.body.classList.remove("sheet-open", "sheet-mid", "filters-open");
  applyLayout();
}

WIDE_MQ.addEventListener("change", () => { if (layoutPref === "auto") applyLayout(); });

// ------------------------------------------------------- 3-state bottom sheet
// peek -> mid -> open -> peek
function cycleSheet() {
  const b = document.body;
  if (b.classList.contains("sheet-open")) {
    b.classList.remove("sheet-open", "sheet-mid");
  } else if (b.classList.contains("sheet-mid")) {
    b.classList.remove("sheet-mid");
    b.classList.add("sheet-open");
  } else {
    b.classList.add("sheet-mid");
  }
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

  const btn = $("#theme");
  if (btn) {
    btn.textContent = dark ? "☀" : "🌙";
    btn.title = dark ? "Switch to light map" : "Switch to dark map";
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
function haversineKm(a, b) {
  const R = 6371, r = Math.PI / 180;
  const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const s = Math.sin(dLat / 2) ** 2 +
    Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}
function threatSpeed(slug) {
  const tt = (CFG.threats || []).find((x) => x.slug === slug);
  return (tt && tt.speed_kmh) || 200;
}
function clusterConfidence(c) {
  return Math.max(0, ...(c.sources || []).map((s) => s.confidence || 0));
}

// ---------------------------------------------------------------- boot
async function init() {
  const resp = await fetch("api/config");
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  CFG = await resp.json();

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
    map = L.map("map", { zoomControl: true })
      .setView(center, a.radius_km && a.radius_km < 150 ? 8 : 6);
    layer.addTo(map);
    fxLayer.addTo(map);
    applyMapTheme(loadMapTheme());
    if (state.here) drawHere();
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
    el.className = "chip";
    el.textContent = ch;
    el.addEventListener("click", () => {
      state.channelsOff.has(ch) ? state.channelsOff.delete(ch) : state.channelsOff.add(ch);
      el.classList.toggle("off");
      loadMessages();
      renderClusters();
    });
    box.appendChild(el);
  }
}

function closeDrawer() { document.body.classList.remove("filters-open"); }

function wire() {
  $("#filtersToggle").addEventListener("click", () => {
    document.body.classList.toggle("filters-open");
  });
  $("#sheetHandle").addEventListener("click", cycleSheet);

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
  $("#scrub").addEventListener("input", (e) => {
    state.asOf = e.target.value / 100;
    $("#scrubLabel").textContent = state.asOf >= 0.999 ? t("now") : fmtClock(asOfDate());
    renderClusters();
  });
  $("#live").addEventListener("change", (e) => { state.live = e.target.checked; startTimer(); });
  $("#ingestBtn").addEventListener("click", async () => {
    setStatus("fetching…");
    try { await fetch("api/ingest", { method: "POST" }); } catch (_) {}
    await refresh();
  });
  $("#theme").addEventListener("click", () => {
    applyMapTheme(state.mapTheme === "dark" ? "light" : "dark");
  });
  $("#layout").addEventListener("click", cycleLayout);
  $("#threatsBox")?.addEventListener("toggle", (e) => {
    lsSet("threatsOpen", e.target.open ? "1" : "0");
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

  let deb;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = e.target.value.trim(); loadMessages(); }, 300);
  });

  map.on("popupclose", () => { if (state.pinned) { state.pinned = null; markPinned(); } });

  // tap the map to step the feed sheet back down (open -> mid -> peek), so a
  // raised sheet is never a dead end on a phone
  map.on("click", () => {
    const b = document.body;
    if (b.classList.contains("sheet-open")) {
      b.classList.remove("sheet-open"); b.classList.add("sheet-mid");
    } else if (b.classList.contains("sheet-mid")) {
      b.classList.remove("sheet-mid");
    }
  });
}

function startTimer() {
  // keep the "updated Ns ago" pill honest between polls - installed once, and
  // left running when Live is paused so the pill can still go stale/red
  if (!freshBoot) { setInterval(updateFreshness, 5000); freshBoot = true; }

  if (timer) clearInterval(timer);
  if (!state.live) { setStatus("paused"); return; }
  const every = Math.max(20, CFG.poll_interval || 60) * 1000;
  timer = setInterval(() => { refresh(); updateFreshness(); }, every);
}

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
      renderClusters();
    },
    () => { b.classList.remove("busy"); b.title = t("myloc"); },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 6e5 }
  );
}

function drawHere() {
  if (hereMarker) { map.removeLayer(hereMarker); hereMarker = null; }
  if (!state.here) return;
  hereMarker = L.marker([state.here.lat, state.here.lon], {
    icon: L.divIcon({ className: "here-pin", html: "📍", iconSize: [24, 24], iconAnchor: [12, 22] }),
    title: t("locpin"), interactive: false, keyboard: false,
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
  await Promise.all([loadClusters(), loadMessages()]);
}

async function loadClusters() {
  const p = new URLSearchParams();
  p.set("area", state.area);
  p.set("since", "-" + state.window);
  const on = CFG.threats.map((th) => th.slug).filter((s) => !state.threatsOff.has(s));
  if (on.length && on.length !== CFG.threats.length) p.set("threats", on.join(","));
  try {
    const resp = await fetch("api/clusters?" + p);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const prev = clusters;
    clusters = data.clusters || [];
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
  // the stream stays readable even at a 15-minute map window
  p.set("since", "-" + (windowMs() < 6 * 36e5 ? "6h" : state.window));
  if (state.q) p.set("q", state.q);
  p.set("limit", "250");
  try {
    const data = await (await fetch("api/messages?" + p)).json();
    lastMsgs = data.messages || [];
    renderMessages(lastMsgs);
  } catch (e) { /* keep old list */ }
}

// new clusters since the previous poll -> pulse on the map + a soft beep
function detectNew(prev) {
  if (seenIds === null) {                 // first load: prime, don't alert
    seenIds = new Set(clusters.map((c) => c.id));
    return;
  }
  const fresh = clusters.filter((c) => !seenIds.has(c.id) && c.lat != null);
  for (const c of clusters) seenIds.add(c.id);
  if (!fresh.length || !state.live || state.asOf < 0.999) return;

  let flashed = false;
  for (const c of fresh) {
    pulse(c.lat, c.lon, c.color);
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
  layer.clearLayers();
  const cutoff = asOfDate().getTime();
  const winMin = windowMs() / 60000;
  const offCh = state.channelsOff;

  for (const c of clusters) {
    if (c.lat == null) continue;
    if (new Date(c.last_posted_at).getTime() > cutoff) continue;
    if (offCh.size && c.channels.every((ch) => offCh.has(ch))) continue;

    const age = Math.max(0, (cutoff - new Date(c.last_posted_at).getTime()) / 60000);
    let op = Math.max(0.25, 1 - age / winMin);
    // dim uncertain parses: full opacity at conf>=0.8, ~0.45x at conf 0
    const conf = clusterConfidence(c);
    op *= 0.45 + 0.55 * Math.min(1, conf / 0.8);
    // size by the reported group size, falling back to number of reports
    const size = Math.max(c.count || 0, c.event_count || 1);
    const r = 7 + Math.min(16, (size - 1) * 3);
    // badge shows the drone count if we have one, else how many reports
    const badge = c.count != null ? String(c.count) : (c.event_count > 1 ? String(c.event_count) : "");

    // movement guess: the path through successive reported positions
    if (Array.isArray(c.track) && c.track.length >= 2) {
      L.polyline(c.track, {
        color: c.color, weight: 2.5, opacity: 0.85 * op, lineJoin: "round",
      }).addTo(layer);
      for (const pt of c.track.slice(0, -1)) {
        L.circleMarker(pt, {
          radius: 2.5, color: c.color, weight: 0,
          fillColor: c.color, fillOpacity: 0.55 * op,
        }).addTo(layer);
      }
    }

    if (c.status === "circling") {
      L.circle([c.lat, c.lon], { radius: 9000, color: c.color, weight: 1,
        opacity: 0.5 * op, fill: false, dashArray: "3 7" }).addTo(layer);
    }
    if (c.dest_lat != null) {
      L.polyline([[c.lat, c.lon], [c.dest_lat, c.dest_lon]], {
        color: c.color, weight: 2, opacity: 0.7 * op, dashArray: "5 6",
      }).addTo(layer);
      L.circleMarker([c.dest_lat, c.dest_lon], {
        radius: 3, color: c.color, opacity: 0.7 * op, fillOpacity: 0.7 * op,
      }).addTo(layer);
    } else if (c.heading_deg != null) {
      const to = destPoint(c.lat, c.lon, c.heading_deg, 28);
      L.polyline([[c.lat, c.lon], to], {
        color: c.color, weight: 2, opacity: 0.7 * op, dashArray: "5 6",
      }).addTo(layer);
    }

    const m = L.circleMarker([c.lat, c.lon], {
      radius: r, color: c.color, weight: 2, opacity: op,
      fillColor: c.color, fillOpacity: 0.5 * op,
    }).bindPopup(popupHtml(c), { maxWidth: 320 });
    m.on("click", () => { state.pinned = c.id; markPinned(c, true); });
    if (badge) {
      m.bindTooltip(badge, {
        permanent: true, direction: "center", className: "count-badge",
      });
    }
    m.addTo(layer);
  }
  markPinned();
}

function popupHtml(c) {
  const obs = c.heading_observed ? " · observed" : "";
  const dst = c.dest_name
    ? `<div class="pp-row">→ <b>${esc(c.dest_name)}</b>${c.compass ? " (" + c.compass + obs + ")" : ""}</div>`
    : (c.compass ? `<div class="pp-row">${t("heading")} ${c.compass}${obs}</div>` : "");
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
  const line2 = [size, esc(c.status), reports].filter(Boolean).join(" · ") + confStr;

  let hereRow = "";
  if (state.here) {
    const d = haversineKm([state.here.lat, state.here.lon], [c.lat, c.lon]);
    const etaMin = (d / threatSpeed(c.threat_type)) * 60;
    hereRow = `<div class="pp-row pp-here">📍 ${d.toFixed(0)} km ${t("away")}` +
      (c.dest_name || c.heading_deg != null ? ` · ${t("eta")} ~${etaMin < 1 ? "<1" : Math.round(etaMin)} min` : "") +
      `</div>`;
  }
  return `<div class="pp-h" style="color:${c.color}">${esc(c.threat_label)}</div>
    <div class="pp-row"><b>${esc(c.place_name || "—")}</b></div>
    <div class="pp-row">${line2}</div>
    ${dst}
    ${hereRow}
    <div class="pp-row" style="color:#666">${esc((c.channels || []).join(", "))} · ${ageStr(c.age_minutes)}</div>
    <div class="pp-src">${src}</div>`;
}

// small-distance destination point, km -> lat/lon
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
  if (isMobile()) document.body.classList.remove("sheet-open", "sheet-mid");
  map.flyTo([loc.lat, loc.lon], Math.max(map.getZoom(), 10), { duration: 0.6 });
}

// highlight the feed rows that belong to a clicked map marker, and reveal them.
// `raise` is only set by a real marker tap - re-applying the highlight after a
// poll must not shove the sheet back up if the user has since lowered it.
function markPinned(c, raise = false) {
  const urls = c ? new Set((c.sources || []).map((s) => s.url)) : null;
  $$("#msgs .msg").forEach((li) => {
    li.classList.toggle("pinned", !!urls && urls.has(li.dataset.url));
  });
  if (!c) return;
  const b = document.body;
  if (raise && isMobile() &&
      !b.classList.contains("sheet-mid") && !b.classList.contains("sheet-open")) {
    b.classList.add("sheet-mid");                    // peek -> mid, once
  }
  const first = $("#msgs .msg.pinned");
  if (first) first.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---------------------------------------------------------------- render: stream
function renderMessages(msgs) {
  const ul = $("#msgs");
  ul.innerHTML = "";
  const now = Date.now();
  for (const m of msgs) {
    const li = document.createElement("li");
    li.className = "msg";
    li.dataset.url = m.url;
    const ageMin = (now - new Date(m.posted_at).getTime()) / 60000;
    if (ageMin < 10) li.classList.add("hot");
    const located = (m.events || []).some((e) => e.lat != null);
    if (located) li.classList.add("locatable");
    const tags = (m.events || []).map((e) => {
      const bits = [e.count ? e.count + "×" : "", e.place_name || "", e.dest_name ? "→ " + e.dest_name : "", e.heading || ""]
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
    li.querySelector(".meta a").addEventListener("click", (ev) => ev.stopPropagation());
    if (located) li.addEventListener("click", () => showMessageOnMap(m));
    ul.appendChild(li);
  }

  drawTimeline(msgs);
  if (state.pinned) {
    const c = clusters.find((x) => x.id === state.pinned);
    markPinned(c);
  }

  // bottom-sheet peek summary
  const hot = msgs.filter((m) => (now - new Date(m.posted_at).getTime()) / 60000 < 15).length;
  const latest = msgs[0];
  const sum = $("#sheetSummary");
  if (sum) {
    sum.textContent = latest
      ? `${msgs.length} ${t("inFeed")}${hot ? " · " + hot + " " + t("new") : ""} — ${latest.channel}: ${latest.text.slice(0, 46)}`
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
