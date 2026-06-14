"""Object identity & subtype catalog for the deterministic template engine.

Two sources of truth, both already byte-exact:

  * ``out/faithful/<name>.json``  — every corpus object carries its EXACT
    ``type, subtype, animation, mask`` (built from the real .h3m template). Use
    :func:`exact_identity` to reproduce a corpus object identically.
  * ``out/objlib.json`` — ``purpose -> terrain_id -> [ {type, subtype, animation,
    mask, weight}, ... ]`` — the catalog of interchangeable concrete objects per
    purpose+terrain, harvested from the corpus. Use :func:`pick_variant` to choose
    one (argmax for the deterministic structure, seeded sample for subtype variety).

The terrain cells in ``out/faithful`` ({t,view,rt,rd,ot,od,m}) are already what
``faithful.to_vmap`` / ``faithful.tile_string`` expect, so a generated map can pass
faithful terrain straight through.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OBJLIB = json.load(open(os.path.join(ROOT, "data", "objlib.json")))

# Identity fields a faithful object carries that the .vmap writer needs.
_IDENT_KEYS = ("type", "subtype", "animation", "mask")


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def faithful_path(name: str) -> str:
    return os.path.join(ROOT, "maps_json", f"{name}.json")


def load_faithful(name: str) -> dict:
    """Load a byte-exact faithful map: terrain (writer-ready) + objects (exact mask)."""
    return json.load(open(faithful_path(name)))


def all_map_names() -> list[str]:
    d = os.path.join(ROOT, "maps_json")
    return [os.path.splitext(f)[0] for f in sorted(os.listdir(d)) if f.endswith(".json")]


# ---------------------------------------------------------------------------
# Object classification & exact identity
# ---------------------------------------------------------------------------

def purpose_of(obj: dict) -> str:
    """Purpose of a faithful object (uses its raw cls/sub via the ontology)."""
    return ON.resolve(obj["cls"], obj["sub"]).get("purpose", "UNKNOWN")


def info_of(obj: dict) -> dict:
    """Full ontology record (purpose, relational, terrain_coupled, ...)."""
    return ON.resolve(obj["cls"], obj["sub"])


def exact_identity(obj: dict) -> dict:
    """The exact {type, subtype, animation, mask} of a corpus object."""
    return {k: obj[k] for k in _IDENT_KEYS}


def is_blocking(mask: list[str]) -> bool:
    """True if the object's footprint blocks movement (mask contains 'B')."""
    return any("B" in row for row in mask)


def is_relational(obj: dict) -> bool:
    """True for portals/gates/quest links whose subtype must not be re-rolled."""
    return ON.resolve(obj["cls"], obj["sub"]).get("relational", False)


def mask_cells(mask: list[str], x: int, y: int):
    """Tiles a mask covers when anchored at (x, y).

    Convention (matches render_footprint / the .h3m mask): rows x cols, anchor at
    the bottom-right cell; 'B' = blocking, 'A'/'V' = visitable/overlay, ' ' = empty.
    Yields (tx, ty, blocking_bool) for every non-empty cell.
    """
    hh = len(mask)
    ww = max((len(r) for r in mask), default=0)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            if ch == " ":
                continue
            tx = x - (ww - 1 - c)
            ty = y - (hh - 1 - r)
            yield tx, ty, (ch == "B")


# ---------------------------------------------------------------------------
# Subtype catalog (objlib) — for seed-driven variety
# ---------------------------------------------------------------------------

def variants(purpose: str, terrain_id: int) -> list[dict]:
    """Interchangeable concrete objects for (purpose, terrain), any-terrain fallback."""
    by_terr = _OBJLIB.get(purpose)
    if not by_terr:
        return []
    cands = by_terr.get(str(terrain_id))
    if not cands:
        for v in by_terr.values():
            if v:
                cands = v
                break
    return cands or []


def pick_variant(purpose: str, terrain_id: int, rng=None) -> dict | None:
    """Pick a concrete object for (purpose, terrain).

    rng is None  -> argmax weight (deterministic structure; seed=0 path).
    rng given    -> weighted sample (subtype variety; seed!=0 path).

    Positions/counts are decided elsewhere; this only varies *which* concrete
    object (e.g. which dwelling/mine resource/treasure) fills a planned slot.
    """
    cands = variants(purpose, terrain_id)
    if not cands:
        return None
    if rng is None:
        return max(cands, key=lambda e: e.get("weight", 0))
    weights = [max(e.get("weight", 1), 1) for e in cands]
    return rng.choices(cands, weights=weights, k=1)[0]


if __name__ == "__main__":
    names = all_map_names()
    print(f"faithful maps: {len(names)}  objlib purposes: {sorted(_OBJLIB)}")
    m = load_faithful("All for One")
    from collections import Counter
    pc = Counter(purpose_of(o) for o in m["objects"])
    print("All for One purposes:", dict(pc.most_common()))
    o = m["objects"][0]
    print("exact identity sample:", exact_identity(o), "blocking=", is_blocking(o["mask"]))
    print("pick MINE on grass argmax:", pick_variant("MINE", 2))
