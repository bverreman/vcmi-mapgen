"""Object-ADJACENCY GRAPH -- IMMEDIATE, TYPE-SPECIFIC, TERRAIN-CONDITIONED.

A zone's shape is which objects literally ABUT which, on which terrain. So:

  * neighbours = objects whose footprints touch (Chebyshev-1 around the footprint),
    not a radius cloud -- so growth places touching objects on free tiles without
    overlap, and we capture real "what's on the tile next to me".
  * keyed by the ANCHOR's terrain AND its specific TYPE -- a sawmill on grass, not
    "a MINE somewhere". Decoration is oakTrees / pineTrees / mountain, never the
    generic DECORATION bucket; the same specificity applies to every type.

Output (out/adjacency.json):
  profiles["<terrain>|<type>"] = {
     avg_degree,                      # how many objects touch one of these, typically
     neighbours: [ {type, share, offsets:[[dx,dy],...]} ]   # specific touching types
  }
"""

import sys, os, json, glob, collections, random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m, vcmi_ids, deps

MAX_OFF = 80
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def footprint(o, tmpl):
    """Tiles an object occupies: blocked body + anchor + visitable entry tiles.

    Including visit_mask tiles ensures that a guard placed at a mine's approach
    tile (2 tiles off, outside the blocked footprint) still counts as immediately
    touching the mine — recovering the mine→guard coupling lost by the strict
    immediate-touch graph.
    """
    s = set(deps.blocked_tiles(o, tmpl))
    s.add((o.x, o.y))
    for r in range(6):
        b = tmpl.visit_mask[r]
        for c in range(8):
            if (b >> (7 - c)) & 1:  # bit SET = visitable/approach tile
                s.add((o.x - (7 - c), o.y - (5 - r)))
    return s


def build(paths):
    random.seed(0)  # deterministic offset sampling so adjacency.json is reproducible
    model = collections.defaultdict(
        lambda: {
            "deg": [],
            "nbr": collections.Counter(),
            "off": collections.defaultdict(list),
        }
    )
    for pth in paths:
        try:
            m = h3m.parse_file(pth)
        except Exception:
            continue
        W, H = m.width, m.height
        for lvl in range(2 if m.two_level else 1):
            # resolve objects on this level: (anchor, type, terrain, footprint)
            objs = []
            for o in m.objects:
                if o.l != lvl:
                    continue
                r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
                if not r:
                    continue
                tmpl = m.templates[o.template_index]
                fp = footprint(o, tmpl)
                tr = m.terrain[lvl][o.y][o.x].terrain if 0 <= o.x < W and 0 <= o.y < H else -1
                objs.append((o.x, o.y, r[0], tr, fp))
            if len(objs) < 2:
                continue
            owner = {}
            for i, (x, y, t, tr, fp) in enumerate(objs):
                for c in fp:
                    owner[c] = i
            for i, (ax, ay, at, atr, fp) in enumerate(objs):
                # objects touching this footprint (immediate grid neighbours)
                touch = set()
                for fx, fy in fp:
                    for dx, dy in NB8:
                        j = owner.get((fx + dx, fy + dy))
                        if j is not None and j != i:
                            touch.add(j)
                key = f"{atr}|{at}"
                model[key]["deg"].append(len(touch))
                for j in touch:
                    bx, by, bt = objs[j][0], objs[j][1], objs[j][2]
                    model[key]["nbr"][bt] += 1
                    model[key]["off"][bt].append((bx - ax, by - ay))

    profiles = {}
    for key, d in model.items():
        total = sum(d["nbr"].values()) or 1
        nlist = []
        for t, c in d["nbr"].most_common(12):
            offs = d["off"][t]
            samp = random.sample(offs, min(MAX_OFF, len(offs)))
            nlist.append(
                {
                    "type": t,
                    "share": round(c / total, 3),
                    "offsets": [[dx, dy] for dx, dy in samp],
                }
            )
        profiles[key] = {
            "avg_degree": round(sum(d["deg"]) / max(1, len(d["deg"])), 2),
            "neighbours": nlist,
        }
    out = f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/out/adjacency.json"
    json.dump({"kind": "immediate-touch", "profiles": profiles}, open(out, "w"))
    return profiles, out


TN = {
    0: "dirt",
    1: "sand",
    2: "grass",
    3: "snow",
    4: "swamp",
    5: "rough",
    6: "subterr",
    7: "lava",
    8: "water",
    9: "rock",
    -1: "?",
}

if __name__ == "__main__":
    paths = sorted(
        glob.glob(
            "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m",
            recursive=True,
        )
    )
    prof, out = build(paths)
    print(
        f"immediate-touch adjacency over {len(paths)} maps -> {out}  ({len(prof)} (terrain|type) keys)\n"
    )
    for key in ["2|mine", "3|mine", "2|randomMonsterLevel3", "2|oakTrees", "2|town"]:
        if key not in prof:
            continue
        tr, ty = key.split("|")
        pr = prof[key]
        print(f"{TN.get(int(tr)):7}/{ty:20} avg_touch={pr['avg_degree']:.2f}  touches:")
        for e in pr["neighbours"][:6]:
            print(f"     {e['type']:22} {e['share']:5.2f}")
        print()
