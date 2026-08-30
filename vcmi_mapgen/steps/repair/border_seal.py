"""Residual border-leak seal — Repair-only (structural/guard fixes, no content placement)."""
import collections

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.steps.gate.gates import rnd_monster


def _blocking_cells(o):
    return [(cx, cy) for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]) if blk]


def seal_zone_borders(W, H, grid, zones, entrance_plan, objs, avoid, hard_avoid, seed, level,
                      skip_tiles=frozenset()):
    """Residual border-leak seal. The border bias (`pp_sample` BORDER_W) densifies zone
    fronts statistically, which is enough on compact probes but NOT on a real map: jagged
    fronts, gameplay approach tiles near the border and repair carve-backs leave aligned
    open pairs a hero can walk (or diagonal-step) through — measured 439 informal crossings
    on a 144x144 build. This pass closes every remaining cross-zone 8-adjacent open pair
    OUTSIDE the planned entrance bands with single corpus-weighted 1x1 blocking decorations
    (byte-for-byte the `fill_open_islands` emission), greedily picking the tile that kills
    the most remaining crossings. The cells land inside the already-dense ridge, so they
    read as ordinary vegetation — the sampler stays the look, this is only the caulk.

    `avoid` = tiles that must stay veg-free (protected web + tunnel corridors, gameplay
    cells + approaches, scatter-used, entrance bands — accumulated by `_run_level`). A pair
    whose BOTH sides are unsealable (a tunnel crossing the border, two facing web tiles)
    must stay open — so it gets a hostile GUARD instead (skipping only `hard_avoid` tiles:
    gameplay cells/approaches/pickups, where a second object cannot sit). One guard's zone
    of control contests every crossing within Chebyshev 1, so a run of adjacent residual
    crossings shares one guard. The map then has NO free informal crossing: every border
    pass is sealed, a planned guarded entrance, or a guarded back path.
    Returns (new_objs, sealed_cells, guard_tiles, n_unguarded_pairs)."""
    import random

    rng = random.Random(seed ^ 0x5EA1 ^ (level * 7919))
    owner, tname = {}, {}
    for zid, z in sorted(zones.items()):
        terr = ZE.TNAME.get(z["terrain_type"])
        if terr in (None, "water", "rock"):
            continue
        for t in z["tiles_set"]:
            owner[t] = zid
            tname[t] = terr
    blocked = set()
    for o in objs:
        blocked.update(_blocking_cells(o))
    land = {(x, y) for y in range(H) for x in range(W) if grid[y][x] < 8}
    open_all = land - blocked
    bands = set()
    for ents in entrance_plan.values():
        for _r, b, _o in ents:
            bands |= set(b)

    # non-band pairs: eligible for vegetation seal AND guard fallback
    # band pairs: entrance corridors — skip vegetation seal, guard-only
    pairs = []
    band_pairs = []
    for t in sorted(open_all):
        a = owner.get(t)
        if a is None or t in skip_tiles:
            continue
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):    # each unordered pair once
            n = (t[0] + dx, t[1] + dy)
            if n not in open_all or n in skip_tiles:
                continue
            b = owner.get(n)
            if b is not None and b != a:
                if t in bands or n in bands:
                    band_pairs.append((t, n))
                else:
                    pairs.append((t, n))

    dead = set()                                     # no decor pool for its terrain

    def sealable(t):
        return t not in avoid and t not in dead and t in owner and t not in bands

    new_objs, sealed = [], set()
    while pairs:
        cnt = collections.Counter()
        for t, n in pairs:
            if sealable(t):
                cnt[t] += 1
            if sealable(n):
                cnt[n] += 1
        if not cnt:
            break                                    # everything left is unsealable
        pick, _n = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))
        pool = ON.decor_pool(tname[pick], blocking=True, max_cells=1,
                             exclude_types=ZE.EXCLUDE_DECOR_TYPES)
        if not pool:
            dead.add(pick)
            continue
        ident = rng.choice(pool)
        new_objs.append({"x": pick[0], "y": pick[1], "l": 0,
                         "type": ident.get("type"), "subtype": ident.get("subtype"),
                         "animation": ident["animation"], "mask": ident["mask"],
                         "template": {"animation": ident["animation"],
                                      "mask": ident["mask"]}})
        sealed.add(pick)
        pairs = [p for p in pairs if pick not in p]

    # Collect existing gameplay guards from objs (placed by pp_gameplay) so the guard
    # pass below avoids duplicating coverage already provided.
    existing_guards = {(o["x"], o["y"]) for o in objs if o.get("purpose") == "GUARD"}

    def _covered(t, n, g_set):
        return any(max(abs(g[0] - t[0]), abs(g[1] - t[1])) <= 1
                   or max(abs(g[0] - n[0]), abs(g[1] - n[1])) <= 1
                   for g in g_set)

    # what must stay open gets contested instead: one hostile guard covers every residual
    # crossing within its Chebyshev-1 zone of control
    guard_tiles = set()
    unguarded = 0
    for t, n in pairs:
        if _covered(t, n, guard_tiles | existing_guards):
            continue
        cands = [c for c in sorted((t, n)) if c not in hard_avoid]
        if not cands:
            unguarded += 1
            continue
        g = cands[0]
        gident = rnd_monster(3 + (1 if rng.random() < 0.3 else 0))
        new_objs.append({"x": g[0], "y": g[1], "l": 0, "purpose": "GUARD",
                         "type": gident.get("type"), "subtype": gident.get("subtype"),
                         "animation": gident["animation"], "mask": gident["mask"],
                         "template": {"animation": gident["animation"],
                                      "mask": gident["mask"]},
                         "options": {"character": "hostile"},
                         "seal": True})               # informational: dup-guard cleanup must
        guard_tiles.add(g)                            # never drop it — it IS the border

    # Band pairs (planned entrance corridors) were left open on purpose but every corridor
    # must have at least one guard so the crossing requires a fight.  If pp_gameplay already
    # placed a guard that covers the pair, skip it; otherwise add one now.
    band_guard_tiles = set()
    for t, n in band_pairs:
        if _covered(t, n, guard_tiles | existing_guards | band_guard_tiles):
            continue
        cands = [c for c in sorted((t, n)) if c not in hard_avoid]
        if not cands:
            continue
        g = cands[0]
        gident = rnd_monster(3 + (1 if rng.random() < 0.3 else 0))
        new_objs.append({"x": g[0], "y": g[1], "l": 0, "purpose": "GUARD",
                         "type": gident.get("type"), "subtype": gident.get("subtype"),
                         "animation": gident["animation"], "mask": gident["mask"],
                         "template": {"animation": gident["animation"],
                                      "mask": gident["mask"]},
                         "options": {"character": "hostile"},
                         "seal": True})
        band_guard_tiles.add(g)

    guard_tiles |= band_guard_tiles
    return new_objs, sealed, guard_tiles, unguarded
