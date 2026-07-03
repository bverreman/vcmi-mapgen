"""Gameplay object layer — simplified L3 of the marked-point-process spec (§7.1).

Places towns / mines / dwellings / visitable shrines per zone BEFORE vegetation:

  - **counts** come from corpus per-purpose densities (objects per zone tile, mined per
    terrain and cached in ``data/pp/gameplay_stats.json``) — Poisson-style stochastic
    rounding of density x area, with sane per-zone caps,
  - **identities** come from the ontology's `gameplay_pool` (never the corpus); a zone with
    >= 2 mines is guaranteed a sawmill + ore pit first (the H3 economy convention),
  - **placement** is hard-constrained (spec §7.1): the full footprint inside the zone, no
    overlap with other gameplay, and the visitable approach tile free — gameplay objects are
    rigid, unlike vegetation. Objects sit at spread (farthest-point) nodes, towns deepest.

The returned footprint cells + approach tiles feed pp_sample as `forbid` (vegetation may
never bury gameplay) and the approach tiles become mandatory backbone nodes (every object
reachable through the protected web).
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import ontology as ON           # noqa: E402
import zone_engine as ZE        # noqa: E402
import zone_field as ZF         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(ROOT, "data", "pp", "gameplay_stats.json")
STATS_VERSION = 5               # v5: border open fraction + full-front gate distances
MIN_AREA_STATS = 60
TOWN_MIN_AREA = 150             # a town needs a real zone
VISIT_PURPOSES = ("STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA", "INFO")
PICKUP_PURPOSES = ("RESOURCE_PILE", "REWARD_PICKUP", "GUARD")
WATER_PURPOSES = ("REWARD_PICKUP", "BONUS_TEMP", "TRANSPORT", "INFO", "BANK",
                  "WATER_TRANSPORT", "GUARD")
ALL_PURPOSES = (("TOWN", "MINE", "DWELLING", "WATER_TRANSPORT", "TRANSPORT", "BANK")
                + VISIT_PURPOSES + PICKUP_PURPOSES)
# soft caps only guard against pathological zones — corpus densities set the real counts.
# They are BASE floors: the effective cap scales with zone area (`scaled_cap`), so a
# 5000-tile zone is not clamped to the same handful of objects as a 600-tile one.
CAPS = {"TOWN": 1, "MINE": 8, "DWELLING": 6, "VISIT": 12, "BANK": 4}
# the six basic resource mines every map must cover (gold is the deliberate exception:
# only worth placing when the map holds several towns)
BASIC_MINE_RES = ("sawmill", "orePit", "alchemistLab", "sulfurDune",
                  "crystalCavern", "gemPond")
LAND = ("dirt", "sand", "grass", "snow", "swamp", "rough", "subterr", "lava")
MINED_TERR = LAND + ("water",)
EB, GB, OB = 6, 4, 4            # covariate bins: edge-dist, gate-dist, openness

# The H3 mapmaking convention (user-mandated): most placed objects are the editor's RANDOM
# classes — random town/dwelling/monster/resource/artifact — with a few fixed ones. All of
# these have real sprites (checked via ontology.has_animation).
RND_TOWN = "avcranx0"                                    # randomTown
RND_DWELL = "avrcgen0"                                   # randomDwelling (any level)
RND_DWELL_L = tuple(f"avrcgen{i}" for i in range(1, 8))  # randomDwellingLvl 1..7
RND_MON = tuple(f"avwmon{i}" for i in range(1, 8))       # randomMonsterLevel 1..7
RND_RES = "avtrndm0"                                     # randomResource
RND_ART = (("avarnd1", 50, 3), ("avarnd2", 30, 5),       # (anim, pick weight, reward value):
           ("avarnd3", 15, 8), ("avarand", 5, 5))        # treasure/minor/major/any artifact
RANDOM_SHARE = 0.7                                       # towns: random vs fixed split
# guard strength tracks the value guarded: mine guards by resource rarity
MINE_GUARD_LVL = {"sawmill": 1, "orePit": 2, "waterWheel": 1, "windmill": 1,
                  "mysticalGarden": 1, "alchemistLab": 3, "sulfurDune": 3,
                  "gemPond": 4, "crystalCavern": 4, "goldMine": 5, "abandoned": 4}


def rnd_monster(lvl):
    """Random-monster identity of a level, clamped to 1..7."""
    return ON.identity_of(RND_MON[max(1, min(7, int(lvl))) - 1])


def scaled_cap(base, expectation):
    """Area-scaled soft cap: the corpus expectation (density x area) drives the count; the
    cap only stops outliers (1.5x the expectation), never below the base floor."""
    import math
    return max(base, math.ceil(expectation * 1.5))


def _gbin(d):
    return min(d // 3, GB - 1)


def _obin(n_open_5x5):
    return min(n_open_5x5 // 7, OB - 1)


def gate_dist(ts, gates):
    """4-connected BFS steps from the zone's rim gates (corpus + generated zones alike)."""
    import collections as _c
    d = {g: 0 for g in gates if g in ts}
    q = _c.deque(d)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n in ts and n not in d:
                d[n] = d[(x, y)] + 1
                q.append(n)
    return d


def openness(open_set):
    """Per open tile: number of open tiles in its 5x5 window (low = nook/chokepoint)."""
    out = {}
    for (x, y) in open_set:
        out[(x, y)] = sum(1 for dx in range(-2, 3) for dy in range(-2, 3)
                          if (x + dx, y + dy) in open_set)
    return out


def mine_gameplay(force=False):
    """Corpus statistics for the FULL L3 intensity fit, per terrain:

      - per-purpose counts + animation frequencies (density and mix),
      - per-purpose covariate histograms — counts by edge-dist bin, gate-dist bin, and (for
        pickups) OPENNESS bin of the veg-only open field — the sufficient statistics of the
        log-linear intensity  lam_p(u) ∝ exp(th_e[e(u)] + th_g[g(u)] + th_o[o(u)]),
      - tiles per covariate bin (the exposure normalizers),
      - guardedness: fraction of resource piles / pickups / MINES with a GUARD within
        Chebyshev 3,
      - a "water" entry: purpose densities inside water zones (flotsam, buoys, boats,
        shipwrecks, whirlpools, sea guards).
    """
    if not force and os.path.exists(STATS_PATH):
        st = json.load(open(STATS_PATH))
        if st.get("_version") == STATS_VERSION:
            return st
    import ontology as _ON
    Z = lambda: {"tiles": 0, "counts": collections.Counter(),
                 "anim_w": collections.defaultdict(collections.Counter),
                 "e": collections.defaultdict(lambda: [0] * EB),
                 "g": collections.defaultdict(lambda: [0] * GB),
                 "o": collections.defaultdict(lambda: [0] * OB),
                 "tiles_e": [0] * EB, "tiles_g": [0] * GB, "tiles_o": [0] * OB,
                 "guarded": collections.Counter(), "guardable": collections.Counter(),
                 "border_tiles": 0, "border_open": 0}
    acc = {t: Z() for t in MINED_TERR}
    for nm in OR.all_map_names():
        try:
            fm = OR.load_faithful(nm)
        except Exception:
            continue
        zones, zl, _ = ZE._segment_level(fm["terrain"][0])
        guards = {(o["x"], o["y"]) for o in fm["objects"]
                  if o.get("l", 0) == 0 and OR.purpose_of(o) == "GUARD"}
        # water is a segmentation BARRIER (no zones) — mine it as raw tiles per map
        wtiles = {(x, y) for y, row in enumerate(fm["terrain"][0])
                  for x, c in enumerate(row) if c["t"] == 8}
        if wtiles:
            aw = acc["water"]
            aw["tiles"] += len(wtiles)
            for o in fm["objects"]:
                if o.get("l", 0) != 0 or (o["x"], o["y"]) not in wtiles:
                    continue
                p = OR.purpose_of(o)
                if p in ALL_PURPOSES:
                    aw["counts"][p] += 1
                    anim = (o.get("animation") or "").lower().removesuffix(".def")
                    if anim:
                        aw["anim_w"][p][anim] += 1
        for zid, z in zones.items():
            terr = ZE.TNAME.get(z["terrain_type"])
            if terr not in acc or z["area"] < MIN_AREA_STATS:
                continue
            a = acc[terr]
            ts = set(z["tiles_set"])
            a["tiles"] += len(ts)
            ed = ZF.edge_dist(ts)
            # corpus zone "gates" ARE the wide terrain borders — measure gate distance from
            # the FULL contact fronts, matching the generator's wide gate bands (v5)
            fronts = ZF._zone_fronts(ts, zones, zid)
            front_union = set().union(*fronts.values()) if fronts else set()
            gd = gate_dist(ts, front_union or ZF._zone_gates(ts, zones, zid))
            veg_blocked, all_blocked = set(), set()
            zone_objs = [o for o in fm["objects"] if o.get("l", 0) == 0
                         and (o["x"], o["y"]) in ts]
            for o in zone_objs:
                is_decor = OR.purpose_of(o) == "DECORATION"
                anim = (o.get("animation") or "").lower().removesuffix(".def")
                for cx, cy, blk in OR.mask_cells(_ON.mask_of(anim), o["x"], o["y"]):
                    if blk and (cx, cy) in ts:
                        all_blocked.add((cx, cy))
                        if is_decor:
                            veg_blocked.add((cx, cy))
            # how OPEN real zone borders are (sizes the generated gate bands)
            a["border_tiles"] += len(front_union)
            a["border_open"] += sum(1 for t in front_union if t not in all_blocked)
            op = openness(ts - veg_blocked)
            for t in ts:
                a["tiles_e"][min(ed[t], EB - 1)] += 1
                a["tiles_g"][_gbin(gd.get(t, 12))] += 1
                if t in op:
                    a["tiles_o"][_obin(op[t])] += 1
            for o in zone_objs:
                p = OR.purpose_of(o)
                if p not in ALL_PURPOSES:
                    continue
                t = (o["x"], o["y"])
                a["counts"][p] += 1
                anim = (o.get("animation") or "").lower().removesuffix(".def")
                if anim:
                    a["anim_w"][p][anim] += 1
                a["e"][p][min(ed[t], EB - 1)] += 1
                a["g"][p][_gbin(gd.get(t, 12))] += 1
                if t in op:
                    a["o"][p][_obin(op[t])] += 1
                if p in ("RESOURCE_PILE", "REWARD_PICKUP", "MINE"):
                    a["guardable"][p] += 1
                    if any(max(abs(t[0] - gx), abs(t[1] - gy)) <= 3 for gx, gy in guards):
                        a["guarded"][p] += 1
    st = {"_version": STATS_VERSION}
    for t, a in acc.items():
        st[t] = {"tiles": a["tiles"], "counts": dict(a["counts"]),
                 "anim_w": {p: dict(c) for p, c in a["anim_w"].items()},
                 "e": {p: v for p, v in a["e"].items()},
                 "g": {p: v for p, v in a["g"].items()},
                 "o": {p: v for p, v in a["o"].items()},
                 "tiles_e": a["tiles_e"], "tiles_g": a["tiles_g"], "tiles_o": a["tiles_o"],
                 "border_open_frac": (a["border_open"] / a["border_tiles"]
                                      if a["border_tiles"] else 0.5),
                 "guard_frac": {p: (a["guarded"][p] / a["guardable"][p]
                                    if a["guardable"][p] else 0.0)
                                for p in ("RESOURCE_PILE", "REWARD_PICKUP", "MINE")}}
    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
    json.dump(st, open(STATS_PATH, "w"))
    return st


def theta_covariates(st_t, purpose):
    """The L3 counting fit: th[bin] = log of the purpose's relative intensity in that covariate
    bin vs its zone-wide average (Laplace-smoothed, clipped to ±2). Additive across covariates
    — the log-linear model of spec §7.1 with independent covariate effects."""
    import math
    out = {}
    tot = sum(st_t["counts"].get(p, 0) for p in [purpose]) or 1
    base = tot / max(st_t["tiles"], 1)
    for cov, tiles_key, nbins in (("e", "tiles_e", EB), ("g", "tiles_g", GB),
                                  ("o", "tiles_o", OB)):
        cnts = st_t[cov].get(purpose, [0] * nbins)
        th = []
        for b in range(nbins):
            lam_b = (cnts[b] + 0.5) / (st_t[tiles_key][b] + 0.5 / max(base, 1e-9))
            th.append(max(-2.0, min(2.0, math.log(lam_b / base))))
        out[cov] = th
    return out


def _cells(ident, ax, ay):
    """(all_cells, blocking_cells, approach) of an identity anchored at (ax, ay); approach is
    the tile a hero stands on to visit ('X' = enter from below; 'A' = the tile itself)."""
    allc, blk, approach = [], [], None
    rows = ident["mask"]
    hh = len(rows)
    for r, row in enumerate(rows):
        for ci, ch in enumerate(row):
            if ch == " ":
                continue
            tx, ty = ax - ci, ay - (hh - 1 - r)
            allc.append((tx, ty))
            if ch in ("B", "X"):
                blk.append((tx, ty))
            if ch == "X":
                approach = (tx, ty + 1)
            elif ch == "A" and approach is None:
                approach = (tx, ty)
    return allc, blk, approach


GAP = 2                         # free tiles kept between any two gameplay footprints — gameplay
                                # neighbours VEGETATION (which fills the gap), not other gameplay


def _fits(ident, ax, ay, ts, occupied, near, reserved):
    """Legality: whole footprint in-zone, at least GAP free tiles from every other gameplay
    footprint (`near` = existing cells inflated by GAP), no squatting on an earlier object's
    approach tile (`reserved`), own approach tile in-zone and standable."""
    allc, blk, approach = _cells(ident, ax, ay)
    if approach is None:
        return None
    for cell in allc:
        if cell not in ts or cell in near or cell in reserved:
            return None
    if approach not in ts or approach in occupied or approach in blk:
        return None
    return allc, blk, approach


_SPIRAL = sorted(((dx, dy) for dx in range(-5, 6) for dy in range(-5, 6)),
                 key=lambda d: (max(abs(d[0]), abs(d[1])), d))


def _intensity_weights(ts, purpose, st_t, ed, gd, op=None):
    """Per-tile placement intensity  w(u) = exp(th_e + th_g (+ th_o))  from the L3 fit."""
    import math
    th = theta_covariates(st_t, purpose)
    w = {}
    for t in sorted(ts):
        s = th["e"][min(ed[t], EB - 1)] + th["g"][_gbin(gd.get(t, 12))]
        if op is not None:
            s += th["o"][_obin(op[t])] if t in op else -2.0
        w[t] = math.exp(s)
    return w


def place_zone(ts, zones, zid, terrain, seed=1, coastal=frozenset(), force_town=False,
               ledger=None):
    """Gameplay objects for one zone. Returns (objs, occupied, blocked, approaches):
    `occupied` = every footprint cell (no vegetation there), `blocked` = the impassable
    subset (the walkable web must route around these; approach tiles are never in it).
    objs carry template + purpose. Deterministic in `seed`.

    Anchors are SAMPLED FROM THE FITTED INTENSITY (towns deep and gate-far, mines mid-depth,
    shrines near routes — whatever the corpus says), not from uniform spread nodes; the GAP
    separation keeps the pattern from clumping.

    Conventions (user-mandated, matching real H3 mapmaking): towns/dwellings are mostly the
    editor's RANDOM classes; a zone with a town always gets a sawmill + ore pit placed NEXT
    TO the town (the start economy); mines get a guard on their approach with the corpus
    guardedness probability (strength ~ resource rarity); every zone gate — the corpus-wide
    open band into the neighbouring zone — gets a random monster at its centre with prob
    0.65 (strength ~ zone size); creature BANKS (utopias, conservatories, crypts) place like
    visitables but carry no extra guard (the bank IS the fight); a coastal zone may get a
    shipyard on the shore (`coastal` = zone tiles touching water). `force_town=True`
    guarantees the zone a town (a designated PLAYER zone).

    `ledger` (optional, shared across the whole map, zones visited in sorted-zid order)
    makes mine types a MAP-level economy: {"missing": set of BASIC_MINE_RES not yet placed
    anywhere, "towns": towns expected+placed so far, "gold": gold mines placed so far}.
    Zones draw globally-missing resource types first, and a gold mine may only be drawn
    while gold < towns - 1 (gold is worth placing only on multi-town maps)."""
    import random
    st = mine_gameplay()[terrain]
    dens = {p: c / max(st["tiles"], 1) for p, c in st["counts"].items()}
    area = len(ts)
    rng = random.Random(seed ^ (zid * 40503) ^ 0x5EED)

    def stoch(x, base_cap):
        n = int(x) + (1 if rng.random() < x - int(x) else 0)
        return min(n, scaled_cap(base_cap, x))

    n_town = 1 if (force_town or (area >= TOWN_MIN_AREA
                   and rng.random() < min(1.0, dens.get("TOWN", 0) * area))) else 0
    n_mine = stoch(dens.get("MINE", 0) * area, CAPS["MINE"])
    if n_town:                                       # a town always brings its economy pair
        n_mine = max(n_mine, 2)
    n_dwell = stoch(dens.get("DWELLING", 0) * area, CAPS["DWELLING"])
    n_visit = stoch(sum(dens.get(p, 0) for p in VISIT_PURPOSES) * area, CAPS["VISIT"])
    n_bank = stoch(dens.get("BANK", 0) * area, CAPS["BANK"])
    if ledger is not None and n_town and not force_town:
        ledger["towns"] = ledger.get("towns", 0) + 1   # player towns are pre-counted by build

    # FIXED identities from the ontology pools: corpus frequency sqrt-damped and repeats
    # penalized, so rare visitables (star axis, gardens, libraries) actually show up
    used_anims = set()

    def pick(pool, purpose=None):
        pool = sorted((i for i in pool if "random" not in str(i.get("type", "")).lower()),
                      key=lambda i: i["animation"])
        if not pool:
            return None
        w = st["anim_w"].get(purpose, {}) if purpose else {}
        weights = [(w.get(i["animation"].lower(), 0) ** 0.5 + 0.3)
                   * (0.05 if i["animation"].lower() in used_anims else 1.0) for i in pool]
        ident = rng.choices(pool, weights=weights, k=1)[0]
        used_anims.add(ident["animation"].lower())
        return ident

    wanted = []                                       # (purpose, ident), placement order
    if n_town:
        ident = (ON.identity_of(RND_TOWN) if rng.random() < RANDOM_SHARE
                 else pick(ON.gameplay_pool(terrain, "TOWN"), "TOWN"))
        if ident:
            wanted.append(("TOWN", ident))
    # mines: wood + ore guaranteed first (the H3 economy convention), then DISTINCT further
    # resource types without replacement — a zone gets a gold mine AND a gem pond, not three
    # sawmills (corpus zones rarely duplicate a mine type). With a map `ledger`, globally
    # MISSING basic resources are drawn first (all six minerals covered map-wide) and gold
    # is rationed to towns - 1 (gold mines pay off only on multi-town maps).
    mines = ON.mines_by_resource(terrain)
    mine_w = st["anim_w"].get("MINE", {})
    mine_idents, used_res = [], set()
    if n_mine >= 2:
        for res in ("sawmill", "orePit"):
            if mines.get(res):
                mine_idents.append(pick(mines[res], "MINE"))
                used_res.add(res)
                if ledger is not None:
                    ledger["missing"].discard(res)
    while len(mine_idents) < n_mine:
        gold_ok = ledger is None or ledger["gold"] < max(0, ledger.get("towns", 0) - 1)
        rest = {res: ids for res, ids in mines.items()
                if res not in used_res and ids
                and res not in ("abandoned", "mine")   # both abandoned-mine variants
                and (res != "goldMine" or gold_ok)}
        if not rest:
            break
        missing = (ledger["missing"] & set(rest)) if ledger is not None else set()
        keys = sorted(missing) if missing else sorted(rest)
        rw = [sum(mine_w.get(i["animation"].lower(), 0) for i in rest[k]) + 0.2 for k in keys]
        res = rng.choices(keys, weights=rw, k=1)[0]
        mine_idents.append(pick(rest[res], "MINE"))
        used_res.add(res)
        if ledger is not None:
            ledger["missing"].discard(res)
            if res == "goldMine":
                ledger["gold"] += 1
    wanted += [("MINE", i) for i in mine_idents if i]
    # dwellings are mostly random (generic or by level, skewed low); fixed dwellings are
    # the deliberate exception in real maps
    pool_dw = ON.gameplay_pool(terrain, "DWELLING")
    for _ in range(n_dwell):
        if rng.random() < 0.8:
            anim = (RND_DWELL if rng.random() < 0.3
                    else rng.choices(RND_DWELL_L, weights=(22, 18, 15, 13, 12, 10, 10), k=1)[0])
            ident = ON.identity_of(anim)
        else:
            ident = pick(pool_dw, "DWELLING")
        if ident:
            wanted.append(("DWELLING", ident))
    # creature banks (utopias, conservatories, crypts, pyramids) — corpus density, no
    # approach guard: the bank IS the fight, its reward is its own
    for _ in range(n_bank):
        ident = pick(ON.gameplay_pool(terrain, "BANK"), "BANK")
        if ident:
            wanted.append(("BANK", ident))
    vw = [st["counts"].get(p, 0) + 0.2 for p in VISIT_PURPOSES]
    for _ in range(n_visit):
        p = rng.choices(VISIT_PURPOSES, weights=vw, k=1)[0]
        ident = pick(ON.gameplay_pool(terrain, p), p)
        if ident:
            wanted.append((p, ident))

    if not wanted:
        return [], set(), set(), []

    # anchor sampling from the fitted per-purpose intensity (the L3 fit applied).
    # Gates are corpus-wide BANDS of the contact front (not 1-tile corridors) — gate
    # distance is measured from the whole open band, matching the v5 corpus mining.
    ed = ZF.edge_dist(ts)
    gate_bands = ZF._zone_gate_bands(ts, zones, zid,
                                     open_frac=st.get("border_open_frac", 0.5))
    band_union = set().union(*(b for _r, b in gate_bands)) if gate_bands else set()
    gd = gate_dist(ts, band_union)
    tiles_sorted = sorted(ts)
    wcache = {}

    objs, occupied, blocked, approaches = [], set(), set(), []
    near = set()                                     # occupied inflated by GAP (separation zone)

    def emit(purpose, ident, x, y):
        objs.append({"x": x, "y": y, "l": 0, "purpose": purpose,
                     "type": ident.get("type"), "subtype": ident.get("subtype"),
                     "animation": ident["animation"], "mask": ident["mask"],
                     "template": {"animation": ident["animation"], "mask": ident["mask"]}})

    def settle(purpose, ident, fit, node):
        allc, blk, approach = fit
        occupied.update(allc)
        blocked.update(blk)
        for (cx2, cy2) in allc:                      # inflate: keep the next object GAP away
            for gx in range(-GAP, GAP + 1):
                for gy in range(-GAP, GAP + 1):
                    near.add((cx2 + gx, cy2 + gy))
        approaches.append(approach)
        emit(purpose, ident, node[0], node[1])
        return approach

    town_center = None                               # set once the zone's town settles
    town_mines_left = 2 if n_town else 0             # sawmill + ore pit anchor NEAR the town
    for purpose, ident in wanted:
        if purpose == "TOWN" and force_town:
            # PLAYER start town: anchored so the footprint CENTER sits on the zone
            # centroid (masks anchor bottom-right, hence the +(w-1)/2 offset). The scan is
            # exhaustive nearest-first over the whole zone — the town is GUARANTEED to
            # place whenever the zone admits its footprint anywhere at all.
            mh = len(ident["mask"])
            mw = max(len(r) for r in ident["mask"])
            ccx = sum(t[0] for t in ts) / area + (mw - 1) / 2.0
            ccy = sum(t[1] for t in ts) / area + (mh - 1) / 2.0
            cands = sorted(ts, key=lambda t: ((t[0] - ccx) ** 2 + (t[1] - ccy) ** 2, t))
            spiral = ()                              # already exhaustive: no nudge needed
        elif purpose == "MINE" and town_mines_left > 0 and town_center is not None:
            # the town's economy pair (sawmill + ore pit, first two MINE entries): an
            # exhaustive nearest-first scan around the town — as close as legality (GAP,
            # approach) admits, guaranteed to place whenever the zone fits it at all
            town_mines_left -= 1
            cands = sorted(ts, key=lambda t: ((t[0] - town_center[0]) ** 2
                                              + (t[1] - town_center[1]) ** 2, t))
            spiral = ()
        else:
            if purpose not in wcache:
                wcache[purpose] = _intensity_weights(ts, purpose, st, ed, gd)
            wmap = wcache[purpose]
            weights = [wmap[t] for t in tiles_sorted]
            cands = rng.choices(tiles_sorted, weights=weights, k=80)
            spiral = _SPIRAL[:25]
        for node in cands:
            fit = _fits(ident, node[0], node[1], ts, occupied, near, set(approaches))
            if fit is None:                          # nudge: try a tight spiral at the sample
                for dx, dy in spiral:
                    fit = _fits(ident, node[0] + dx, node[1] + dy, ts,
                                occupied, near, set(approaches))
                    if fit:
                        node = (node[0] + dx, node[1] + dy)
                        break
            if fit:
                approach = settle(purpose, ident, fit, node)
                if purpose == "TOWN":                # the economy pair anchors around this
                    mh = len(ident["mask"])
                    mw = max(len(r) for r in ident["mask"])
                    town_center = (node[0] - (mw - 1) / 2.0, node[1] - (mh - 1) / 2.0)
                # mines are typically guarded: a monster ON the approach (fight to flip),
                # strength ~ resource rarity, probability = corpus mine guardedness
                if purpose == "MINE" and rng.random() < st["guard_frac"].get("MINE", 0.5):
                    lvl = MINE_GUARD_LVL.get(str(ident.get("subtype")), 2)
                    gident = rnd_monster(lvl + (1 if rng.random() < 0.25 else 0))
                    emit("GUARD", gident, approach[0], approach[1])
                    occupied.add(approach)           # no vegetation/pickup may stack there
                break

    # a shipyard on the shore of a coastal zone (mined WATER_TRANSPORT density, boosted for
    # the coastal-only condition) — makes the adjacent water actually navigable
    if coastal and rng.random() < min(0.8, dens.get("WATER_TRANSPORT", 0) * area * 3):
        pool = ([i for i in ON.gameplay_pool(terrain, "WATER_TRANSPORT")
                 if i.get("type") == "shipyard"] or [ON.identity_of("avxshyd0")])
        ident = pool[0]
        cand = sorted(coastal)
        rng.shuffle(cand)
        for c in cand[:150]:
            fit = _fits(ident, c[0], c[1], ts, occupied, near, set(approaches))
            if fit:
                settle("WATER_TRANSPORT", ident, fit, c)
                break

    # zone-edge guards: each gate band (the corpus-wide open border into a neighbouring
    # zone) is guarded at its CENTRE with prob 0.65 by a random monster whose level scales
    # with the zone's size — one guard per passage, standing in the open like real maps,
    # not a cork in a corridor
    for rep, _band in sorted(gate_bands):
        if rep not in ts or rep in occupied or rng.random() > 0.65:
            continue
        lvl = min(7, 1 + area // 250 + (1 if rng.random() < 0.4 else 0))
        emit("GUARD", rnd_monster(lvl), rep[0], rep[1])
        occupied.add(rep)

    return objs, occupied, blocked, approaches


# purposes deliberately NOT reproduced by the generator (the audit's whitelist)
AUDIT_EXCLUDED = {
    "TRANSPORT": "relational (monolith/portal pairing) — out of scope, spec §19",
    "GUARD": "guards are leveled RANDOM monsters by design, never corpus identities",
}
# corpus sprite VARIANTS of ontology objects: the fort-less 'village' town sprites are the
# same gameplay object as the editor's forted '..x0' towns (fort state is a town option,
# not a distinct visitable) — the audit treats them as reachable through their canonical
TOWN_SPRITE_VARIANTS = {
    "avcrand0": "avcranx0", "avccast0": "avccasx0", "avcramp0": "avcramx0",
    "avctowr0": "avctowx0", "avcinft0": "avcinfx0", "avcnecr0": "avcnecx0",
    "avcdung0": "avcdunx0", "avcstro0": "avcstrx0", "avcftrt0": "avcftrx0",
    "avchfor0": "avchforx",
}
# purposes the generator actually places on land (BANK included since the land-bank change)
PLACED_PURPOSES = (set(VISIT_PURPOSES) | set(PICKUP_PURPOSES)
                   | {"TOWN", "MINE", "DWELLING", "BANK", "WATER_TRANSPORT"}) \
                  - set(AUDIT_EXCLUDED)


def audit_variety():
    """Corpus-variety audit: every (purpose, animation) with a nonzero corpus count on land
    must (a) resolve through the ontology and (b) be reachable through a generator pool —
    i.e. its purpose is placed and the animation sits in `gameplay_pool` for at least one
    land terrain (or it is an editor RANDOM class, placed by convention). Returns a list of
    gap dicts (empty = the generated maps can reach the corpus's full visitable variety)."""
    st = mine_gameplay()
    seen = {}                                        # (purpose, anim) -> total corpus count
    for terr in LAND:
        for p, anims in st[terr]["anim_w"].items():
            if p in AUDIT_EXCLUDED:
                continue
            for anim, cnt in anims.items():
                seen[(p, anim)] = seen.get((p, anim), 0) + cnt
    pool_anims = {}                    # purpose -> anims reachable on ANY terrain incl water
    for p in {p for p, _a in seen}:
        pool_anims[p] = {i["animation"].lower() for t in LAND + ("water",)
                         for i in ON.gameplay_pool(t, p)}
    gaps = []
    for (p, anim), cnt in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])):
        anim = TOWN_SPRITE_VARIANTS.get(anim, anim)
        ident = ON.identity_of(anim)
        if not ON.has_animation(anim):
            gaps.append({"purpose": p, "anim": anim, "count": cnt,
                         "why": "animation missing from the ontology"})
        elif p not in PLACED_PURPOSES:
            gaps.append({"purpose": p, "anim": anim, "count": cnt,
                         "why": f"purpose {p} not placed by the generator"})
        elif ("random" not in str(ident.get("type", "")).lower()
              and anim not in pool_anims[p]):
            gaps.append({"purpose": p, "anim": anim, "count": cnt,
                         "why": "not in gameplay_pool for any land terrain"})
    return gaps


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true",
                    help="corpus-variety audit: report corpus objects the generator cannot "
                         "reproduce (empty output = full variety reachable)")
    args = ap.parse_args()
    st = mine_gameplay()
    if args.audit:
        gaps = audit_variety()
        for reason, note in AUDIT_EXCLUDED.items():
            print(f"excluded {reason}: {note}")
        if not gaps:
            print("AUDIT OK: every corpus (purpose, animation) on land is reachable")
        else:
            print(f"AUDIT: {len(gaps)} gaps")
            for g in gaps:
                print(f"  {g['purpose']:<15} {g['anim']:<10} corpus n={g['count']:>5}  "
                      f"{g['why']}")
        raise SystemExit(0 if not gaps else 1)
    for t in LAND:
        d = st[t]
        dens = {p: round(c / max(d["tiles"], 1) * 1000, 2) for p, c in d["counts"].items()}
        print(f"{t:<8} tiles={d['tiles']:>7}  per-1000-tiles: {dens}  "
              f"border_open={d['border_open_frac']:.2f}")
        print(f"         guard_frac={d['guard_frac']}")
