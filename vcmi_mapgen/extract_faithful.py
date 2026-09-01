"""Regenerate the faithful map JSON the engine consumes.

Reads the committed .h3m corpus at <repo>/maps/ and writes the faithful
representation to <repo>/maps_json/<name>.json (object identity + exact mask +
writer-ready terrain). Run: `uv run python -m vcmi_mapgen.extract_faithful`.
"""

import glob, os, re, json

from vcmi_mapgen import h3m
from vcmi_mapgen.kit import vcmi_config as vcmi_ids
from vcmi_mapgen import h3m2vmap as HV
from vcmi_mapgen.kit.paths import project_root

ROOT = project_root()
OUT = str(ROOT / "maps_json")
os.makedirs(OUT, exist_ok=True)
maps = sorted(glob.glob(f"{ROOT}/maps/*.h3m"))
ok = 0
unresolved = 0
total_obj = 0
for p in maps:
    try:
        m = h3m.parse_file(p)
    except Exception as e:
        print("PARSE FAIL", os.path.basename(p), e)
        continue
    terr = [
        [
            [
                {
                    "t": t.terrain,
                    "view": t.view,
                    "rt": t.river_type,
                    "rd": t.river_dir,
                    "ot": t.road_type,
                    "od": t.road_dir,
                    "m": t.mirror,
                }
                for t in row
            ]
            for row in lvl
        ]
        for lvl in m.terrain
    ]
    objs = []
    for o in m.objects:
        r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        tmpl = m.templates[o.template_index]
        if not r:
            unresolved += 1
        objs.append(
            {
                "x": o.x,
                "y": o.y,
                "l": o.l,
                "cls": o.obj_class,
                "sub": o.obj_subclass,
                "type": r[0] if r else None,
                "subtype": r[1] if r else None,
                "animation": re.sub(r"\.(def|DEF)$", "", o.animation),
                "mask": HV.build_mask(tmpl.block_mask, tmpl.visit_mask),
            }
        )
    total_obj += len(objs)
    fm = {
        "name": m.name,
        "width": m.width,
        "height": m.height,
        "twoLevel": m.two_level,
        "players": m.players,
        "terrain": terr,
        "objects": objs,
    }
    json.dump(fm, open(f"{OUT}/{os.path.basename(p)[:-4]}.json", "w"))
    ok += 1
print(
    f"extracted {ok}/{len(maps)} maps, {total_obj} objects, {unresolved} unresolved ({100 * unresolved / max(1, total_obj):.2f}%)"
)
