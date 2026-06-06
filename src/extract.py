"""Extract normalized JSON per map + a validation-oracle report.

Usage:
    python3 extract.py

Writes:
    out/maps/<name>.json           one normalized record per map
    out/validation_report.json     per-map oracle results + summary

Also prints a final summary: oracle pass count, total objects parsed, and the
top-25 most common (objectClass, subclass) pairs across the corpus.
"""

from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import struct
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3m  # noqa: E402

MAPS_GLOB = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/*.h3m"
OUT_DIR = "/mnt/data/workspace/vcmi-mapgen/out"
MAPS_OUT_DIR = os.path.join(OUT_DIR, "maps")

# Short terrain codes (H3M terrain type index -> code). Raw int is also kept.
TERRAIN_CODES = {
    0: "dirt",
    1: "sand",
    2: "grass",
    3: "snow",
    4: "swamp",
    5: "rough",
    6: "subterra",
    7: "lava",
    8: "water",
    9: "rock",
}


def map_to_json(m: h3m.H3Map) -> dict:
    terrain = []
    for level in m.terrain:
        rows = []
        for row in level:
            rows.append(
                [
                    {
                        "t": tile.terrain,
                        "river": tile.river,
                        "road": tile.road,
                    }
                    for tile in row
                ]
            )
        terrain.append(rows)

    objects = [
        {
            "x": o.x,
            "y": o.y,
            "l": o.l,
            "class": o.obj_class,
            "subclass": o.obj_subclass,
            "animation": o.animation,
            "footprint": o.footprint,
        }
        for o in m.objects
    ]

    return {
        "name": m.name,
        "format": m.fmt,
        "width": m.width,
        "height": m.height,
        "twoLevel": m.two_level,
        "players": m.players,
        "terrain": terrain,
        "objects": objects,
    }


def main() -> int:
    os.makedirs(MAPS_OUT_DIR, exist_ok=True)
    files = sorted(glob.glob(MAPS_GLOB))

    report = []
    class_counter: collections.Counter = collections.Counter()
    total_objects = 0
    passed = 0

    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        raw = open(path, "rb").read()
        data = gzip.decompress(raw)
        fmt = struct.unpack_from("<I", data, 0)[0]

        entry = {
            "filename": os.path.basename(path),
            "format": fmt,
        }
        try:
            m = h3m.H3MParser(data).parse(name)
            xy_ok = all(0 <= o.x < m.width + 8 and 0 <= o.y < m.height + 8 for o in m.objects)
            clean = (m.bytes_remaining == 0 or m.remaining_all_zero) and xy_ok

            entry.update(
                {
                    "width": m.width,
                    "height": m.height,
                    "twoLevel": m.two_level,
                    "templates": len(m.templates),
                    "objects": len(m.objects),
                    "bytes_remaining": m.bytes_remaining,
                    "remaining_all_zero": m.remaining_all_zero,
                    "xy_in_range": xy_ok,
                    "oracle_pass": clean,
                }
            )

            if clean:
                passed += 1

            total_objects += len(m.objects)
            for o in m.objects:
                class_counter[(o.obj_class, o.obj_subclass)] += 1

            with open(os.path.join(MAPS_OUT_DIR, name + ".json"), "w") as fh:
                json.dump(map_to_json(m), fh)

        except Exception as exc:  # pragma: no cover - we want this reported, not swallowed
            entry.update(
                {
                    "oracle_pass": False,
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
            )

        report.append(entry)

    top25 = class_counter.most_common(25)

    summary = {
        "total_maps": len(files),
        "passed": passed,
        "summary_line": f"{passed}/{len(files)} clean",
        "total_objects": total_objects,
        "top25_class_subclass": [{"class": c, "subclass": s, "count": n} for (c, s), n in top25],
    }

    with open(os.path.join(OUT_DIR, "validation_report.json"), "w") as fh:
        json.dump({"summary": summary, "maps": report}, fh, indent=2)

    # ---- final printout ----
    print(f"Oracle: {passed}/{len(files)} maps pass (clean EOF, zero-padding only)")
    print(f"Total objects parsed: {total_objects}")
    print()
    print("Top 25 (objectClass, subclass) pairs:")
    print(f"  {'class':>6} {'subclass':>9} {'count':>8}")
    for (c, s), n in top25:
        print(f"  {c:>6} {s:>9} {n:>8}")

    failures = [e for e in report if not e.get("oracle_pass")]
    if failures:
        print()
        print("FAILURES:")
        for e in failures:
            print(f"  {e['filename']}: {e.get('error', 'nonzero tail / xy out of range')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
