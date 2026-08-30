"""PocketOverlay — magenta gradient over guard-sealed pocket regions."""
from __future__ import annotations

import collections
import colorsys

from PIL import Image, ImageDraw

from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen.renderers.overlays.base import MapOverlay, TILE

_POCKET_MIN = 2
_POCKET_MAX = 16
_WATER = 8
_ROCK = 9
_NB8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


class PocketOverlay(MapOverlay):
    """Highlight sealed pocket regions in a magenta depth gradient.

    A pocket is a small (2–16 tile) passable region reachable through a unique
    1- or 2-tile entrance — the exact definition used by the zone overlay tool.
    Darker magenta = near the entrance, lighter = deep inside.

    Reads ``state.objs`` for object footprints and ``state.surfs``/``state.cells``
    for terrain.  Works with both pipeline-generated states and VmapReader output
    (provided objects carry ``template.mask``).

    Args:
        min_tiles: minimum pocket size (default 2).
        max_tiles: maximum pocket size (default 16).
    """

    def __init__(self, min_tiles: int = _POCKET_MIN, max_tiles: int = _POCKET_MAX) -> None:
        self._min = min_tiles
        self._max = max_tiles

    def apply(self, state: MapState, level: int) -> Image.Image:
        surf = state.surfs.get(level) or state.cells.get(level)
        if not surf:
            return Image.new("RGBA", (state.size * TILE, state.size * TILE), (0, 0, 0, 0))
        W, H = len(surf[0]), len(surf)

        passable = _passable_tiles(surf, state.objs, level, W, H)
        pockets = _find_pockets(passable, self._min, self._max)

        img = Image.new("RGBA", (W * TILE, H * TILE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for pocket, entrance in pockets:
            dist = _bfs_distances(pocket, entrance)
            max_d = max(dist.values()) if dist else 0
            for tile, d in dist.items():
                tx, ty = tile
                t = d / max_d if max_d else 0.0
                color = _magenta_color(t)
                draw.rectangle(
                    [tx * TILE, ty * TILE, (tx + 1) * TILE - 1, (ty + 1) * TILE - 1],
                    fill=color,
                )
        return img


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _passable_tiles(surf, objs, level: int, W: int, H: int) -> set:
    """Land tiles not blocked by any object footprint."""
    land = set()
    for y, row in enumerate(surf):
        for x, cell in enumerate(row):
            t = cell.get("t", 0) if isinstance(cell, dict) else _prefix_to_code(cell[:2])
            if t not in (_WATER, _ROCK):
                land.add((x, y))

    blocked = set()
    for o in objs:
        if o.get("l", 0) != level:
            continue
        mask = (o.get("template") or {}).get("mask")
        if not mask:
            continue
        ox, oy = o.get("x", 0), o.get("y", 0)
        hh = len(mask)
        for r, row in enumerate(mask):
            ww = len(row)
            for c, ch in enumerate(row):
                if ch in ("B", "X"):
                    tx = ox - (ww - 1 - c)
                    ty = oy - (hh - 1 - r)
                    blocked.add((tx, ty))

    return land - blocked


def _find_pockets(passable: set, min_tiles: int, max_tiles: int) -> list:
    """Find small sealed regions with a unique 1- or 2-tile entrance.

    Returns list of (pocket_frozenset, entrance_frozenset).
    """
    wall_adjacent = {
        t for t in passable
        if any((t[0] + dx, t[1] + dy) not in passable for dx, dy in _NB8)
    }

    best: dict = {}

    def _try_entrance(entrance):
        remaining = passable - entrance
        seeds = {
            (gx + dx, gy + dy)
            for gx, gy in entrance
            for dx, dy in _NB8
            if (gx + dx, gy + dy) in remaining
        }
        seen: set = set()
        for seed in sorted(seeds):
            if seed in seen:
                continue
            comp: set = {seed}
            q = collections.deque([seed])
            too_big = False
            while q and not too_big:
                cx, cy = q.popleft()
                for dx, dy in _NB8:
                    nb = (cx + dx, cy + dy)
                    if nb in remaining and nb not in comp:
                        comp.add(nb)
                        if len(comp) > max_tiles:
                            too_big = True
                            break
                        q.append(nb)
            if too_big:
                continue
            seen |= comp
            if len(comp) < min_tiles:
                continue
            has_exit = any(
                (t[0] + dx, t[1] + dy) in passable
                and (t[0] + dx, t[1] + dy) not in comp
                and (t[0] + dx, t[1] + dy) not in entrance
                for t in comp for dx, dy in _NB8
            )
            if has_exit:
                continue
            key = frozenset(comp)
            if key not in best or len(entrance) < len(best[key]):
                best[key] = frozenset(entrance)

    for g in sorted(wall_adjacent):
        _try_entrance(frozenset({g}))
    for g1 in sorted(wall_adjacent):
        g1x, g1y = g1
        for dx, dy in _NB8:
            g2 = (g1x + dx, g1y + dy)
            if g2 in wall_adjacent and g2 > g1:
                _try_entrance(frozenset({g1, g2}))

    # dedup: reject pockets adjacent to larger accepted ones
    accepted: list = []
    accepted_tiles: set = set()
    for pocket, entrance in sorted(best.items(), key=lambda kv: (-len(kv[0]), min(kv[0]))):
        footprint = pocket | {(x + dx, y + dy) for x, y in pocket for dx, dy in _NB8}
        if footprint.isdisjoint(accepted_tiles):
            accepted.append((pocket, entrance))
            accepted_tiles |= pocket
    return accepted


def _bfs_distances(pocket: frozenset, entrance: frozenset) -> dict:
    """BFS distance from entrance into pocket body (8-connected)."""
    dist: dict = {}
    q = collections.deque()
    for gx, gy in entrance:
        for dx, dy in _NB8:
            nb = (gx + dx, gy + dy)
            if nb in pocket and nb not in dist:
                dist[nb] = 0
                q.append(nb)
    while q:
        t = q.popleft()
        tx, ty = t
        for dx, dy in _NB8:
            nb = (tx + dx, ty + dy)
            if nb in pocket and nb not in dist:
                dist[nb] = dist[t] + 1
                q.append(nb)
    return dist


def _magenta_color(t: float) -> tuple:
    """t=0 (entrance, darkest) → t=1 (deepest, lightest). Returns RGBA."""
    v = 0.30 + 0.70 * t
    r, g, b = colorsys.hsv_to_rgb(300 / 360, 0.90, v)
    return (int(r * 255), int(g * 255), int(b * 255), 130)


_PREFIX_MAP = {
    "dt": 0, "sa": 1, "gr": 2, "sn": 3, "sw": 4,
    "rg": 5, "sb": 6, "lv": 7, "wt": 8, "ro": 9, "hl": 10, "wa": 11,
}


def _prefix_to_code(prefix: str) -> int:
    return _PREFIX_MAP.get(prefix, 0)
