"""The .vmap container format: zip/JSON I/O, plus the tile-string and template-mask
encoding VCMI's 1.7.4 editor expects.

Strategy: clone a real RMG vmap's header.json as a template and patch only the
map dimensions / level count / name. Terrain is a list (per level) of 2D arrays of
VCMI tile strings (e.g. 'gr0_'); objects is a list of vmap object dicts
(instanceName/type/subtype/template/x/y/l). VCMI's JSON reader accepts strict JSON.
"""

import zipfile, re, json, os, copy

from vcmi_mapgen import ontology

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


def _mir(m):
    h, v = m & 1, m & 2
    return "+" if (h and v) else "|" if v else "-" if h else "_"


def tile_string(c):
    s = f"{TCODE.get(c['t'], 'gr')}{c['view']}{_mir(c.get('m', 0))}"
    if c.get("rt"):
        s += f"{RIVER.get(c['rt'], 'clrv')}{c.get('rd', 0)}_"
    if c.get("ot"):
        s += f"{ROAD.get(c['ot'], 'dirtrd')}{c.get('od', 0)}_"
    return s


def visitable_from(mask):
    """The 3x3 approach grid VCMI needs for visitable templates. Buildings (have
    blocked body) are entered from the sides/below; free-standing pickups/monsters
    from all 8 directions. None for pure decoration (no visitable tile)."""
    if not any(ch in "AX" for r in mask for ch in r):     # 'A' or 'X' = a visitable tile
        return None
    if any(ch in "BX" for r in mask for ch in r):         # has a blocked body -> a building
        return ["---", "+-+", "+++"]
    return ["+++", "+-+", "+++"]


def vcmi_mask(mask):
    """Translate an engine-internal mask to VCMI's template charset for .vmap export.

    Our masks use 'X' for a blocked ENTRANCE cell (entered from below). VCMI's
    ObjectTemplate parser only knows ' 0VBHAT' and logs "Unrecognized char X in template
    mask", dropping the cell to FREE — which made every mine/town silently unvisitable
    in-game (no VISITABLE cell survived). VCMI's 'A' = VISIBLE|BLOCKED|VISITABLE is the
    exact semantic of our 'X' (and of our walk-on 'A' — monsters/pickups are
    blocked-visitable in H3), so both map onto it."""
    return [row.replace("X", "A") for row in mask]


def _trim_v(mask):
    """The mask minus its all-'V' border rows/columns — the footprint core two masks must
    share to be the same object shape."""
    rows = [i for i, r in enumerate(mask) if set(r) - {"V"}]
    cols = [c for c in range(len(mask[0])) if any(row[c] != "V" for row in mask)]
    if not rows or not cols:
        return ["V"]
    return [mask[i][min(cols):max(cols) + 1] for i in range(min(rows), max(rows) + 1)]


def export_mask(o):
    """The template mask written to the .vmap: the ontology's sprite-extent mask
    (`vmap_mask_of` — VCMI draws an object only on mask-covered tiles, so the bbox-trimmed
    internal mask truncates tall sprites in-game) when its footprint core agrees with the
    instance mask; otherwise the instance mask translated to VCMI's charset (the editor
    table and a map instance legitimately disagree for a handful of corpus dwellings)."""
    inst = vcmi_mask(o["mask"])
    vm = ontology.vmap_mask_of(o["animation"])
    return vm if vm and _trim_v(vm) == _trim_v(inst) else inst


def _relaxed(t):
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


def read_raw(path):
    """Load the exact JSON components of a real vmap (for templating / round-trip)."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    header = _relaxed(z.read("header.json").decode("utf-8", "replace"))
    surf = _relaxed(z.read("surface_terrain.json").decode())
    under = (
        _relaxed(z.read("underground_terrain.json").decode())
        if "underground_terrain.json" in names
        else None
    )
    objs = _relaxed(z.read("objects.json").decode("utf-8", "replace"))
    return header, surf, under, objs


def _name_struct(s):
    return {
        "exactStrings": [s],
        "localStrings": None,
        "message": [0],
        "numbers": None,
        "stringsTextID": None,
    }


def write_vmap(path, header_template, terrain_levels, objects_raw, name="vcmi-mapgen"):
    """terrain_levels: [surface_grid] or [surface_grid, underground_grid]; grids are 2D str arrays."""
    h = copy.deepcopy(header_template)
    two = len(terrain_levels) > 1
    height = len(terrain_levels[0])
    width = len(terrain_levels[0][0])
    ml = {
        "surface": {
            "height": height,
            "index": 0,
            "layer": "core:surface",
            "width": width,
        }
    }
    if two:
        ml["underground"] = {
            "height": len(terrain_levels[1]),
            "index": 1,
            "layer": "core:underground",
            "width": len(terrain_levels[1][0]),
        }
    h["mapLevels"] = ml
    h["name"] = _name_struct(name)
    files = {
        "header.json": json.dumps(h, indent=1),
        "surface_terrain.json": json.dumps(terrain_levels[0]),
        "objects.json": json.dumps(objects_raw, indent=1),
    }
    if two:
        files["underground_terrain.json"] = json.dumps(terrain_levels[1])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in files.items():
            z.writestr(n, data)
    return path


# write_vmap / read_raw are imported as a library by faithful.to_vmap and h3m2vmap.
# The former round-trip self-test depended on the removed vmaplib experiment.
