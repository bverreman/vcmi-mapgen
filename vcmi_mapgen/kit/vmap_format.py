"""Write a VCMI .vmap (zip of JSON) the 1.7.4 editor will open.

Strategy: clone a real RMG vmap's header.json as a template and patch only the
map dimensions / level count / name. Terrain is a list (per level) of 2D arrays of
VCMI tile strings (e.g. 'gr0_'); objects is a list of vmap object dicts
(instanceName/type/subtype/template/x/y/l). VCMI's JSON reader accepts strict JSON.
"""

import zipfile, re, json, os, copy


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
#
# faithful.py's atomic tile/mask string encoders (TCODE, RIVER, ROAD, tile_string, ...)
# join this module in a later refactor phase — they're the other half of ".vmap
# container + its tile-string encoding" but move separately to keep this relocation
# mechanical.
