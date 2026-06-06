"""Step 1: extract all .h3m into the faithful representation (out/faithful/*.json)."""

import glob, os, sys, re, json

sys.path.insert(0, "src")
import h3m, vcmi_ids, h3m2vmap as HV

ROOT = "/mnt/data/workspace/vcmi-mapgen"
OUT = f"{ROOT}/out/faithful"
os.makedirs(OUT, exist_ok=True)
maps = sorted(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/*.h3m"))
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
