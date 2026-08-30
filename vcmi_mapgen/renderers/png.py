"""PngRenderer — render a MapState level to a PIL Image using H3 sprites."""
from __future__ import annotations

import os
import pathlib

from vcmi_mapgen.pipeline import MapState


ROOT = pathlib.Path(__file__).parent.parent.parent


class PngRenderer:
    """Render a MapState to editor-quality 32px H3 sprite PNGs.

    Usage::

        renderer = PngRenderer(out_dir="out/render/pp")
        img = renderer.render(state, level=0)          # returns PIL Image
        path = renderer.save(state, "mymap.png")       # saves and returns path
    """

    def __init__(self, out_dir: str | None = None) -> None:
        self.out_dir = out_dir or str(ROOT / "out" / "render" / "pp")

    def render(self, state: MapState, level: int = 0, title: str = ""):
        """Return a PIL Image for the given level."""
        from vcmi_mapgen import render_editor as RED
        surfs = state.surfs.get(level)
        if surfs is None:
            raise ValueError(f"state.surfs has no level {level}")
        if level == 0:
            objs = [o for o in state.objs if o.get("l", 0) == 0]
        else:
            # render_editor draws only l==0 objects; shift underground to l=0
            objs = [dict(o, l=0) for o in state.objs if o.get("l", 0) == level]
        return RED.render_map(surfs, objs, title=title)

    def save(self, state: MapState, path: str, level: int = 0,
             title: str = "") -> str:
        """Render and save to *path*. Returns the resolved path."""
        if not os.path.isabs(path):
            path = os.path.join(self.out_dir, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = self.render(state, level=level, title=title)
        img.save(path)
        return path
