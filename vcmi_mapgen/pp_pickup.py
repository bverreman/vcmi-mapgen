"""Pickup layer (L4): resources, artifacts and monster guards over the finished open field.

Runs AFTER vegetation — the open field is known, so the classic H3 treasure grammar applies:

  - **unguarded scatter** — resource piles/artifacts lying in the open along routes are
    ALWAYS free (user-mandated: a guard only belongs at a genuine chokepoint, never planted
    beside loot that sits in open terrain and can be walked around); sampled from the L3
    intensity fit (edge/gate/openness covariates) over reachable open tiles, with a small
    separation,
  - **guarded caches** — every genuine geometric POCKET (`ZF.find_pockets`: a bounded area
    with exactly one entrance neck — see its docstring) gets a cache, deterministically —
    not a corpus-density budget draw that can run dry before every real nook is reached.
    Each cache holds up to 3 resource piles plus an artifact (its own dedicated allocation,
    separate from the unguarded-scatter budget below) and a GUARD monster on the pocket's
    one mouth tile, so the guard actually gates the treasure.

Identities come from `ontology.gameplay_pool` (RESOURCE_PILE / REWARD_PICKUP / GUARD),
weighted by corpus animation frequency; random-* editor placeholders are skipped.
Everything is deterministic in `seed`.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import ontology as ON           # noqa: E402
import pp_gameplay as PG        # noqa: E402
import zone_field as ZF         # noqa: E402

CAPS = {"RESOURCE_PILE": 16, "REWARD_PICKUP": 8}   # base floors; caps scale (scatter only --
                                # pocket guards/caches are deterministic, see place_pickups)
SCATTER_ART_SHARE = 0.15        # unguarded scatter: mostly LOOT (chests/campfires); the
                                # tiered random artifacts live behind cache guards instead
LOOT_FLOOR_AREA = 300           # a real zone always yields a couple of unguarded loots


def _web_dist(open_set, prot):
    """4-connected BFS steps from the protected web through the open field. Tiles absent
    from the result are UNREACHABLE (sealed by vegetation) — nothing may be placed there."""
    d = {t: 0 for t in prot if t in open_set}
    q = collections.deque(d)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n in open_set and n not in d:
                d[n] = d[(x, y)] + 1
                q.append(n)
    return d


def _reach8(open_set, seed):
    """8-connected BFS over the true `open_set` (the physical open/blocked tile layer),
    seeded from tiles already proven reachable by `_web_dist`. Extends that 4-connected web
    reach with anything only joined by a diagonal step — H3 heroes move diagonally, so a
    tile behind a corner-cut squeeze IS reachable in play even though `_web_dist` can't see
    past it. This is the layer pocket mouths/cache tiles are actually validated against:
    plain `open_set` membership alone would also accept ground that is open but totally
    disconnected from the web (an unreachable floating island), which is not placeable
    either."""
    d = set(t for t in seed if t in open_set)
    q = collections.deque(d)
    while q:
        x, y = q.popleft()
        for dx, dy in ZF.NB8:
            n = (x + dx, y + dy)
            if n in open_set and n not in d:
                d.add(n)
                q.append(n)
    return d


def _pick(pool, purpose, st_t, rng, allow_random=True, art_share=0.45):
    """Identity for a pickup. The H3 convention (user-mandated): favour the editor's RANDOM
    classes — random resource, tiered random artifacts — over fixed ones. `art_share` is
    the random-artifact probability for REWARD_PICKUP: high for guarded caches, low for
    unguarded scatter (which draws the fixed LOOT pool — treasure chests, campfires —
    weighted by the corpus mix, where the chest dominates)."""
    if allow_random and purpose == "RESOURCE_PILE" and rng.random() < 0.6:
        return ON.identity_of(PG.RND_RES)
    if allow_random and purpose == "REWARD_PICKUP" and rng.random() < art_share:
        anim = rng.choices([a for a, w, v in PG.RND_ART],
                           weights=[w for a, w, v in PG.RND_ART], k=1)[0]
        return ON.identity_of(anim)
    pool = sorted((i for i in pool if "random" not in str(i.get("type", "")).lower()),
                  key=lambda i: i["animation"])
    if not pool:
        return None
    w = st_t["anim_w"].get(purpose, {})
    return rng.choices(pool, weights=[w.get(i["animation"].lower(), 0) + 0.2
                                      for i in pool], k=1)[0]


def _dedupe_pockets(pockets):
    """Collapse near-duplicate mouth candidates into one CANDIDATE LIST per genuine physical
    nook. `ZF.find_pockets` returns one entry per candidate MOUTH tile, but a neck wider than
    one tile makes several neighbouring tiles each independently qualify as "the" mouth of the
    same nook -- and in H3 a guard already threatens every adjacent tile (stepping next to a
    wandering monster forces combat), so one guard placed at a shared neck already gates every
    mouth candidate touching it. Merge mouth+pocket tiles into 4-connected blobs (union-find
    over shared tiles).

    Returns a list of candidate lists (one list per nook), each sorted LARGEST pocket first,
    outer list sorted largest-top-candidate first. The caller tries candidates within a blob
    in order and falls back to the next one when the top pick's mouth tile is unusable --
    e.g. it happens to coincide with an unrelated, already-placed object's approach cell.
    Previously this function collapsed each blob down to a single (best) mouth, so a blob
    whose ONLY candidate happened to be blocked lost its cache and guard entirely even though
    6 other valid mouth candidates for the exact same physical nook existed (confirmed
    2026-07-04: a real 11-tile pocket next to a gem mine had its best mouth land on the
    mine's own visitableFrom approach cell -- already `used` -- and was skipped outright,
    reproducing the user's "pockets you found were not filled" complaint even after pocket
    DETECTION was fixed)."""
    items = list(pockets.items())
    owner = collections.defaultdict(list)
    for idx, (mouth, pocket) in enumerate(items):
        for t in (mouth, *pocket):
            owner[t].append(idx)
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for idxs in owner.values():
        for a, b in zip(idxs, idxs[1:]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    groups = collections.defaultdict(list)
    for idx, (mouth, pocket) in enumerate(items):
        groups[find(idx)].append((mouth, pocket))
    blobs = [sorted(cands, key=lambda kv: (-len(kv[1]), kv[0])) for cands in groups.values()]
    return sorted(blobs, key=lambda cands: (-len(cands[0][1]), cands[0][0]))


def _legal(ident, x, y, open_set, used, bounds=None):
    """A pickup/guard placement is legal if its INTERACTIVE cell(s) -- the tile(s) a hero
    must actually step on, per `mask_interactive_cells` -- sit on an unused, placement-
    eligible tile. Other footprint cells (mask 'V') are cosmetic sprite bleed and may overlap
    non-open terrain exactly like real H3 maps place objects near walls; they only need to
    stay on the map and avoid double-stacking another pickup placed this same pass. A pocket
    is small and wall-bounded by construction, so requiring the WHOLE mask free (the old
    behaviour) rejected the vast majority of otherwise-legal cache tiles whenever a 'V'
    overlay cell fell on the pocket's own wall or off the map edge."""
    cells = [(tx, ty) for tx, ty, _b in OR.mask_cells(ident["mask"], x, y)]
    if bounds is not None:
        bw, bh = bounds
        if any(not (0 <= tx < bw and 0 <= ty < bh) for tx, ty in cells):
            return None
    if any(c in used for c in cells):
        return None
    interactive = OR.mask_interactive_cells(ident["mask"], x, y) or cells
    if all(c in open_set and c not in used for c in interactive):
        return cells
    return None


def _place_one(objs, used, reach, rng, st, purpose, pool, x, y,
               ident=None, art_share=0.45, cache=False, bounds=None):
    """Shared placement primitive for both scatter and pocket caches: resolve an identity,
    check its footprint against `reach`/`used`, and if legal append the obj and claim its
    cells. Returns whether it landed."""
    ident = ident or _pick(pool, purpose, st, rng, art_share=art_share)
    if ident is None:
        return False
    if purpose == "GUARD":
        # a guard's mask carries decorative overlay cells (H3's monster sprites always
        # bleed into surrounding scenery) alongside its one interactive cell -- at a
        # genuine chokepoint the surroundings are mostly blocked/unreachable BY
        # DEFINITION, so requiring the whole footprint free (like _legal does) means the
        # guard can almost never actually land on the neck. Only the interactive cell has
        # to be free & reachable; the rest may fall outside `reach` or overlap terrain.
        interactive = OR.mask_interactive_cells(ident["mask"], x, y)
        if not interactive or not all(c in reach and c not in used for c in interactive):
            return False
        cells = [(tx, ty) for tx, ty, _b in OR.mask_cells(ident["mask"], x, y)]
        if bounds is not None:
            bw, bh = bounds
            if any(not (0 <= tx < bw and 0 <= ty < bh) for tx, ty in cells):
                return False
        if any(c in used for c in cells):
            return False
    else:
        cells = _legal(ident, x, y, reach, used, bounds=bounds)
        if cells is None:
            return False
    used.update(cells)
    o = {"x": x, "y": y, "l": 0, "purpose": purpose,
         "type": ident.get("type"), "subtype": ident.get("subtype"),
         "animation": ident["animation"], "mask": ident["mask"],
         "template": {"animation": ident["animation"], "mask": ident["mask"]}}
    if purpose == "GUARD":       # absent => VCMI 'compliant' => every creature joins free
        o["options"] = {"character": "hostile"}
    if cache:  # a guarded-pocket pickup, not open scatter — informational marker only,
        o["cache"] = True        # ignored by the vmap exporter, used by tests
    objs.append(o)
    return True


def place_scatter(ts, zones, zid, terrain, open_set, prot, seed=1, bounds=None):
    """Unguarded scatter loot for one zone (resources/artifacts lying in the open along
    routes — user-mandated to always be free, never guarded, since it can just be walked
    around). Returns (objs, used, reach): `used` and `reach` (this zone's own BFS-reachable
    open tiles) are handed to `place_pocket_caches` so the global pocket pass knows which
    tiles this zone already spent on scatter and can treat the rest as this zone's share of
    the whole map's reachable field.

    Guarded pocket caches are NOT placed here — see `place_pocket_caches`, which must run
    once for the WHOLE map after every zone's scatter is done (a genuine pocket must be
    judged against true global passability, not one zone's reach alone)."""
    import random
    st = PG.mine_gameplay()[terrain]
    rng = random.Random(seed ^ (zid * 92821) ^ 0x9C4)
    area = len(ts)
    dens = {p: st["counts"].get(p, 0) / max(st["tiles"], 1) for p in PG.PICKUP_PURPOSES}

    def stoch(x, cap):
        n = int(x) + (1 if rng.random() < x - int(x) else 0)
        return min(n, cap)

    n_res = stoch(dens["RESOURCE_PILE"] * area, PG.scaled_cap(CAPS["RESOURCE_PILE"],
                                                              dens["RESOURCE_PILE"] * area))
    n_art = stoch(dens["REWARD_PICKUP"] * area, PG.scaled_cap(CAPS["REWARD_PICKUP"],
                                                              dens["REWARD_PICKUP"] * area))
    if area >= LOOT_FLOOR_AREA:                      # a real zone always holds some loot
        n_art = max(n_art, 2)

    dweb = _web_dist(open_set, prot)
    reach = set(dweb)                                # reachable open tiles only
    op = PG.openness(open_set)
    ed = ZF.edge_dist(ts)
    bands = ZF._zone_gate_bands(ts, zones, zid,
                                open_frac=st.get("border_open_frac", 0.5))
    gd = PG.gate_dist(ts, set().union(*(b for _r, b in bands)) if bands else set())

    pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
    pool_art = ON.gameplay_pool(terrain, "REWARD_PICKUP")

    objs, used = [], set()

    # rewards here are LOOT (treasure chests, campfires — the corpus mix), not artifacts: the
    # tiered random artifacts sit behind the cache guards instead. User-mandated: scattered
    # loot sits in the open field (not a genuine chokepoint), so it is ALWAYS free — no guard
    # is ever planted beside it, since a monster in open terrain can just be walked around.
    def scatter(purpose, pool, n, min_sep):
        if n <= 0:
            return
        wmap = PG._intensity_weights(reach, purpose, st, ed, gd, op=op)
        cands = sorted(reach)
        weights = [wmap[t] for t in cands]
        placed = []
        for t in rng.choices(cands, weights=weights, k=60 * n):
            if len(placed) >= n:
                break
            if t in used:
                continue
            if any(max(abs(t[0] - q[0]), abs(t[1] - q[1])) < min_sep for q in placed):
                continue
            if _place_one(objs, used, reach, rng, st, purpose, pool, t[0], t[1],
                         art_share=SCATTER_ART_SHARE, bounds=bounds):
                placed.append(t)

    scatter("RESOURCE_PILE", pool_res, n_res, min_sep=3)
    scatter("REWARD_PICKUP", pool_art, n_art, min_sep=5)
    return objs, used, reach


def place_pocket_caches(zone_records, seed=1, bounds=None):
    """Guarded caches in genuine geometric pockets — found in ONE global, zone-independent
    pass over the WHOLE map's TRUE physical passability, run once after every zone's
    terrain, vegetation and scatter is finalized. `zone_records` is a list of
    {"zid", "terrain", "ts", "open_set", "passable", "reach", "used"}: `open_set` is that
    zone's PLACEMENT-ELIGIBLE tiles (terrain minus vegetation-blocked/gameplay-occupied/
    approach cells — nothing new may stack there); `passable` is that zone's TRUE physical
    passability (terrain minus only the tiles that are actually impassable — approach tiles
    and non-blocking occupied footprint cells stay in it, since a hero can walk over them
    even though nothing new can be placed there); `reach`/`used` as returned by
    `place_scatter` (a 4-connected BFS subset of `open_set` reachable from the zone's
    protected web).

    User-mandated fix (2026-07-04): pocket detection must not run per zone against that
    zone's own `reach` alone — a tile absent from one zone's reach is NOT necessarily
    blocking, it may just be a NEIGHBOURING zone's open ground, and zone borders are wide
    gate bands, not walls (see `zone_field._zone_gate_bands`). Fix #1: build one GLOBAL
    reachable set (union of every zone's remaining reach) instead of a per-zone one.

    Third fix, same day (user: "there is something wrong in the way you classify open tile,
    block tile and visitable tile... The region shown is clearly not a pocket" — rejecting
    the second fix below). The second fix fed `find_pockets` the union of every zone's
    `open_set` (`global_open`). That is WRONG for geometry: `open_set` is a
    placement-eligibility layer — it also excludes approach tiles and non-blocking occupied
    cells, which ARE physically walkable. Feeding it to `find_pockets` made every such
    reserved-but-walkable tile look like a wall, fabricating a "pocket" wherever one happened
    to sit near a corner. Confirmed empirically: of 338 raw candidates found via
    `global_open`, only 116 survive once true passability is used instead — 271 (~80%) were
    false positives, including the exact mouth (11,48) example proven and sent as "fixed" in
    the prior turn. Fix: geometry now runs on `global_true` (union of every zone's
    `passable`, i.e. `ts - blocked - gblocked`), the ACTUAL per-tile open/blocked layer.
    `open_set`/`global_open` still exists and still matters — but only downstream, to gate
    where a NEW object may physically land (see `global_place` below).

    Second fix, same day, superseded above but kept for the diagonal-neck rationale ("add the
    open block tile layer" — `reach` alone is still the wrong universe for pocket GEOMETRY):
    `_web_dist` is a 4-connected BFS, but `find_pockets`/`_bounded_fill` probe neighbours with
    `NB8` (H3 heroes move diagonally). A pocket whose only neck is a diagonal squeeze — or
    whose interior simply isn't 4-connected back to the web — never enters `reach` at all, so
    `find_pockets` silently skips it, neither detecting it as a pocket nor as open ground,
    even though it's physically walkable and 8-connected-reachable in-game. This diagonal
    argument is still correct; only the layer it was applied to (`global_open` instead of
    `global_true`) was wrong.

    `global_reach` (4-connected) is too narrow to gate commitment: a genuine diagonal-neck
    mouth is, by construction, passable (in `global_true`) yet absent from any zone's
    4-connected `reach`. `global_reach8` closes this: an 8-connected BFS over `global_true`,
    seeded from `global_reach`, giving the tiles a hero can ACTUALLY stand on using real H3
    movement. But standing-on and building-on are different questions — a hero can stand on
    an approach tile, yet nothing new may be placed there (it's already claimed). So actual
    commitment (guard precheck, every cache tile) gates on `global_place = global_reach8 &
    global_open`: truly reachable AND placement-eligible.

    Returns (objs, n_pockets)."""
    import random
    zone_of = {}
    terrain_of = {}
    global_open = set()
    global_true = set()
    global_reach = set()
    used = set()
    for zr in zone_records:
        zid = zr["zid"]
        for t in zr["ts"]:
            zone_of[t] = zid
        terrain_of[zid] = zr["terrain"]
        global_open |= zr["open_set"]
        global_true |= zr.get("passable", zr["open_set"])
        global_reach |= (zr["reach"] - zr["used"])
        used |= zr["used"]
    global_reach8 = _reach8(global_true, global_reach)
    global_place = global_reach8 & global_open

    raw = ZF.find_pockets(global_true)
    blobs = _dedupe_pockets(raw)
    guard_mask = PG.rnd_monster(1)["mask"]  # uniform across levels 1-7; used to pre-check fit
    objs = []

    for candidates in blobs:
        # try candidate mouths for this SAME physical nook best-first; fall back instead of
        # abandoning a genuine pocket just because its top-ranked mouth tile happens to
        # coincide with an unrelated object's already-`used` approach cell (see
        # `_dedupe_pockets` docstring).
        mouth = pocket = zid = None
        for cand_mouth, cand_pocket in candidates:
            if cand_mouth in used:
                continue
            cand_zid = zone_of.get(cand_mouth)
            if cand_zid is None:
                continue
            if not all(c in global_place and c not in used
                       for c in OR.mask_interactive_cells(guard_mask, cand_mouth[0], cand_mouth[1])):
                continue
            mouth, pocket, zid = cand_mouth, cand_pocket, cand_zid
            break
        if mouth is None:
            continue
        terrain = terrain_of[zid]
        st = PG.mine_gameplay()[terrain]
        pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
        pool_art = ON.gameplay_pool(terrain, "REWARD_PICKUP")
        # deterministic per-pocket rng (keyed on the mouth's own position, not the zone id) —
        # placement order no longer depends on per-zone iteration since pockets are now found
        # in one global pass and may be visited in any deterministic order.
        rng = random.Random(seed ^ (mouth[0] * 92821) ^ (mouth[1] * 131071) ^ 0x9C4)
        # pocket tiles found via `global_true` may include tiles that are physically
        # walkable but NOT placement-eligible (another object's approach/occupied cell) --
        # those can't host a new resource/artifact, so filter to `global_open` here.
        cache_spots = [t for t in pocket if t not in used and t in global_open]
        if not cache_spots:
            continue
        # reserve the neck for the duration of cache placement: a resource/artifact's own
        # footprint carries a decorative overlay cell one tile OFF its own anchor, which can
        # otherwise land exactly on the mouth and steal the guard's only interactive cell.
        used.add(mouth)
        rng.shuffle(cache_spots)
        # nearest-to-the-mouth first, and "the pocket is fully filled with resource" (user's
        # verbatim spec): one tile nearest the mouth is reserved for the artifact, every other
        # pocket tile that can legally hold one gets a resource pile, so even a maximal
        # 16-tile pocket reads as a dense, deliberate treasure room instead of a guard
        # standing next to a few piles in an otherwise-empty space.
        cache_spots.sort(key=lambda t: max(abs(t[0] - mouth[0]), abs(t[1] - mouth[1])))
        art_spots = cache_spots[:1]
        res_spots = cache_spots[1:]
        val = 0                                      # reward value accumulated in this cache
        for t in res_spots:
            if _place_one(objs, used, global_place, rng, st, "RESOURCE_PILE", pool_res,
                          t[0], t[1], cache=True, bounds=bounds):
                val += 2
        if art_spots:
            t = art_spots[0]
            anim, _w, av = rng.choices(PG.RND_ART,
                                       weights=[w for _a, w, _v in PG.RND_ART], k=1)[0]
            if _place_one(objs, used, global_place, rng, st, "REWARD_PICKUP", pool_art,
                          t[0], t[1], ident=ON.identity_of(anim), cache=True, bounds=bounds):
                val += av
        used.discard(mouth)  # release: the precheck guarantees the guard's own placement succeeds
        if val:
            lvl = 1 + (val >= 4) + (val >= 7) + (val >= 10) + (val >= 13)
            gident = PG.rnd_monster(lvl + (1 if rng.random() < 0.25 else 0))
            _place_one(objs, used, global_place, rng, st, "GUARD", None,
                      mouth[0], mouth[1], ident=gident, bounds=bounds)

    return objs, len(blobs)


def place_pickups(ts, zones, zid, terrain, open_set, prot, seed=1, bounds=None):
    """Single-zone convenience wrapper (scatter + pocket caches over just this one zone's
    own reach) — used by tests and any other single-zone caller. Production multi-zone maps
    must NOT use this: call `place_scatter` per zone and `place_pocket_caches` ONCE globally
    instead (see `pp_map.build`), so pocket detection runs against the whole map's reachable
    field rather than one zone's alone."""
    sobjs, sused, reach = place_scatter(ts, zones, zid, terrain, open_set, prot, seed=seed,
                                        bounds=bounds)
    zone_records = [{"zid": zid, "terrain": terrain, "ts": ts, "passable": set(open_set),
                     "open_set": open_set, "reach": reach, "used": sused}]
    cobjs, _n = place_pocket_caches(zone_records, seed=seed, bounds=bounds)
    return sobjs + cobjs


def place_water(ts, zones, zid, seed=1):
    """Populate a WATER zone (spec point: water must not be empty): flotsam/sea chests
    (pickups), buoys/mermaids/sirens (bonus), boats + whirlpools (navigability), shipwrecks/
    derelicts (banks), ocean bottles, and random sea guards. Densities and animation mix come
    from the corpus water pass; identities from the ontology's water pools."""
    import random
    st = PG.mine_gameplay().get("water")
    if not st or not st.get("tiles"):
        return []
    rng = random.Random(seed ^ (zid * 55313) ^ 0x5EA)
    area = len(ts)
    objs, used = [], set()
    for p in PG.WATER_PURPOSES:
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
            ident = (PG.rnd_monster(rng.choices((2, 3, 4, 5), (30, 30, 25, 15))[0])
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
