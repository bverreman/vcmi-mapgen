"""Object identity & faithful-map catalog — the shared, generic lookup layer.

Two sources of truth, both already byte-exact:

  * ``maps_json/<name>.json``  — every corpus object carries its EXACT
    ``type, subtype, animation, mask`` (built from the real .h3m template). Use
    :func:`exact_identity` to reproduce a corpus object identically.
  * ``data/objlib.json`` — ``purpose -> terrain_id -> [ {type, subtype, animation,
    mask, weight}, ... ]`` — the catalog of interchangeable concrete objects per
    purpose+terrain, harvested from the corpus.

The terrain cells in a faithful map ({t,view,rt,rd,ot,od,m}) are already what
``faithful.to_vmap`` / ``kit.vmap_format.tile_string`` expect, so a generated map can pass
faithful terrain straight through.
"""
from __future__ import annotations

import json
import os

from vcmi_mapgen import ontology as ON
from vcmi_mapgen.kit.paths import project_root

ROOT = project_root()
_OBJLIB = json.load(open(str(ROOT / "data" / "objlib.json")))

# Identity fields a faithful object carries that the .vmap writer needs.
_IDENT_KEYS = ("type", "subtype", "animation", "mask")


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def faithful_path(name: str) -> str:
    return str(ROOT / "maps_json" / f"{name}.json")


def load_faithful(name: str) -> dict:
    """Load a byte-exact faithful map: terrain (writer-ready) + objects (exact mask)."""
    return json.load(open(faithful_path(name)))


def all_map_names() -> list[str]:
    d = ROOT / "maps_json"
    return [os.path.splitext(f)[0] for f in sorted(os.listdir(d)) if f.endswith(".json")]


# ---------------------------------------------------------------------------
# Object classification & exact identity
# ---------------------------------------------------------------------------

def purpose_of(obj: dict) -> str:
    """Purpose of a faithful (corpus) object — uses its raw cls/sub via the ontology.
    Only corpus objects (loaded via load_faithful) carry cls/sub; a GENERATED map's
    objects carry type/subtype/purpose instead (purpose is set directly at construction
    time) — use :func:`type_to_purpose` for a type-keyed lookup that works on either."""
    return ON.resolve(obj["cls"], obj["sub"]).get("purpose", "UNKNOWN")


_TYPE2PURPOSE = {it["type"]: p for p, terr in _OBJLIB.items()
                 for items in terr.values() for it in items}


def type_to_purpose(type_name: str) -> str | None:
    """Purpose for an object TYPE alone (no cls/sub needed) — built from the same
    objlib.json catalog as :func:`purpose_of`, and verified byte-equivalent to it across
    the full corpus (every type's purpose_of() result agrees with this table). The only
    lookup that works for a generated map's objects, which don't carry cls/sub."""
    return _TYPE2PURPOSE.get(type_name)


def cluster_of(obj: dict) -> str:
    """Macro-cluster of a faithful object (DECORATION / VISIBLE / GATE / QUEST_PAIR)."""
    return ON.resolve(obj["cls"], obj["sub"]).get("cluster", "VISIBLE")


def info_of(obj: dict) -> dict:
    """Full ontology record (purpose, cluster, relational, terrain_coupled, ...)."""
    return ON.resolve(obj["cls"], obj["sub"])


def exact_identity(obj: dict) -> dict:
    """The exact {type, subtype, animation, mask} of a corpus object."""
    return {k: obj[k] for k in _IDENT_KEYS}


def is_blocking(mask: list[str]) -> bool:
    """True if the object's footprint blocks movement (mask has a 'B' or 'X' cell — 'X' is a
    blocked-and-visitable building action tile)."""
    return any(ch in "BX" for row in mask for ch in row)


def is_relational(obj: dict) -> bool:
    """True for portals/gates/quest links whose subtype must not be re-rolled."""
    return ON.resolve(obj["cls"], obj["sub"]).get("relational", False)


def mask_cells(mask: list[str], x: int, y: int):
    """Tiles a mask covers when anchored at (x, y).

    Convention: anchor (x, y) is the BOTTOM-RIGHT tile of the footprint. Mask rows are stored
    LEFT-TO-RIGHT, sprite-aligned (matching `kit.vmap.mask.build_mask_from_h3m` and `ontology._decode_mask`),
    so column 0 is the LEFTMOST tile and the anchor is the LAST column of each row ->
    `tx = x - (ww - 1 - c)` where `ww = len(row)`. (Verified pixel-for-pixel against real sprite
    art: a sawmill's ramp/visit tile and a pine clump's trunks land on the correct side only with
    this formula -- the plain `x - c` mirrors every asymmetric footprint horizontally.)
    'B' = blocking, 'X' = blocking + visitable, 'A' = passable + visitable, 'V' = passable
    overlay, ' ' = empty. Yields (tx, ty, blocking_bool) per non-empty cell.
    """
    hh = len(mask)
    for r, row in enumerate(mask):
        ww = len(row)
        for c, ch in enumerate(row):
            if ch == " ":
                continue
            yield x - (ww - 1 - c), y - (hh - 1 - r), (ch in ("B", "X"))


def mask_interactive_cells(mask: list[str], x: int, y: int):
    """The subset of `mask_cells` a hero must actually step on to trigger this object --
    visitable ('A') or blocking+visitable ('X') -- as opposed to pure passable overlay
    ('V') or solid-but-inert ('B'). A guard's other footprint cells are cosmetic canopy;
    only this cell needs to be free & reachable for the object to functionally gate a tile."""
    hh = len(mask)
    out = []
    for r, row in enumerate(mask):
        ww = len(row)
        for c, ch in enumerate(row):
            if ch in ("A", "X"):
                out.append((x - (ww - 1 - c), y - (hh - 1 - r)))
    return out


if __name__ == "__main__":
    names = all_map_names()
    print(f"faithful maps: {len(names)}  objlib purposes: {sorted(_OBJLIB)}")
    m = load_faithful("All for One")
    from collections import Counter
    pc = Counter(purpose_of(o) for o in m["objects"])
    print("All for One purposes:", dict(pc.most_common()))
    o = m["objects"][0]
    print("exact identity sample:", exact_identity(o), "blocking=", is_blocking(o["mask"]))
