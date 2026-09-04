"""VCMI tile-string and template-mask encoding/decoding -- the stateless format
primitives `.vmap` terrain and object footprints are built from.
"""
import re

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
_TCODE_REV = {v: k for k, v in TCODE.items()}
RIVER = {1: "clrv", 2: "icyrv", 3: "mudrv", 4: "lavrv"}
_RIVER_REV = {v: k for k, v in RIVER.items()}
ROAD = {1: "dirtrd", 2: "gravrd", 3: "cobbrd"}
_ROAD_REV = {v: k for k, v in ROAD.items()}

_TILE_RE = re.compile(
    r"^(?P<t>[a-z]{2})(?P<view>\d+)(?P<mir>[+|_-])"
    r"(?:(?P<river>clrv|icyrv|mudrv|lavrv)(?P<rd>\d+)_)?"
    r"(?:(?P<road>dirtrd|gravrd|cobbrd)(?P<od>\d+)_)?$"
)


def _mir(m):
    h, v = m & 1, m & 2
    return "+" if (h and v) else "|" if v else "-" if h else "_"


def _mir_code(ch):
    return {"+": 3, "|": 2, "-": 1, "_": 0}[ch]


def tile_string(c):
    s = f"{TCODE.get(c['t'], 'gr')}{c['view']}{_mir(c.get('m', 0))}"
    if c.get("rt"):
        s += f"{RIVER.get(c['rt'], 'clrv')}{c.get('rd', 0)}_"
    if c.get("ot"):
        s += f"{ROAD.get(c['ot'], 'dirtrd')}{c.get('od', 0)}_"
    return s


def decode_tile_string(s):
    """Inverse of `tile_string`: a VCMI tile token -> `{t,view,m,rt,rd,ot,od}`."""
    m = _TILE_RE.match(s)
    if not m:
        raise ValueError(f"not a VCMI tile string: {s!r}")
    g = m.groupdict()
    return {
        "t": _TCODE_REV.get(g["t"], 2),
        "view": int(g["view"]),
        "m": _mir_code(g["mir"]),
        "rt": _RIVER_REV.get(g["river"], 0),
        "rd": int(g["rd"]) if g["rd"] is not None else 0,
        "ot": _ROAD_REV.get(g["road"], 0),
        "od": int(g["od"]) if g["od"] is not None else 0,
    }


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
    blocked-visitable in H3), so both map onto it.

    NOTE this is LOSSY: a VCMI-charset mask can never be translated back into 'X' vs 'A'
    -- see `kit.vmap.reader`'s docstring and `kit/objects.py`'s SSOT note for why the
    engine-internal mask must always be re-derived from the ontology, never read back
    out of a .vmap's `template.mask`.
    """
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
