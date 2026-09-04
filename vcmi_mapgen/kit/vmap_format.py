"""Compatibility shim over `kit.vmap` -- the tile/mask codecs re-export unchanged;
`read_raw`/`write_vmap` (opaque-template writer) stay here verbatim for the callers
not yet migrated onto `kit.vmap.reader`/`writer`'s structured `VmapDocument`. Retired
once every caller moves over (see the vmap-unification plan, Phase H).
"""
import copy
import json
import os
import zipfile

from vcmi_mapgen.kit.vmap.reader import _relaxed
from vcmi_mapgen.kit.vmap.terrain import (  # noqa: F401 (re-exported)
    RIVER,
    ROAD,
    TCODE,
    _mir,
    _trim_v,
    export_mask,
    tile_string,
    vcmi_mask,
    visitable_from,
)
from vcmi_mapgen.kit.vmap.writer import _name_struct


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


# write_vmap / read_raw are imported as a library by faithful.to_vmap.
