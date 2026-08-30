"""VmapReader — load a .vmap file into a MapState for rendering."""
from __future__ import annotations

import json
import re
import zipfile

from vcmi_mapgen.pipeline import MapState


def _relaxed(text: str):
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


class VmapReader:
    """Load a VCMI .vmap file into a MapState with surfs, cells, objs, and size.

    The resulting MapState can be passed directly to PngRenderer (with overlays)
    or VmapRenderer for a round-trip re-export.  Fields not stored in a .vmap
    (zones, gate_*, player_towns, ledger, …) are left at their dataclass defaults.

    Usage::

        state = VmapReader().read("out/vmap/mymap.vmap")
        img = PngRenderer().render(state, level=0)
    """

    def read(self, path: str) -> MapState:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            header = _relaxed(z.read("header.json").decode())
            surf = _relaxed(z.read("surface_terrain.json").decode())
            under = (
                _relaxed(z.read("underground_terrain.json").decode())
                if "underground_terrain.json" in names
                else None
            )
            objs = _relaxed(z.read("objects.json").decode("utf-8", "replace"))

        state = MapState()
        state.surfs = {0: surf}
        state.cells = {0: surf}
        if under is not None:
            state.surfs[1] = under
            state.cells[1] = under
        state.objs = objs

        # infer size from the surface grid
        if surf and surf[0]:
            state.size = max(len(surf[0]), len(surf))

        # subterrain flag
        state.subterrain = under is not None

        # carry header name if available
        if isinstance(header, dict):
            state.extras["vmap_name"] = header.get("name", "")

        return state
