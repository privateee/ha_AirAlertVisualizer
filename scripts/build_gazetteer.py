"""Expand the seed gazetteer with GeoNames data.

The bundled ``dronevis/geo/data/gazetteer.json`` is hand-made and Kyiv-centric.
This script pulls every populated place in Ukraine from GeoNames, keeps the
ones above a population threshold (plus all raion/oblast centres), and merges
them with the seed - seed entries win on conflicts so your curated aliases and
coordinates are preserved.

Usage:
    python scripts/build_gazetteer.py                 # download UA.zip, write merged file
    python scripts/build_gazetteer.py --min-pop 2000
    python scripts/build_gazetteer.py --source path/to/UA.txt --out custom.json

GeoNames dump format (tab separated), columns we use:
    1 geonameid  2 name  4 alternatenames  5 lat  6 lon
    7 feature_class  8 feature_code  11 admin1  15 population
Licence: GeoNames data is CC BY 4.0.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "dronevis" / "geo" / "data" / "gazetteer.json"
DUMP_URL = "https://download.geonames.org/export/dump/UA.zip"

# GeoNames admin1 code -> our short oblast key
ADMIN1_TO_OBL = {
    "01": "vinnytsia", "02": "volyn", "03": "luhansk", "04": "dnipro",
    "05": "donetsk", "06": "zhytomyr", "07": "zakarp", "08": "zapor",
    "09": "if", "10": "kyiv_o", "11": "kropyv", "13": "lviv", "14": "mykolaiv",
    "15": "odesa", "16": "poltava", "17": "rivne", "18": "sumy",
    "19": "ternopil", "20": "kharkiv", "21": "kherson", "22": "khmeln",
    "23": "cherkasy", "24": "chernihiv", "25": "chernivtsi", "26": "kyiv_c",
    "27": "crimea",
}
KEEP_CODES = {
    "PPLC": 6, "PPLA": 5, "PPLA2": 4, "PPLA3": 4, "PPLA4": 3,
    "PPL": 2, "PPLX": 1,
}


def load_dump(source: str | None) -> list[str]:
    if source:
        p = Path(source)
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                return z.read("UA.txt").decode("utf-8").splitlines()
        return p.read_text(encoding="utf-8").splitlines()
    print(f"downloading {DUMP_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(DUMP_URL) as r:  # noqa: S310 (trusted host)
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("UA.txt").decode("utf-8").splitlines()


def rank_for(code: str, population: int) -> int:
    base = KEEP_CODES.get(code, 0)
    if population >= 250_000:
        base = max(base, 5)
    elif population >= 50_000:
        base = max(base, 4)
    elif population >= 10_000:
        base = max(base, 3)
    return base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="local UA.txt or UA.zip instead of downloading")
    ap.add_argument("--min-pop", type=int, default=1000)
    ap.add_argument("--bbox", help="south,west,north,east - keep only inside")
    ap.add_argument("--out", default=str(SEED))
    args = ap.parse_args(argv)

    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    seed_places = seed["places"]
    seen = {p["name"].lower() for p in seed_places}

    added = 0
    for line in load_dump(args.source):
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        name, alt, lat, lon = cols[1], cols[3], cols[4], cols[5]
        fclass, fcode, admin1 = cols[6], cols[7], cols[10]
        population = int(cols[14] or 0)
        if fclass != "P" or fcode not in KEEP_CODES:
            continue
        rank = rank_for(fcode, population)
        if population < args.min_pop and rank < 4:
            continue
        try:
            latf, lonf = float(lat), float(lon)
        except ValueError:
            continue
        if bbox and not (bbox[0] <= latf <= bbox[2] and bbox[1] <= lonf <= bbox[3]):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        aliases = [a.strip() for a in alt.split(",") if a.strip() and a.isascii() is False][:6]
        seed_places.append({
            "name": name, "lat": round(latf, 4), "lon": round(lonf, 4),
            "kind": "city" if rank >= 4 else "town" if rank == 3 else "village",
            "obl": ADMIN1_TO_OBL.get(admin1, ""), "rank": rank,
            "aliases": aliases,
        })
        added += 1

    out = Path(args.out)
    out.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"merged: {len(seed_places)} places (+{added} from GeoNames) -> {out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
