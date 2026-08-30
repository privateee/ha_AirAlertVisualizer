"""Cluster events that describe the same airborne object.

A new event joins the best open cluster of the same *family* that is close
enough in time and space, OR - the "5 БпЛА повз Славутич" then "5 на Димер a
few minutes later" case - a plausible next step along a known track:

* same group size (count) is a strong signal
* a single-source cluster (one channel reporting repeatedly) is trusted more
* the jump must be *downrange* of the cluster's heading and reachable at the
  threat's cruise speed within the elapsed time

When neither side has coordinates we fall back to text similarity.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from dateutil import parser as dtparse
from rapidfuzz import fuzz

from .config import Config
from .db import Database
from .geo.util import bearing_deg, haversine_km
from .parse.pipeline import ParsedEvent
from .parse.threats import FAMILY


def _dt(iso: str) -> datetime:
    return dtparse.isoparse(iso)


def _angdiff(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings, 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


class Deduper:
    def __init__(self, db: Database, cfg: Config):
        self.db = db
        self.cfg = cfg.dedupe

    # -- public -----------------------------------------------------------
    def assign(
        self, event_id: int, ev: ParsedEvent, channel: str, posted_at: str
    ) -> int:
        window = timedelta(minutes=self.cfg.time_window_minutes)
        now = _dt(posted_at)
        since = (now - window).isoformat()
        family = FAMILY.get(ev.threat_type, "unknown")
        max_span = timedelta(minutes=self.cfg.max_span_minutes)

        best_id: int | None = None
        best_score = 0.0
        for c in self.db.open_clusters(since):
            if self.cfg.incompatible_split and FAMILY.get(c["threat_type"]) != family:
                continue
            gap = abs((now - _dt(c["last_posted_at"])).total_seconds())
            if gap > window.total_seconds():
                continue
            if now - _dt(c["first_posted_at"]) > max_span:
                continue
            score = self._score(ev, c, family, channel, gap / 60.0)
            if score > best_score:
                best_score, best_id = score, c["id"]

        if best_id is not None and best_score >= 0.5:
            self._attach(best_id, event_id, ev, channel, posted_at)
            return best_id
        return self._create(event_id, ev, channel, posted_at)

    # -- scoring --------------------------------------------------------
    def _count_match(self, a, b) -> bool:
        return a is not None and b is not None and abs(a - b) <= self.cfg.count_tolerance

    def _count_conflict(self, a, b) -> bool:
        return a is not None and b is not None and abs(a - b) > self.cfg.count_tolerance

    def _score(self, ev: ParsedEvent, c, family: str, channel: str, gap_min: float) -> float:
        c_count = c["count"] if "count" in c.keys() else None
        single_source = json.loads(c["channels"]) == [channel]
        count_match = self._count_match(ev.count, c_count)
        count_conflict = self._count_conflict(ev.count, c_count)

        # exact same named position -> almost certainly the same object
        if ev.place_name and c["place_name"] and ev.place_name == c["place_name"]:
            return 0.80 if count_conflict else 0.95

        if family == "unknown":
            # terse "unknown" spottings carry little identity; only chain them
            # when the group size matches and it is the same single reporter
            if single_source and count_match and ev.lat is not None and c["centroid_lat"] is not None:
                return self._trajectory_score(
                    ev, c, gap_min, single_source, count_match, count_conflict
                )
            return 0.0

        if ev.lat is not None and c["centroid_lat"] is not None:
            d = haversine_km((ev.lat, ev.lon), (c["centroid_lat"], c["centroid_lon"]))
            converging = bool(
                ev.dest_name and ev.dest_name in {c["place_name"], c["dest_name"]}
            )

            # close by -> same object (unless the group size clearly differs)
            if d <= self.cfg.distance_km and not count_conflict:
                base = max(0.55, 1.0 - d / (self.cfg.distance_km * 2))
                return min(0.98, base + (0.05 if count_match else 0.0))

            # far apart: only a plausible downrange step keeps them together
            if self.cfg.trajectory:
                tj = self._trajectory_score(
                    ev, c, gap_min, single_source, count_match, count_conflict
                )
                if tj > 0:
                    return tj

            # different named place, not converging, no trajectory -> not it
            if (
                not converging
                and ev.place_name and c["place_name"]
                and ev.place_name != c["place_name"]
            ):
                return 0.0
            if converging and d <= self.cfg.distance_km * 2.5 and not count_conflict:
                return 0.58
            if (
                ev.dest_name and c["dest_name"] and ev.dest_name == c["dest_name"]
                and d <= self.cfg.distance_km * 3 and not count_conflict
            ):
                return 0.6
            return 0.0

        # no geometry on one side -> fall back to wording
        if ev.raw_line and c["place_name"]:
            r = fuzz.token_set_ratio(ev.raw_line.lower(), c["place_name"].lower()) / 100
            if r >= self.cfg.text_similarity:
                return 0.5 + (r - self.cfg.text_similarity)
        return 0.0

    def _trajectory_score(
        self, ev: ParsedEvent, c, gap_min: float, single_source: bool,
        count_match: bool, count_conflict: bool
    ) -> float:
        """Is ``ev`` a believable next sighting of moving cluster ``c``?"""
        if ev.lat is None or c["centroid_lat"] is None:
            return 0.0
        # a group that keeps flying keeps its size; a different count at a new
        # place is almost always a different group
        if count_conflict:
            return 0.0
        frm = (c["centroid_lat"], c["centroid_lon"])
        to = (ev.lat, ev.lon)
        d = haversine_km(frm, to)
        if d < 0.5:
            return 0.0

        # 1) hard physical gate: distance must be coverable at this threat's
        #    speed within the elapsed time (plus a small imprecision margin),
        #    and never beyond an absolute single-hop cap. Chernihiv->Poltava
        #    (~330 km) fails this for anything slower than a ballistic missile.
        if d > self.cfg.max_hop_km:
            return 0.0
        speed = self.cfg.speed_kmh.get(ev.threat_type, 220.0)
        reach = speed * (gap_min / 60.0) * self.cfg.speed_slack
        reach = min(max(reach, self.cfg.distance_km), self.cfg.max_hop_km)
        if d > reach:
            return 0.0

        # 2) it must be moving the right way
        step_brg = bearing_deg(frm, to)
        hdg = c["heading_deg"]
        big_jump = d > self.cfg.distance_km * 2
        if hdg is not None:
            off = _angdiff(step_brg, hdg)
            tol = self.cfg.heading_tolerance_deg * (0.5 if big_jump else 1.0)
            if off > tol:
                return 0.0
            aligned = off <= self.cfg.heading_tolerance_deg * 0.55
        else:
            if big_jump:
                return 0.0          # long jump with no known heading -> no
            aligned = False

        # 3) score, penalising hard as the hop approaches the reach limit
        score = 0.5
        if aligned:
            score += 0.15
        if count_match:
            score += 0.18
        if single_source:
            score += 0.12
        if ev.dest_name and ev.dest_name in {c["place_name"], c["dest_name"]}:
            score += 0.08
        score -= 0.30 * (d / reach)
        return max(0.0, min(0.9, score)) if score >= 0.5 else 0.0

    # -- mutation --------------------------------------------------------
    def _create(self, event_id: int, ev: ParsedEvent, channel: str, posted_at: str) -> int:
        cid = self.db.insert_cluster({
            "threat_type": ev.threat_type,
            "status": ev.status,
            "first_posted_at": posted_at,
            "last_posted_at": posted_at,
            "centroid_lat": ev.lat,
            "centroid_lon": ev.lon,
            "place_name": ev.place_name,
            "dest_name": ev.dest_name,
            "dest_lat": ev.dest_lat,
            "dest_lon": ev.dest_lon,
            "heading_deg": ev.heading_deg,
            "count": ev.count,
            "count_max": ev.count,
            "event_count": 1,
            "channels": json.dumps([channel], ensure_ascii=False),
        })
        self.db.set_event_cluster(event_id, cid)
        return cid

    def _attach(
        self, cid: int, event_id: int, ev: ParsedEvent, channel: str, posted_at: str
    ) -> None:
        self.db.set_event_cluster(event_id, cid)
        c = self.db.query_one("SELECT * FROM cluster WHERE id=?", (cid,))
        channels = set(json.loads(c["channels"]))
        channels.add(channel)
        newer = _dt(posted_at) >= _dt(c["last_posted_at"])

        old_pos = (c["centroid_lat"], c["centroid_lon"])
        patch: dict = {
            "last_posted_at": max(posted_at, c["last_posted_at"]),
            "first_posted_at": min(posted_at, c["first_posted_at"]),
            "event_count": int(c["event_count"]) + 1,
            "channels": json.dumps(sorted(channels), ensure_ascii=False),
        }

        # peak group size ever reported
        cmax = max((x for x in (c["count_max"], c["count"], ev.count) if x is not None),
                   default=None)
        if cmax is not None:
            patch["count_max"] = cmax

        if newer:
            patch["status"] = ev.status
            if ev.count is not None:
                patch["count"] = ev.count
            if ev.place_name:
                patch["place_name"] = ev.place_name
            if ev.dest_name:
                patch["dest_name"] = ev.dest_name
                patch["dest_lat"] = ev.dest_lat
                patch["dest_lon"] = ev.dest_lon

            # the marker tracks the object: its position is the latest fix,
            # not the average of everywhere it has been seen
            if ev.lat is not None:
                patch["centroid_lat"] = ev.lat
                patch["centroid_lon"] = ev.lon
                if None not in old_pos and haversine_km(old_pos, (ev.lat, ev.lon)) >= 3:
                    patch["heading_deg"] = bearing_deg(old_pos, (ev.lat, ev.lon))
                elif ev.heading_deg is not None:
                    patch["heading_deg"] = ev.heading_deg
                elif ev.dest_lat is not None:
                    patch["heading_deg"] = bearing_deg((ev.lat, ev.lon),
                                                       (ev.dest_lat, ev.dest_lon))
            elif ev.heading_deg is not None:
                patch["heading_deg"] = ev.heading_deg
        else:
            # out-of-order older event: keep position, just fill a missing one
            if c["centroid_lat"] is None and ev.lat is not None:
                patch["centroid_lat"] = ev.lat
                patch["centroid_lon"] = ev.lon

        self.db.update_cluster(cid, patch)
