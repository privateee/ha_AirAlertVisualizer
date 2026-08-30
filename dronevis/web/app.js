"use strict";

const $ = (s) => document.querySelector(s);
const WINDOW_MS = {
  "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5, "2h": 2 * 36e5, "3h": 3 * 36e5,
  "6h": 6 * 36e5, "12h": 12 * 36e5, "24h": 24 * 36e5, "48h": 48 * 36e5,
};

let CFG = null;
let map = null;
let tileLayer = null;
const layer = L.layerGroup();
let clusters = [];
let timer = null;

const state = {
  area: null,
  window: "3h",
  threatsOff: new Set(),      // slugs the user unchecked
  channelsOff: new Set(),
  q: "",
  asOf: 1,                    // 0..1 across the window; 1 == now
  live: true,
  mapTheme: "dark",
};

function lsGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }

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
    applyMapTheme(loadMapTheme());
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
  all.value = "all"; all.textContent = "Everything";
  sel.appendChild(all);
}

function buildThreatChips() {
  const box = $("#threats");
  box.innerHTML = "";
  for (const t of CFG.threats) {
    const el = document.createElement("label");
    el.className = "chip";
    el.innerHTML = `<span class="dot" style="background:${t.color}"></span>${esc(t.short || t.label)}`;
    el.title = t.label;
    el.addEventListener("click", () => {
      state.threatsOff.has(t.slug) ? state.threatsOff.delete(t.slug) : state.threatsOff.add(t.slug);
      el.classList.toggle("off");
      loadClusters();
    });
    box.appendChild(el);
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
  $("#sheetHandle").addEventListener("click", () => {
    document.body.classList.toggle("sheet-open");
  });

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
    $("#scrubLabel").textContent = state.asOf >= 0.999 ? "now" : fmtClock(asOfDate());
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
  let deb;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.q = e.target.value.trim(); loadMessages(); }, 300);
  });
}

function startTimer() {
  if (timer) clearInterval(timer);
  if (!state.live) { setStatus("paused"); return; }
  const every = Math.max(20, CFG.poll_interval || 60) * 1000;
  timer = setInterval(refresh, every);
}

// ---------------------------------------------------------------- data
async function refresh() {
  await Promise.all([loadClusters(), loadMessages()]);
}

async function loadClusters() {
  const p = new URLSearchParams();
  p.set("area", state.area);
  p.set("since", "-" + state.window);
  const on = CFG.threats.map((t) => t.slug).filter((s) => !state.threatsOff.has(s));
  if (on.length && on.length !== CFG.threats.length) p.set("threats", on.join(","));
  try {
    const resp = await fetch("api/clusters?" + p);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    clusters = data.clusters || [];
    renderClusters();
    setStatus(`${clusters.length} tracks · updated ${fmtClock(new Date())}`);
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
    renderMessages(data.messages || []);
  } catch (e) { /* keep old list */ }
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
    const op = Math.max(0.25, 1 - age / winMin);
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
    if (badge) {
      m.bindTooltip(badge, {
        permanent: true, direction: "center", className: "count-badge",
      });
    }
    m.addTo(layer);
  }
}

function popupHtml(c) {
  const obs = c.heading_observed ? " · observed" : "";
  const dst = c.dest_name
    ? `<div class="pp-row">→ <b>${esc(c.dest_name)}</b>${c.compass ? " (" + c.compass + obs + ")" : ""}</div>`
    : (c.compass ? `<div class="pp-row">heading ${c.compass}${obs}</div>` : "");
  const src = (c.sources || []).map((s) =>
    `<div>${esc(s.channel)} · <a href="${esc(s.url)}" target="_blank" rel="noopener">${fmtClock(new Date(s.posted_at))}</a>${s.count ? " · " + s.count + "×" : ""}${s.line ? " — " + esc(s.line) : ""}</div>`
  ).join("");
  const size = c.count != null
    ? `<b>${c.count}</b> ${c.threat_type === "shahed" || c.threat_type === "jet_uav" ? "drone" : "unit"}${c.count === 1 ? "" : "s"}`
      + (c.count_max && c.count_max > c.count ? ` (peak ${c.count_max})` : "")
    : "";
  const reports = c.event_count > 1 ? `${c.event_count} reports` : "";
  const line2 = [size, esc(c.status), reports].filter(Boolean).join(" · ");
  return `<div class="pp-h" style="color:${c.color}">${esc(c.threat_label)}</div>
    <div class="pp-row"><b>${esc(c.place_name || "—")}</b></div>
    <div class="pp-row">${line2}</div>
    ${dst}
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
  if (isMobile()) document.body.classList.remove("sheet-open");
  map.flyTo([loc.lat, loc.lon], Math.max(map.getZoom(), 10), { duration: 0.6 });
}

// ---------------------------------------------------------------- render: stream
function renderMessages(msgs) {
  const ul = $("#msgs");
  ul.innerHTML = "";
  const now = Date.now();
  for (const m of msgs) {
    const li = document.createElement("li");
    li.className = "msg";
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

  // bottom-sheet peek summary
  const hot = msgs.filter((m) => (now - new Date(m.posted_at).getTime()) / 60000 < 15).length;
  const latest = msgs[0];
  const sum = $("#sheetSummary");
  if (sum) {
    sum.textContent = latest
      ? `${msgs.length} in feed${hot ? " · " + hot + " new" : ""} — ${latest.channel}: ${latest.text.slice(0, 46)}`
      : "Feed";
  }
}

function setStatus(s) { $("#status").textContent = s; }

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
      banner("Connecting to the DroneVisualizer server…");
      await init();
      banner(null);
      return;
    } catch (e) {
      tries++;
      banner(
        "Can't reach the DroneVisualizer server on this address.\n" +
        "Start it with:  python -m dronevis run   — then this page reconnects automatically.\n" +
        "(retry " + tries + "…)"
      );
      setStatus("server offline");
      await new Promise((r) => setTimeout(r, 4000));
    }
  }
}

boot();
