"""Home Assistant integration: publish air-threat state as MQTT-discovery
entities so HA automations can react (TTS, push, sirens).

Entities (device "DroneVisualizer"):
    binary_sensor.dronevis_<slug>     one per threat type, on when a cluster of
                                      that type has its position OR destination
                                      inside the configured area
    binary_sensor.dronevis_danger     any threat type on
    binary_sensor.dronevis_alarm      on only for the `alarm.threats` you list
                                      (default: ballistic) above min_confidence
    sensor.dronevis_active            active cluster count
    sensor.dronevis_nearest_km        distance to the nearest active threat
    sensor.dronevis_last_update       timestamp of the latest report

Broker: `mqtt.host` in config, or - inside the HA add-on - auto-discovered
from the Supervisor (`http://supervisor/services/mqtt`).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from dateutil import parser as dtparse

from .areas import area_center, cluster_touches_area
from .config import Config
from .geo.util import bearing_deg, compass, haversine_km
from .log import get_logger
from .parse.threats import ALL_SLUGS, FAMILY, LABEL

log = get_logger("ha")

_DEVICE = {
    "identifiers": ["dronevisualizer"],
    "name": "DroneVisualizer",
    "model": "Air-threat map",
    "manufacturer": "dronevis",
}


def _dt(s: str) -> datetime:
    return dtparse.isoparse(s)


_FAMILIES = set(FAMILY.values())


def expand_alarm_slugs(names: list[str]) -> set[str]:
    """`alarm.threats` entries may be slugs or family names. A family name
    (e.g. "ballistic") expands to every slug in it - so listing "ballistic"
    also alarms Kinzhal and Iskander."""
    out: set[str] = set()
    for n in names:
        n = n.strip().lower()
        if n in _FAMILIES:
            out |= {s for s, fam in FAMILY.items() if fam == n}
        elif n in ALL_SLUGS:
            out.add(n)
    return out


# ---------------------------------------------------------------- state
def compute_state(db, cfg: Config) -> dict:
    """Snapshot of what is threatening the configured area, ready to publish."""
    area = cfg.default_area
    centre = area_center(area)
    since = (datetime.now(timezone.utc)
             .timestamp() - cfg.alarm.active_minutes * 60)
    rows = db.query(
        "SELECT * FROM cluster WHERE resolved_at IS NULL AND last_posted_at >= ? "
        "ORDER BY last_posted_at DESC",
        (datetime.fromtimestamp(since, timezone.utc).isoformat(),),
    )

    per: dict[str, list] = {}
    for c in rows:
        if c["centroid_lat"] is None:
            continue
        if cfg.alarm.in_area_only and not cluster_touches_area(area, c):
            continue
        per.setdefault(c["threat_type"], []).append(c)

    # per-cluster best parse confidence (for the alarm gate)
    conf: dict[int, float] = {}
    ids = [c["id"] for cl in per.values() for c in cl]
    if ids:
        ph = ",".join("?" * len(ids))
        for r in db.query(
            f"SELECT cluster_id, MAX(parse_confidence) mc FROM event "
            f"WHERE cluster_id IN ({ph}) GROUP BY cluster_id", ids
        ):
            conf[r["cluster_id"]] = r["mc"] or 0.0

    def where(c) -> tuple[float, float, str | None]:
        # how far the object physically is, and its bearing from the area centre
        pos = (c["centroid_lat"], c["centroid_lon"])
        return haversine_km(pos, centre), bearing_deg(centre, pos), c["place_name"]

    threats: dict[str, dict] = {}
    for slug, cl in per.items():
        dists = [where(c) for c in cl]
        nd, nb, npl = min(dists, key=lambda x: x[0])
        dests = sorted({c["dest_name"] for c in cl
                        if c["dest_name"] and cluster_touches_area(area, {
                            "centroid_lat": c["dest_lat"], "centroid_lon": c["dest_lon"],
                            "dest_lat": None, "dest_lon": None})})
        chans = sorted({ch for c in cl for ch in json.loads(c["channels"])})
        threats[slug] = {
            "on": True,
            "label": LABEL.get(slug, slug),
            "count": sum((c["count"] or c["event_count"] or 1) for c in cl),
            "clusters": len(cl),
            "nearest_km": round(nd, 1),
            "nearest_bearing": compass(nb),
            "nearest_place": npl,
            "heading_to": dests,
            "sources": chans,
            "updated": max(c["last_posted_at"] for c in cl),
            "first_seen": min(c["first_posted_at"] for c in cl),
            "confidence": round(max((conf.get(c["id"], 0.0) for c in cl), default=0.0), 2),
        }

    active_slugs = [s for s in ALL_SLUGS if s in threats]
    alarm_slugs = expand_alarm_slugs(cfg.alarm.threats)
    alarm_hits = [threats[s] for s in active_slugs
                  if s in alarm_slugs and threats[s]["confidence"] >= cfg.alarm.min_confidence]
    all_near = [t["nearest_km"] for t in threats.values()]

    alarm_on = bool(alarm_hits)
    alarm_msg = ""
    if alarm_on:
        t = min(alarm_hits, key=lambda x: x["nearest_km"])
        bits = [t["label"], f"×{t['count']}" if t["count"] > 1 else "",
                f"{t['nearest_km']} km {t['nearest_bearing'] or ''}".strip(),
                ("→ " + ", ".join(t["heading_to"])) if t["heading_to"] else ""]
        alarm_msg = " · ".join(b for b in bits if b)

    return {
        "threats": threats,
        "danger": {
            "on": bool(active_slugs),
            "types": active_slugs,
            "count": sum(t["count"] for t in threats.values()),
            "nearest_km": round(min(all_near), 1) if all_near else None,
        },
        "alarm": {
            "on": alarm_on,
            "types": [s for s in active_slugs if s in alarm_slugs],
            "nearest_km": (round(min(t["nearest_km"] for t in alarm_hits), 1)
                           if alarm_hits else None),
            "message": alarm_msg,
        },
        "active": sum(t["clusters"] for t in threats.values()),
        "nearest_km": round(min(all_near), 1) if all_near else None,
        "last_update": (max((t["updated"] for t in threats.values()), default=None)
                        or datetime.now(timezone.utc).isoformat()),
        "area": area.label,
    }


# ---------------------------------------------------------------- MQTT
class HAPublisher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mc = cfg.mqtt
        self._client = None
        self._connected = False
        self._lock = threading.Lock()
        self.enabled = self.mc.enabled != "false"

    # -- broker resolution -------------------------------------------------
    def _broker(self) -> tuple[str, int, str, str] | None:
        if self.mc.host:
            return self.mc.host, self.mc.port, self.mc.username, self.mc.password
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return None
        try:
            import httpx

            r = httpx.get("http://supervisor/services/mqtt",
                          headers={"Authorization": f"Bearer {token}"}, timeout=5)
            r.raise_for_status()
            d = r.json()["data"]
            return d["host"], int(d["port"]), d.get("username", ""), d.get("password", "")
        except Exception as exc:                       # noqa: BLE001
            log.info("no Supervisor MQTT service: %s", exc)
            return None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            if self.mc.enabled == "true":
                log.warning("mqtt.enabled=true but 'paho-mqtt' is not installed")
            return False
        broker = self._broker()
        if not broker:
            if self.mc.enabled == "true":
                log.warning("mqtt.enabled=true but no broker (set mqtt.host)")
            return False
        host, port, user, pw = broker
        base = self.mc.base_topic
        cli = mqtt.Client(client_id="dronevisualizer")
        if user:
            cli.username_pw_set(user, pw)
        cli.will_set(f"{base}/status", "offline", retain=True)
        cli.on_connect = self._on_connect
        try:
            cli.connect(host, port, keepalive=45)
        except Exception as exc:                       # noqa: BLE001
            log.warning("MQTT connect to %s:%s failed: %s", host, port, exc)
            return False
        cli.loop_start()
        self._client = cli
        log.info("MQTT: publishing HA discovery to %s:%s (%s/*)", host, port, base)
        return True

    def _on_connect(self, client, *_):
        self._connected = True
        client.publish(f"{self.mc.base_topic}/status", "online", retain=True)
        self._publish_discovery()

    def close(self) -> None:
        if self._client:
            try:
                self._client.publish(f"{self.mc.base_topic}/status", "offline", retain=True)
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:                          # noqa: BLE001
                pass

    # -- discovery ------------------------------------------------------
    def _cfg_topic(self, comp: str, obj: str) -> str:
        return f"{self.mc.discovery_prefix}/{comp}/dronevisualizer/{obj}/config"

    def _entity(self, comp: str, obj: str, payload: dict) -> None:
        payload.setdefault("availability_topic", f"{self.mc.base_topic}/status")
        payload["device"] = _DEVICE
        payload["unique_id"] = f"dronevis_{obj}"
        self._client.publish(self._cfg_topic(comp, obj), json.dumps(payload), retain=True)

    def _publish_discovery(self) -> None:
        b = self.mc.base_topic
        for slug in ALL_SLUGS:
            self._entity("binary_sensor", f"threat_{slug}", {
                "name": f"{LABEL[slug]}",
                "device_class": "safety",
                "state_topic": f"{b}/threat/{slug}/state",
                "json_attributes_topic": f"{b}/threat/{slug}/attributes",
                "payload_on": "ON", "payload_off": "OFF",
                "icon": "mdi:alert",
            })
        self._entity("binary_sensor", "danger", {
            "name": "Air danger", "device_class": "safety",
            "state_topic": f"{b}/danger/state",
            "json_attributes_topic": f"{b}/danger/attributes",
            "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:radar",
        })
        self._entity("binary_sensor", "alarm", {
            "name": "Air alarm", "device_class": "safety",
            "state_topic": f"{b}/alarm/state",
            "json_attributes_topic": f"{b}/alarm/attributes",
            "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:alarm-light",
        })
        self._entity("sensor", "active", {
            "name": "Active threats", "state_topic": f"{b}/active/state",
            "state_class": "measurement", "icon": "mdi:counter",
        })
        self._entity("sensor", "nearest_km", {
            "name": "Nearest threat", "state_topic": f"{b}/nearest_km/state",
            "unit_of_measurement": "km", "device_class": "distance",
            "state_class": "measurement", "icon": "mdi:map-marker-distance",
        })
        self._entity("sensor", "last_update", {
            "name": "Last report", "state_topic": f"{b}/last_update/state",
            "device_class": "timestamp", "icon": "mdi:clock-outline",
        })

    # -- state --------------------------------------------------------
    def publish(self, state: dict) -> None:
        if not (self._client and self._connected):
            return
        b, pub = self.mc.base_topic, self._client.publish
        with self._lock:
            for slug in ALL_SLUGS:
                t = state["threats"].get(slug)
                pub(f"{b}/threat/{slug}/state", "ON" if t else "OFF", retain=True)
                pub(f"{b}/threat/{slug}/attributes",
                    json.dumps(t or {"on": False}), retain=True)
            for key in ("danger", "alarm"):
                pub(f"{b}/{key}/state", "ON" if state[key]["on"] else "OFF", retain=True)
                pub(f"{b}/{key}/attributes", json.dumps(state[key]), retain=True)
            pub(f"{b}/active/state", str(state["active"]), retain=True)
            pub(f"{b}/nearest_km/state",
                "" if state["nearest_km"] is None else str(state["nearest_km"]), retain=True)
            pub(f"{b}/last_update/state", state["last_update"], retain=True)
