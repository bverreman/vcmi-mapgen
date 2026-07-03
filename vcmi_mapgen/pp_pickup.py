"""Pickup layer (L4): resources, artifacts and monster guards over the finished open field.

Runs AFTER vegetation — the open field is known, so the classic H3 treasure grammar applies:

  - **unguarded scatter** — most resource piles lie in the open along routes (corpus: the
    guarded fraction of resource piles is the minority); sampled from the L3 intensity fit
    (edge/gate/openness covariates) over reachable open tiles, with a small separation,
  - **guarded caches** — the corpus guarded fraction goes into POCKETS: deep, low-openness
    reachable tiles far from the walkable web. Each cache holds 1–3 resource piles (plus an
    artifact when the budget allows) and a GUARD monster on the cache's mouth — the tile of
    the pocket nearest the web, so the guard actually gates the treasure,
  - **roaming guards** — leftover GUARD budget goes to open tiles off the protected web,
    spread out (they may pinch optional lanes, never the mandatory backbone).

Identities come from `ontology.gameplay_pool` (RESOURCE_PILE / REWARD_PICKUP / GUARD),
weighted by corpus animation frequency; random-* editor placeholders are skipped.
Everything is deterministic in `seed`.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON           # noqa: E402
import pp_gameplay as PG        # noqa: E402
import zone_field as ZF         # noqa: E402

CAPS = {"RESOURCE_PILE": 16, "REWARD_PICKUP": 8, "GUARD": 9}   # base floors; caps scale
POCKET_DWEB = 3                 # a pocket starts this many open-BFS steps off the web
POCKET_OPEN = 12                # ... and is at most this open in its 5x5 window (a nook)
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


def _legal(ident, x, y, open_set, used):
    """A pickup/guard footprint must sit fully on unused open tiles."""
    cells = [(x - c, y - (len(ident["mask"]) - 1 - r))
             for r, row in enumerate(ident["mask"]) for c, ch in enumerate(row) if ch != " "]
    if all(cell in open_set and cell not in used for cell in cells):
        return cells
    return None


def place_pickups(ts, zones, zid, terrain, open_set, prot, seed=1):
    """Sample the pickup layer for one zone. Returns list of objs (with purpose)."""
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
    n_mon = stoch(dens["GUARD"] * area, PG.scaled_cap(CAPS["GUARD"], dens["GUARD"] * area))
    if area >= LOOT_FLOOR_AREA:                      # a real zone always holds some loot
        n_art = max(n_art, 2)
    if not (n_res or n_art or n_mon):
        return []

    dweb = _web_dist(open_set, prot)
    reach = set(dweb)                                # reachable open tiles only
    op = PG.openness(open_set)
    ed = ZF.edge_dist(ts)
    bands = ZF._zone_gate_bands(ts, zones, zid,
                                open_frac=st.get("border_open_frac", 0.5))
    gd = PG.gate_dist(ts, set().union(*(b for _r, b in bands)) if bands else set())

    pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
    pool_art = ON.gameplay_pool(terrain, "REWARD_PICKUP")
    pool_mon = ON.gameplay_pool(terrain, "GUARD")

    objs, used = [], set()

    def put(purpose, pool, x, y, ident=None, art_share=0.45):
        ident = ident or _pick(pool, purpose, st, rng, art_share=art_share)
        if ident is None:
            return False
        cells = _legal(ident, x, y, reach, used)
        if cells is None:
            return False
        used.update(cells)
        o = {"x": x, "y": y, "l": 0, "purpose": purpose,
             "type": ident.get("type"), "subtype": ident.get("subtype"),
             "animation": ident["animation"], "mask": ident["mask"],
             "template": {"animation": ident["animation"], "mask": ident["mask"]}}
        if purpose == "GUARD":       # absent => VCMI 'compliant' => every creature joins free
            o["options"] = {"character": "hostile"}
        objs.append(o)
        return True

    # ---- guarded caches in pockets: guard STRENGTH tracks the cache VALUE -----------------
    g_res = round(st["guard_frac"].get("RESOURCE_PILE", 0.3) * n_res)
    g_art = round(st["guard_frac"].get("REWARD_PICKUP", 0.5) * n_art)
    pocket_cands = sorted((t for t in reach
                           if dweb[t] >= POCKET_DWEB and op.get(t, 25) <= POCKET_OPEN),
                          key=lambda t: (-dweb[t], t))
    pocket_used = set()
    while (g_res > 0 or g_art > 0) and n_mon > 0 and pocket_cands:
        center = next((t for t in pocket_cands if t not in pocket_used), None)
        if center is None:
            break
        spots = sorted(t for t in reach if max(abs(t[0] - center[0]), abs(t[1] - center[1])) <= 2
                       and t not in used)
        pocket_used.update(t for t in pocket_cands
                           if max(abs(t[0] - center[0]), abs(t[1] - center[1])) <= 6)
        if len(spots) < 2:
            continue
        # the guard sits on the pocket's MOUTH: its reachable spot nearest the web
        mouth = min(spots, key=lambda t: (dweb[t], t))
        cache_spots = [t for t in spots if t != mouth]
        rng.shuffle(cache_spots)
        val = 0                                      # reward value accumulated in this cache
        for t in cache_spots[:min(3, max(1, g_res))]:
            if g_res > 0 and put("RESOURCE_PILE", pool_res, t[0], t[1]):
                g_res -= 1; n_res -= 1; val += 2
        if g_art > 0 and cache_spots:
            t = cache_spots[-1]
            anim, _w, av = rng.choices(PG.RND_ART,
                                       weights=[w for _a, w, _v in PG.RND_ART], k=1)[0]
            if put("REWARD_PICKUP", pool_art, t[0], t[1], ident=ON.identity_of(anim)):
                g_art -= 1; n_art -= 1; val += av
        if val:
            lvl = 1 + (val >= 4) + (val >= 7) + (val >= 10) + (val >= 13)
            gident = PG.rnd_monster(lvl + (1 if rng.random() < 0.25 else 0))
            if put("GUARD", None, mouth[0], mouth[1], ident=gident):
                n_mon -= 1

    # ---- unguarded scatter (intensity-weighted, near routes) ------------------------------
    # rewards here are LOOT (treasure chests, campfires — the corpus mix), not artifacts:
    # the tiered random artifacts sit behind the cache guards above
    def scatter(purpose, pool, n, min_sep, allow_web, off_web=False):
        if n <= 0:
            return
        wmap = PG._intensity_weights(reach, purpose, st, ed, gd, op=op)
        cands = sorted(reach)
        weights = [wmap[t] for t in cands]
        placed = []
        for t in rng.choices(cands, weights=weights, k=60 * n):
            if len(placed) >= n:
                break
            if t in used or (not allow_web and t in prot) or (off_web and dweb[t] < 1):
                continue
            if any(max(abs(t[0] - q[0]), abs(t[1] - q[1])) < min_sep for q in placed):
                continue
            ident = (PG.rnd_monster(rng.choices((1, 2, 3, 4), (30, 30, 25, 15))[0])
                     if purpose == "GUARD" else None)     # roamers are weak-mid random monsters
            if put(purpose, pool, t[0], t[1], ident=ident, art_share=SCATTER_ART_SHARE):
                placed.append(t)

    scatter("RESOURCE_PILE", pool_res, n_res, min_sep=3, allow_web=True)
    scatter("REWARD_PICKUP", pool_art, n_art, min_sep=5, allow_web=True)
    scatter("GUARD", pool_mon, n_mon, min_sep=7, allow_web=False, off_web=True)
    return objs


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
