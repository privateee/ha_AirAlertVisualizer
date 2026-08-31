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
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

_CYR_NAME = re.compile(r"^[А-ЯЁІЇЄҐ][А-Яа-яЁёІіЇїЄєҐґ'’ \-]{1,40}$")
# rough Cyrillic -> Latin so we can score a Cyrillic alt against the GeoNames
# ascii name and pick the true transliteration, not a poetic alternate
_TR = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "",
    "-": "", " ": "",
}


def _translit(s: str) -> str:
    return "".join(_TR.get(c, c) for c in s.lower())


def _similar(a: str, b: str) -> float:
    a, b = _translit(a), b.lower().replace("'", "").replace("-", "").replace(" ", "")
    n = min(len(a), len(b))
    if not n:
        return 0.0
    common = sum(1 for i in range(n) if a[i] == b[i])
    return common / max(len(a), len(b))


def pick_cyrillic(primary: str, ascii_name: str, alt_field: str) -> tuple[str, list[str]]:
    """(display_name, aliases). GeoNames col-1 is the Latin transliteration for
    UA; the Cyrillic form is in alternatenames along with poetic/historic
    alternates. Pick the Cyrillic alt that best transliterates back to the
    ascii name; keep only close Cyrillic alts as aliases (no 'Південна
    Пальміра' junk)."""
    alts = [a.strip() for a in alt_field.split(",") if a.strip()]
    cyr = [a for a in alts if _CYR_NAME.match(a)]
    scored = sorted(((_similar(a, ascii_name or primary), a) for a in cyr), reverse=True)
    if scored and scored[0][0] >= 0.55:
        name = scored[0][1]
    else:
        return primary, [ascii_name] if ascii_name and ascii_name != primary else []
    aliases, seen = [], {name.lower()}
    for _, a in scored[1:]:
        if _similar(a, _translit(name)) >= 0.6 and a.lower() not in seen:
            seen.add(a.lower())
            aliases.append(a)
    return name, aliases[:5]

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "dronevis" / "geo" / "data" / "gazetteer.seed.json"
OUT_DEFAULT = ROOT / "dronevis" / "geo" / "data" / "gazetteer.json"
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


def _ssl_ctx():
    import ssl
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def load_dump(source: str | None) -> list[str]:
    if source:
        p = Path(source)
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                return z.read("UA.txt").decode("utf-8").splitlines()
        return p.read_text(encoding="utf-8").splitlines()
    print(f"downloading {DUMP_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(DUMP_URL, context=_ssl_ctx()) as r:  # noqa: S310
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
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args(argv)

    _OBL_UK = {
        "vinnytsia": "Вінницька", "volyn": "Волинська", "luhansk": "Луганська",
        "dnipro": "Дніпропетровська", "donetsk": "Донецька", "zhytomyr": "Житомирська",
        "zakarp": "Закарпатська", "zapor": "Запорізька", "if": "Івано-Франківська",
        "kyiv_o": "Київська", "kropyv": "Кіровоградська", "lviv": "Львівська",
        "mykolaiv": "Миколаївська", "odesa": "Одеська", "poltava": "Полтавська",
        "rivne": "Рівненська", "sumy": "Сумська", "ternopil": "Тернопільська",
        "kharkiv": "Харківська", "kherson": "Херсонська", "khmeln": "Хмельницька",
        "cherkasy": "Черкаська", "chernivtsi": "Чернівецька", "chernihiv": "Чернігівська",
        "kyiv_c": "Київ", "crimea": "Крим",
    }
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    seed_places = seed["places"]
    seed_names = {p["name"].lower() for p in seed_places}

    # folded keys the seed already owns - GeoNames aliases must not shadow them
    # (e.g. a village "Славуціч" whose alt-name list contains "Славутич")
    def _fold(s: str) -> str:
        s = s.lower().translate(str.maketrans({
            "і": "и", "ї": "и", "ы": "и", "є": "е", "э": "е", "ґ": "г",
            "'": "", "’": "", "ь": "", "ъ": "",
        }))
        return re.sub(r"\s+", " ", s).strip()

    def _forms(f: str) -> set[str]:
        out = {f}
        last = f.rsplit(" ", 1)[-1]
        if len(last) >= 5 and last[-1] in "аяое":
            stem = f[:-1]
            out |= {stem + c for c in "иуіе"}
            if last.endswith("я"):
                out |= {stem + "ю", stem + "и"}
        return out

    seed_keys = set()
    for p in seed_places:
        for s in [p["name"], *p.get("aliases", [])]:
            seed_keys |= _forms(_fold(re.sub(r"\s*\([^)]*\)", "", s)))
    by_name: dict[str, list[dict]] = {}

    # oblast is assigned by nearest oblast centre (GeoNames' admin1 codes in the
    # dump are unreliable). Uses the seed's rank-5 UA entries.
    def _hav(a, b):
        import math
        la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
        h = (math.sin((la2 - la1) / 2) ** 2
             + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
        return 2 * 6371 * math.asin(math.sqrt(h))

    centres = [(p["obl"], (p["lat"], p["lon"])) for p in seed_places
               if p.get("rank") == 5 and p.get("obl") and not p["obl"].startswith(("ru_", "by_"))]

    def nearest_obl(lat, lon):
        return min(centres, key=lambda c: _hav((lat, lon), c[1]))[0] if centres else ""

    added = 0
    for line in load_dump(args.source):
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        name, alt, lat, lon = cols[1], cols[3], cols[4], cols[5]
        fclass, fcode = cols[6], cols[7]
        population = int(cols[14] or 0)
        if fclass != "P" or fcode not in KEEP_CODES:
            continue
        rank = rank_for(fcode, population)
        # towns and up only - villages add ambiguity, not real coverage
        if rank < 3:
            continue
        try:
            latf, lonf = float(lat), float(lon)
        except ValueError:
            continue
        if bbox and not (bbox[0] <= latf <= bbox[2] and bbox[1] <= lonf <= bbox[3]):
            continue
        disp, aliases = pick_cyrillic(name, cols[2], alt)
        if disp.lower() in seed_names or _fold(disp) in seed_keys:
            continue                       # hand-curated seed always wins
        aliases = [a for a in aliases if _fold(a) not in seed_keys]
        obl = nearest_obl(latf, lonf)
        rec = {
            "name": disp, "lat": round(latf, 4), "lon": round(lonf, 4),
            "kind": "city" if rank >= 4 else "town" if rank == 3 else "village",
            "obl": obl, "rank": rank, "aliases": aliases,
        }
        by_name.setdefault(disp.lower(), []).append(rec)
        added += 1

    # same name in several oblasts -> keep all, oblast-suffix the name so the
    # resolver disambiguates by post header / proximity (like the seed does)
    for recs in by_name.values():
        if len(recs) > 1:
            for r in recs:
                suffix = _OBL_UK.get(r["obl"])
                if suffix:
                    r["name"] = f"{r['name']} ({suffix})"
        seed_places.extend(recs)

    out = Path(args.out)
    out.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"merged: {len(seed_places)} places (+{added} from GeoNames) -> {out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
