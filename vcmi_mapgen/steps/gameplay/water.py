"""Water-body population and seaport guarantee — Gameplay-timing logic (runs before
vegetation forbids their footprint), despite `place_water`'s old home in pp_pickup.py.

Also owns `_pick`/`_legal`, the low-level identity-pick/footprint-legality helpers
`place_water` needs: Gameplay is the first step in pipeline order to need them, so
`steps/pickup/scatter.py` (added in a later phase, for place_scatter/place_pockets/etc.,
which need the exact same helpers) imports them from here rather than duplicating them.
"""
import collections
import zlib

from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.steps.gate.gates import rnd_monster
from vcmi_mapgen.steps.gameplay.mines import RND_ART, RND_RES, WATER_PURPOSES, mine_gameplay

_SEAPORT_ANIM = "avxshyd0"
_SEAPORT_MASK = ["VVV", "VVV", "BXB"]
_WATER_BODY_MIN = 30   # minimum water body size to require seaports


def _pick(pool, purpose, st_t, rng, allow_random=True, art_share=0.45):
    """Identity for a pickup. The H3 convention (user-mandated): favour the editor's RANDOM
    classes — random resource, tiered random artifacts — over fixed ones. `art_share` is
    the random-artifact probability for REWARD_PICKUP: high for guarded caches, low for
    unguarded scatter (which draws the fixed LOOT pool — treasure chests, campfires —
    weighted by the corpus mix, where the chest dominates)."""
    if allow_random and purpose == "RESOURCE_PILE" and rng.random() < 0.6:
        return ON.identity_of(RND_RES)
    if allow_random and purpose == "REWARD_PICKUP" and rng.random() < art_share:
        anim = rng.choices([a for a, w, v in RND_ART],
                           weights=[w for a, w, v in RND_ART], k=1)[0]
        return ON.identity_of(anim)
    pool = sorted((i for i in pool if "random" not in str(i.get("type", "")).lower()),
                  key=lambda i: i["animation"])
    if not pool:
        return None
    w = st_t["anim_w"].get(purpose, {})
    return rng.choices(pool, weights=[w.get(i["animation"].lower(), 0) + 0.2
                                      for i in pool], k=1)[0]


def _legal(ident, x, y, open_set, used, bounds=None, interactive_only=False):
    """A pickup/guard placement is legal if its INTERACTIVE cell(s) sit on an unused,
    placement-eligible tile.  V-overlay cells (sprite bleed) may overlap terrain/walls.

    interactive_only=True: only the interactive (A/X) cell is checked against `used` and
    bounds, and only that cell is returned for claiming.  Use this for dense fill passes
    where adjacent pickups' V-cells would otherwise falsely block each other — V cells are
    cosmetic in H3/VCMI and two objects sharing V-cell space is legal."""
    cells = [(tx, ty) for tx, ty, _b in OR.mask_cells(ident["mask"], x, y)]
    interactive = OR.mask_interactive_cells(ident["mask"], x, y) or cells
    check = interactive if interactive_only else cells
    if bounds is not None:
        bw, bh = bounds
        if any(not (0 <= tx < bw and 0 <= ty < bh) for tx, ty in check):
            return None
    if any(c in used for c in check):
        return None
    if all(c in open_set and c not in used for c in interactive):
        return check
    return None


def place_water(ts, zones, zid, seed=1):
    """Populate a WATER zone (spec point: water must not be empty): flotsam/sea chests
    (pickups), buoys/mermaids/sirens (bonus), boats + whirlpools (navigability), shipwrecks/
    derelicts (banks), ocean bottles, and random sea guards. Densities and animation mix come
    from the corpus water pass; identities from the ontology's water pools."""
    import random
    st = mine_gameplay().get("water")
    if not st or not st.get("tiles"):
        return []
    rng = random.Random(seed ^ (zid * 55313) ^ 0x5EA)
    area = len(ts)
    objs, used = [], set()
    for p in WATER_PURPOSES:
        x = st["counts"].get(p, 0) / st["tiles"] * area
        n = min(int(x) + (1 if rng.random() < x - int(x) else 0), 14)
        if not n:
            continue
        pool = ON.gameplay_pool("water", p)
        cands = sorted(ts)
        placed = []
        for t in rng.choices(cands, k=50 * n):
            if len(placed) >= n:
                break
            if any(max(abs(t[0] - q[0]), abs(t[1] - q[1])) < 4 for q in placed):
                continue
            ident = (rnd_monster(rng.choices((2, 3, 4, 5), (30, 30, 25, 15))[0])
                     if p == "GUARD" else _pick(pool, p, st, rng, allow_random=False))
            if ident is None:
                break
            cells = _legal(ident, t[0], t[1], ts, used)
            if cells is None:
                continue
            used.update(cells)
            o = {"x": t[0], "y": t[1], "l": 0, "purpose": p,
                 "type": ident.get("type"), "subtype": ident.get("subtype"),
                 "animation": ident["animation"], "mask": ident["mask"],
                 "template": {"animation": ident["animation"], "mask": ident["mask"]}}
            if p == "GUARD":
                o["options"] = {"character": "hostile"}
            objs.append(o)
            placed.append(t)
    return objs


def _ensure_water_seaports(W, H, grid, zones, objs, seed):
    """Guarantee ≥1 shipyard per land zone bordering a water body ≥ _WATER_BODY_MIN tiles,
    and ≥1 shipyard per island land zone ≥ _WATER_BODY_MIN tiles.

    For a 'lake' (water body not touching map borders) this ensures each bordering zone has
    a seaport — typically 1 zone = 1 seaport.  For 'open water' touching map borders, each
    zone on opposite shores gets its own seaport (≥2 total).

    Placement uses any anchor in the zone where the 3×3 shipyard fits with all 9 cells and
    the approach tile in the zone, and no blocking-cell conflict with existing objects.

    Returns list of new shipyard objects to append to `objs`."""
    import random as _rnd

    WATER, ROCK = 8, 9
    NB4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

    water_tiles = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == WATER}
    if not water_tiles:
        return []

    # land tile → zone id
    land_zone_of = {}
    for zid, z in zones.items():
        if ZE.TNAME.get(z["terrain_type"]) in (None, "water", "rock"):
            continue
        for t in z["tiles_set"]:
            land_zone_of[t] = zid

    # All blocking/interactive cells of existing objects
    existing_blk = set()
    for o in objs:
        mask_rows = o.get("mask") or o.get("template", {}).get("mask", ["X"])
        ax, ay = o["x"], o["y"]
        hh = len(mask_rows)
        for r, row in enumerate(mask_rows):
            ww = len(row)
            for ci, ch in enumerate(row):
                if ch in ("B", "X", "A"):
                    tx = ax - (ww - 1 - ci)
                    ty = ay - (hh - 1 - r)
                    existing_blk.add((tx, ty))

    new_objs = []
    # Anchor positions of seaports already in objs (for 20-tile spacing constraint)
    placed_anchors = [(o["x"], o["y"]) for o in objs if o.get("type") == "shipyard"]

    def _seaport_footprint(ax, ay):
        allc, blk, approach = [], [], None
        hh = len(_SEAPORT_MASK)
        for r, row in enumerate(_SEAPORT_MASK):
            ww = len(row)
            for ci, ch in enumerate(row):
                tx = ax - (ww - 1 - ci)
                ty = ay - (hh - 1 - r)
                allc.append((tx, ty))
                if ch in ("B", "X"):
                    blk.append((tx, ty))
                if ch == "X":
                    approach = (tx, ty + 1)
        return allc, blk, approach

    _SEAPORT_SPACING_SQ = 20 * 20  # minimum squared Euclidean distance between seaports

    def _try_place(ts_set, cand_tiles, label, force=True):
        """Try to place a shipyard. cand_tiles = anchor candidates (coastal tiles first).
        Prefers positions ≥20 tiles from existing seaports; when force=True (required
        placement) falls back to any valid position if no spaced candidate exists."""
        # NOTE: Python's built-in hash() is salted per-process for str (PYTHONHASHSEED),
        # so seeding from hash(label) would make this non-reproducible across runs even
        # for the identical seed — crc32 is a plain, stable string->int hash.
        rng = _rnd.Random(seed ^ zlib.crc32(label.encode()) ^ 0x53A9)
        shuffled = list(cand_tiles)
        rng.shuffle(shuffled)
        cap = shuffled[:300]

        def _candidate_ok(ax, ay, check_spacing):
            allc, blk, approach = _seaport_footprint(ax, ay)
            if any(c not in ts_set for c in allc):
                return False
            if approach not in ts_set:
                return False
            # Approach tile must not be occupied (dark-green X tile must be accessible)
            if approach in existing_blk:
                return False
            if any(c in existing_blk for c in blk):
                return False
            # At least one BXB cell must be 4-adjacent to water
            if not any((bx + dx, by + dy) in water_tiles
                       for bx, by in blk for dx, dy in NB4):
                return False
            if check_spacing and any(
                (ax - px) ** 2 + (ay - py) ** 2 < _SEAPORT_SPACING_SQ
                for px, py in placed_anchors
            ):
                return False
            return True

        def _do_place(ax, ay):
            _, blk, _ = _seaport_footprint(ax, ay)
            existing_blk.update(blk)
            placed_anchors.append((ax, ay))
            o = {
                "x": ax, "y": ay, "l": 0, "purpose": "WATER_TRANSPORT",
                "type": "shipyard", "subtype": "object",
                "animation": _SEAPORT_ANIM, "mask": _SEAPORT_MASK,
                "template": {
                    "animation": _SEAPORT_ANIM, "editorAnimation": "",
                    "mask": _SEAPORT_MASK,
                    "visitableFrom": ["+++", "+-+", "+++"],
                },
            }
            new_objs.append(o)
            return o

        for ax, ay in cap:
            if _candidate_ok(ax, ay, check_spacing=True):
                return _do_place(ax, ay)
        if force:
            for ax, ay in cap:
                if _candidate_ok(ax, ay, check_spacing=False):
                    return _do_place(ax, ay)
        return None

    def _zone_has_seaport(zid, ts_set):
        """True if any existing or new seaport is in this zone's tile set."""
        for o in objs + new_objs:
            if o.get("type") != "shipyard":
                continue
            # seaport BXB row at y=o["y"]: cells o["x"]-2..o["x"]
            ax, ay = o["x"], o["y"]
            if any((ax - 2 + i, ay) in ts_set for i in range(3)):
                return True
        return False

    def _place_for_zone(zid, z, label):
        """Ensure zone zid has a seaport; restrict to near-coastal tiles only."""
        ts_set = set(z["tiles_set"])
        if _zone_has_seaport(zid, ts_set):
            return True
        # Coastal = zone tiles adjacent to water
        coastal_set = {t for t in ts_set if any(
            0 <= t[0]+dx < W and 0 <= t[1]+dy < H and grid[t[1]+dy][t[0]+dx] == WATER
            for dx, dy in NB4)}
        if not coastal_set:
            return False
        # Expand 1 hop inland so the 3×3 footprint can anchor with its BXB bottom
        # row immediately adjacent to the water edge — keeps seaports ≤1 tile
        # from the shoreline.
        near_coastal = set(coastal_set)
        for _ in range(1):
            for t in list(near_coastal):
                for dx, dy in NB4:
                    nb = (t[0]+dx, t[1]+dy)
                    if nb in ts_set:
                        near_coastal.add(nb)
        o = _try_place(ts_set, list(near_coastal), label)
        if not o:
            print(f"  WARNING: no seaport placed on zone {zid} "
                  f"({ZE.TNAME.get(z['terrain_type'])}, {z['area']} tiles) — "
                  f"no valid near-coastal anchor found")
            return False
        return True

    # ── 1. Water-body guarantee: one seaport per bordering zone ───────────────
    seen_w = set()
    for t0 in sorted(water_tiles):
        if t0 in seen_w:
            continue
        comp, q = {t0}, [t0]
        while q:
            x, y = q.pop()
            for dx, dy in NB4:
                n = (x + dx, y + dy)
                if n in water_tiles and n not in comp:
                    comp.add(n)
                    q.append(n)
        seen_w |= comp
        if len(comp) < _WATER_BODY_MIN:
            continue

        # Find unique zones bordering this water body (by shore tile zone membership)
        bordering_zids = set()
        for wx, wy in comp:
            for dx, dy in NB4:
                t = (wx + dx, wy + dy)
                zid = land_zone_of.get(t)
                if zid is not None:
                    bordering_zids.add(zid)

        for zid in sorted(bordering_zids):
            z = zones[zid]
            if z["area"] < _WATER_BODY_MIN:
                continue   # tiny border sliver — skip
            _place_for_zone(zid, z, f"wb_{t0}_{zid}")

    # ── 2. Island guarantee ───────────────────────────────────────────────────
    for zid, z in sorted(zones.items()):
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < _WATER_BODY_MIN:
            continue
        ts_set = set(z["tiles_set"])
        is_island = all(
            grid[ny][nx] in (WATER, ROCK)
            for x, y in ts_set
            for dx, dy in NB4
            for nx, ny in [(x + dx, y + dy)]
            if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in ts_set
        )
        if not is_island:
            continue
        _place_for_zone(zid, z, f"island_{zid}")

    return new_objs
