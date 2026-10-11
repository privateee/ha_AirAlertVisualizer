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
from .geo.launch import site_at
from .geo.util import bearing_deg, haversine_km
from .parse.pipeline import ParsedEvent
from .parse.threats import FAMILY, MAX_SPEED_KMH

MISSILE_FAMILIES = ("ballistic", "cruise")
MISSILE_UPDATE_S = 15 * 60       # a channel's follow-up on its missile, within this
DIRECTION_CONFLICT_DEG = 90.0    # stated headings further apart: different drones


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
        self, event_id: int, ev: ParsedEvent, channel: str, posted_at: str,
        follow: list[int] | None = None,
    ) -> int:
        """``follow``: clusters of the post this one replies to. A channel
        replying to its own report is saying that object moved on, so the
        event continues that track - no distance / speed gate (the channel
        knows it's the same drone), only a compatible type and a sane age."""
        window = timedelta(minutes=self.cfg.time_window_minutes)
        now = _dt(posted_at)
        since = (now - window).isoformat()

        if ev.threat_type == "clear":
            return self._resolve_nearby(ev, posted_at)

        family = FAMILY.get(ev.threat_type, "unknown")
        max_span = timedelta(minutes=self.cfg.max_span_minutes)

        for cid in follow or []:
            c = self.db.query_one("SELECT * FROM cluster WHERE id=?", (cid,))
            if c is None or c["resolved_at"] is not None:
                continue
            cf = FAMILY.get(c["threat_type"], "unknown")
            if cf != family and "unknown" not in (cf, family):
                continue                     # a missile reply to a drone report
            if abs((now - _dt(c["last_posted_at"])).total_seconds()) > 3 * window.total_seconds():
                continue
            self._attach(cid, event_id, ev, channel, posted_at)
            return cid

        # the post this event came from - same for every candidate cluster
        mine = (self.db.query_one("SELECT raw_message_id FROM event WHERE id=?", (event_id,))
                if ev.lat is not None else None)
        raw_id = mine["raw_message_id"] if mine is not None else None

        best_id: int | None = None
        best_score = 0.0
        for c in self.db.open_clusters(since):
            if c["resolved_at"] is not None:
                continue
            if self.cfg.incompatible_split and FAMILY.get(c["threat_type"]) != family:
                continue
            gap = abs((now - _dt(c["last_posted_at"])).total_seconds())
            if gap > window.total_seconds():
                continue
            if now - _dt(c["first_posted_at"]) > max_span:
                continue
            if family in MISSILE_FAMILIES:
                score = self._missile_score(ev, c, channel, gap)
                if score > best_score:
                    best_score, best_id = score, c["id"]
                continue
            score = self._score(ev, c, family, channel, gap / 60.0)
            if score > 0:
                score = self._same_channel_adjust(score, c, ev, event_id, raw_id, channel, now)
            if score > best_score:
                best_score, best_id = score, c["id"]

        if best_id is not None and best_score >= 0.5:
            self._attach(best_id, event_id, ev, channel, posted_at)
            return best_id
        return self._create(event_id, ev, channel, posted_at)

    def _resolve_nearby(self, ev: ParsedEvent, posted_at: str) -> int:
        """An 'all clear' at a place marks open clusters there as resolved."""
        if ev.lat is None:
            return -1
        window = timedelta(minutes=self.cfg.time_window_minutes * 2)
        since = (_dt(posted_at) - window).isoformat()
        for c in self.db.open_clusters(since):
            if c["resolved_at"] is not None or c["centroid_lat"] is None:
                continue
            same = c["place_name"] and c["place_name"] == ev.place_name
            near = haversine_km((ev.lat, ev.lon),
                                (c["centroid_lat"], c["centroid_lon"])) <= self.cfg.distance_km
            if same or near:
                self.db.update_cluster(c["id"], {"resolved_at": posted_at})
        return -1

    # -- scoring --------------------------------------------------------
    def _missile_score(self, ev: ParsedEvent, c, channel: str, gap_s: float) -> float:
        """Ballistic / cruise missiles, reported as threat -> course -> descent:

        * the same launch area from several channels ("Загроза балістики з
          Курська", "Курськ ОТРК!") is one threat;
        * the same channel's next post within a few minutes updates *its*
          missile ("… далі Київ", "Київ — спуск балістики!") rather than
          adding a point;
        * after "спуск" / impact the missile is done - its trajectory is final
          and a further "спуск" ("Третя") is another missile. Only another
          channel reporting the same descent right away joins it.
        """
        same_channel = channel in json.loads(c["channels"])
        same_place = bool(ev.place_name) and ev.place_name == c["place_name"]
        if c["status"] == "descending":
            # "💥 Вибухи в Києві" right after "Київ — спуск": that missile hit
            if ev.status == "impact" and same_place and gap_s <= 300:
                return 0.9
            # the same descent from another channel
            return 0.9 if (not same_channel and gap_s <= 180 and same_place) else 0.0
        if c["status"] == "impact":
            return 0.0
        ev_site = site_at(ev.lat, ev.lon)
        c_site = site_at(c["centroid_lat"], c["centroid_lon"])
        if ev_site is not None:
            # a report from a launch area is a launch: the same one reported
            # again (any channel), or a new missile - never the follow-up of
            # one already on its way ("Вихід Циркону Курськ" after "2х Циркони
            # далі Київ" is the next launch, not those two turning back)
            return 0.9 if ev_site == c_site else 0.0
        if same_channel and gap_s <= MISSILE_UPDATE_S:
            # the channel's follow-up goes to its most recent missile
            return 0.85 - gap_s / 100000.0
        if ev.place_name and ev.place_name == c["place_name"] and gap_s <= 300:
            return 0.8
        return 0.0

    def _same_channel_adjust(
        self, score: float, c, ev: ParsedEvent, event_id: int, raw_id: int | None,
        channel: str, now: datetime,
    ) -> float:
        """Check ``ev`` against this *channel's own* last fix in cluster ``c``.

        A channel reporting an attack over a city posts a fix every minute or
        two ("🅿️1х Виноградар", "🅿️1х Пріорка", ...). Its consecutive fixes are
        one consistent observer, so:

        * two different places in the *same post* are two objects;
        * a fix out of reach of its previous one (speed x elapsed) is a
          second object, even inside the 20 km "close by" radius that merges
          reports from *different* channels;
        * a fix within reach continues that channel's track - a small bonus,
          so it sticks to its own track rather than a neighbouring one.
        """
        if ev.lat is None:
            return score
        last = self.db.query_one(
            "SELECT lat, lon, posted_at, raw_message_id, place_name FROM event "
            "WHERE cluster_id=? AND channel=? AND lat IS NOT NULL AND id<>? "
            "ORDER BY posted_at DESC, id DESC LIMIT 1",
            (c["id"], channel, event_id),
        )
        if last is None:
            return score
        d = haversine_km((last["lat"], last["lon"]), (ev.lat, ev.lon))
        if (raw_id is not None and raw_id == last["raw_message_id"]
                and last["place_name"] != ev.place_name):
            return 0.0
        speed = max(self._reach_kmh(ev.threat_type), self._reach_kmh(c["threat_type"]))
        gap_h = max(0.0, (now - _dt(last["posted_at"])).total_seconds() / 3600.0)
        reach = max(self.cfg.same_channel_floor_km, speed * gap_h)
        if d > reach:
            return 0.0
        return min(0.99, score + 0.04)

    def _reach_kmh(self, slug: str) -> float:
        """Fastest this type plausibly covers ground: cruise x slack, or its
        top speed where that's higher (a jet UAV's 900 km/h dash)."""
        cruise = self.cfg.speed_kmh.get(slug, 220.0) * self.cfg.speed_slack
        return max(cruise, MAX_SPEED_KMH.get(slug, 0.0))

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
            # A report that states its own direction ("з Чернігівщини на
            # Вишгородський район", coming from the north) is a different drone
            # than a track flying another way ("повз Переяслав курсом на Київ",
            # north-west) - however reachable and "downrange" the place looks.
            if (ev.heading_deg is not None and c["heading_deg"] is not None and d > 5.0
                    and _angdiff(ev.heading_deg, c["heading_deg"]) > DIRECTION_CONFLICT_DEG):
                return 0.0
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
        reach = self._reach_kmh(ev.threat_type) * (gap_min / 60.0)
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
            "altitude_m": ev.altitude_m,
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
            if ev.altitude_m is not None:
                patch["altitude_m"] = ev.altitude_m
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
