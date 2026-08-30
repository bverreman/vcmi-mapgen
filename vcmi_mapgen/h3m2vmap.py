"""Faithful h3m -> .vmap converter using VCMI's OWN config for object identifiers
(src/vcmi_ids.py) -- no reverse-engineering. Round-trips a real map so it can be
opened in the editor and confirmed faithful, then trusted as the reference.
"""

import json, glob, os, sys, re, collections

from vcmi_mapgen import h3m, vcmi_ids, vmapwrite, vcmi_paths
from vcmi_mapgen.vcmi_paths import project_root

ROOT = project_root()

TCODE = {
    0: "dt",
    1: "sa",
    2: "gr",
    3: "sn",
    4: "sw",
    5: "rg",
    6: "sb",
    7: "lv",
    8: "wt",
    9: "rc",
}
RIVER = {1: "clrv", 2: "icyrv", 3: "mudrv", 4: "lavrv"}
ROAD = {1: "dirtrd", 2: "gravrd", 3: "cobbrd"}


def mirror_suffix(m):
    h, v = m & 1, m & 2
    return "+" if (h and v) else "|" if v else "-" if h else "_"


def tile_string(t):
    s = f"{TCODE.get(t.terrain, 'gr')}{t.view}{mirror_suffix(t.mirror)}"
    if t.river_type:
        s += f"{RIVER.get(t.river_type, 'clrv')}{t.river_dir}_"
    if t.road_type:
        s += f"{ROAD.get(t.road_type, 'dirtrd')}{t.road_dir}_"
    return s


def build_mask(block_mask, visit_mask):
    # 6 rows x 8 cols. block bit 1=passable/0=blocked; visit bit 1=visitable. A cell carries TWO
    # independent bits -> four states: 'B' blocked, 'V' passable, 'A' passable+visitable (stand on),
    # 'X' blocked+visitable (a building's action tile -- visited from an adjacent tile).
    # The mask is anchored bottom-right: bit b of row byte r is the tile at column b counted
    # from the RIGHT edge (VCMI: usedTiles[5-i][7-j]). Reading bit (7-c) into column c mirrors
    # every asymmetric footprint horizontally (the v5.2 sawmill-entrance bug) — bit c is the
    # correct read for a left-to-right row. Kept in sync with ontology._decode_mask.
    grid = [["V"] * 8 for _ in range(6)]
    for r in range(6):
        for c in range(8):
            blocked = not (block_mask[r] >> c) & 1
            visit = (visit_mask[r] >> c) & 1
            grid[r][c] = ("X" if blocked else "A") if visit else ("B" if blocked else "V")
    # In H3 EVERY visitable tile is also flagged blocked, so a lone pickup (resource/chest/monster)
    # looks identical to a building's gate. Distinguish by the solid BODY: only an object that has
    # pure-blocked ('B') body cells keeps its visit tile blocked ('X', visited from adjacent); a
    # bodyless single visit tile is a walk-onto pickup -> 'A' (passable). Restores passability.
    if not any(grid[r][c] == "B" for r in range(6) for c in range(8)):
        for r in range(6):
            for c in range(8):
                if grid[r][c] == "X":
                    grid[r][c] = "A"
    rows = [r for r in range(6) if any(ch != "V" for ch in grid[r])]
    cols = [c for c in range(8) if any(grid[r][c] != "V" for r in range(6))]
    if not rows:
        return ["B"]
    return [
        "".join(grid[r][c] for c in range(min(cols), max(cols) + 1))
        for r in range(min(rows), max(rows) + 1)
    ]


def convert(h3m_path, out_path):
    m = h3m.parse_file(h3m_path)
    levels = [[[tile_string(t) for t in row] for row in lvl] for lvl in m.terrain]
    objs = []
    skip = collections.Counter()
    n = 0
    for o in m.objects:
        r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        if not r:
            skip[o.obj_class] += 1
            continue
        vtype, sub = r
        anim = re.sub(r"\.(def|DEF)$", "", o.animation)
        tmpl = m.templates[o.template_index]
        n += 1
        objs.append(
            {
                "instanceName": f"{vtype}_{n}",
                "l": o.l,
                "type": vtype,
                "subtype": sub,
                "template": {
                    "animation": anim,
                    "editorAnimation": "",
                    "mask": build_mask(tmpl.block_mask, tmpl.visit_mask),
                },
                "x": o.x,
                "y": o.y,
            }
        )
    _rmg = glob.glob(os.path.join(vcmi_paths.vcmi_home(), "Maps", "RandomMaps", "*.vmap"))
    if _rmg:
        header, _, _, _ = vmapwrite.read_raw(_rmg[0])
    else:
        _tpl = str(ROOT / "data" / "vmap_header_template.json")
        header = json.load(open(_tpl))
    for pid, pl in list(header.get("players", {}).items()):
        if isinstance(pl, dict):
            pl["mainTown"] = None
    vmapwrite.write_vmap(
        out_path,
        header,
        levels,
        objs,
        name=os.path.basename(h3m_path).replace(".h3m", ""),
    )
    print(
        f"converted {os.path.basename(h3m_path)}  {m.width}x{m.height} 2lvl={m.two_level}  objects={len(objs)}  skipped={sum(skip.values())} {dict(skip)}"
    )
    return out_path


if __name__ == "__main__":
    src = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(vcmi_paths.vcmi_home(), "Maps", "Elbow Room.h3m")
    )
    out = str(ROOT / "out" / f"REAL_{os.path.basename(src).replace('.h3m', '')}.vmap")
    convert(src, out)
