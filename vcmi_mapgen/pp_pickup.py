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

# Artifact tier (animation name from RND_ART) indexed by monster level 1-6:
# treasure(1-2) → minor(3) → major(4-5) → any/relic(6).
# The monster level is derived from resources + visitable structures placed in the pocket;
# the artifact at the deepest tile then matches that level so the guard's strength is
# always proportional to the prize behind it.
_ART_BY_LVL = ["avarnd1", "avarnd1", "avarnd2", "avarnd3", "avarnd3", "avarand"]

_SOLO_VIS_PURPOSES = ("BONUS_TEMP", "SPELL_SKILL", "MANA", "STAT_PERMANENT")
LOOT_FLOOR_AREA = 300           # a real zone always yields a couple of unguarded loots
POCKET_MIN_SEP = 4              # Chebyshev distance between accepted cache guards, applied
                                # to TINY (1-2 tile) pockets only: the ZoC-neck detector
                                # legitimately flags every concave wall corner as a 1-tile
                                # nook (locally identical to a flat-face recess), so without
                                # thinning, a long wall run grows a guard at every kink.
                                # Real (3+ tile) pockets stay deterministic — every one gets
                                # its cache, per the module doctrine above.

LOOT_ZONE_MAX_TILES = 80        # land zone with ≤ this many tiles, exactly one entrance cluster, no town

_LOOT_COLORS = [                # (border_gate_anim, keymaster_anim); index == VCMI subtype 0-7
    ("avxbgt00", "avxkey00"),   # 0 light blue
    ("avxbgt10", "avxkey10"),   # 1 green
    ("avxbgt20", "avxkey20"),   # 2 red
    ("avxbgt30", "avxkey30"),   # 3 dark blue
    ("avxbgt40", "avxkey40"),   # 4 brown
    ("avxbgt50", "avxkey50"),   # 5 purple
    ("avxbgt60", "avxkey60"),   # 6 white
    ("avxbgt70", "avxkey70"),   # 7 black
]
_LOOT_ART_W = {"avarnd1": 5, "avarnd2": 15, "avarnd3": 35, "avarand": 45}
_LOOT_EXCL_DECOR = frozenset({"LAKE", "FROZEN_LAKE", "RIVER_DELTA", "KELP", "REEF", "LAKE_2"})
# Visitable structures excluded from BOTH pocket caches and loot zone fill.
_FILL_EXCL_ANIMS = frozenset({"avsfntn0", "avsidol0"})  # Fountain of Fortune, Idol of Fortune
# Only shrines teaching spells at level ≥ 3 are placed in loot zones (level 1-2 are too weak).
_LOOT_SHRINE_MIN_LEVEL = 3
# Vis-pool entries excluded from loot zones only (still allowed in pockets with sep. constraint).
_LOOT_VIS_EXCL_ANIMS = frozenset({"avxwelg0", "avxwelr0", "avxwlsn0"})  # Magic Well
# REWARD_PICKUP types excluded from loot zone art/chest fill (pool_art + pool_chest).
_LOOT_ART_EXCL_TYPES = frozenset({"leanTo", "wagon", "warriorTomb", "denOfThieves"})
# Types that must maintain a minimum map-fraction separation between any two instances in pockets.
_POCKET_SPACED_TYPES = frozenset({"magicWell", "warriorTomb"})
# Two-way monolith pairs for sealed teleport loot zones (ci > 0).
# Both ends of each pair use the SAME animation → same subtype → they teleport to each other.
# Subtypes monolith1-4 (simple 1-4 cell, no blocking body) suit small pockets best.
_LOOT_MONOLITHS = ["avxmn2g0", "avxmn2o0", "avxmn2p0", "avxmn4b0"]


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


def _dedupe_pockets(pockets, reach=frozenset()):
    """Collapse near-duplicate mouth candidates into one CANDIDATE LIST per genuine physical
    nook. `ZF.find_pockets` returns one entry per candidate MOUTH tile, but several nearby
    tiles each independently qualify as "the" guard spot of the same nook (a ZoC-neck is 3x3,
    so a flat-face nook alone yields ~4 candidates) -- and in H3 a guard already threatens
    every adjacent tile (stepping next to a wandering monster forces combat), so one guard
    placed at a shared neck already gates every mouth candidate touching it. Merge
    guard_tile+pocket tiles into 4-connected blobs (union-find over shared tiles).

    `pockets` maps guard_tile -> (pocket_frozenset, mouth_frozenset) as returned by
    `ZF.find_pockets`.

    Returns a list of candidate lists (one list per nook), each sorted by `ZF.mouth_key`
    over `reach` (in-neck first, then largest pocket, then orthogonal-front), outer list
    sorted best-top-candidate first. Each candidate is a (guard_tile, pocket, mouth_fs)
    triple. The caller tries candidates within a blob in order and falls back to the next
    one when the top pick's mouth tile is unusable."""
    items = [(g, pocket, mouth_fs) for g, (pocket, mouth_fs) in pockets.items()]
    owner = collections.defaultdict(list)
    for idx, (g, pocket, mouth_fs) in enumerate(items):
        for t in (g, *pocket):
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
    for idx, (g, pocket, mouth_fs) in enumerate(items):
        groups[find(idx)].append((g, pocket, mouth_fs))
    blobs = [sorted(cands, key=lambda kv: ZF.mouth_key(reach, kv[0], kv[1]))
             for cands in groups.values()]
    return sorted(blobs, key=lambda cands: ZF.mouth_key(reach, cands[0][0], cands[0][1]))


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


PANDORA_CREATURES = ("pikeman", "centaur", "gremlin", "imp", "skeleton",
                    "troglodyte", "goblin", "gnoll", "peasant")  # vanilla tier-1 dwelling
                    # creatures, one per RoE town plus the neutral peasant -- a modest
                    # unguarded-scatter payload, not cache-treasure tier.

_RW_TEXT = {"exactStrings": None, "localStrings": None, "message": None,
           "numbers": None, "stringsTextID": None}
_RW_LIMITER = {"allOf": [], "anyOf": [], "artifacts": [], "creatures": [], "dayOfWeek": 0,
              "daysPassed": 0, "heroExperience": 0, "heroLevel": -1, "manaPercentage": 0,
              "manaPoints": 0, "movePercentage": 0, "movePoints": 0, "noneOf": [],
              "primary": [0, 0, 0, 0], "secondary": []}
_RW_REWARD = {"creatures": [], "creaturesChange": [], "heroExperience": 0, "heroLevel": 0,
             "manaDiff": 0, "manaOverflowFactor": 0, "manaPercentage": -1,
             "moveOverflowFactor": 0, "movePercentage": -1, "movePoints": 0,
             "primary": [0, 0, 0, 0], "resources": {}, "secondary": [],
             "spellCast": {"level": 0}}


def _pandora_reward(rng):
    """A VCMI 'Rewardable' payload for a pandoraBox (schema captured verbatim from a real
    VCMI-RMG .vmap: `options.rewardable.info[].reward` alongside a sibling all-null
    `guardMessage`). Without this an unconfigured pandoraBox is legal but permanently
    empty -- every field defaults to 0/-1/null, which is a no-op reward. Kept modest
    (gold/experience/a small creature stack): this fires from the unguarded-scatter loot
    pool, not a guarded cache."""
    reward = dict(_RW_REWARD)
    flavor = rng.choices(("gold", "experience", "creatures"), weights=(45, 30, 25), k=1)[0]
    if flavor == "gold":
        reward["resources"] = {"gold": rng.choice((500, 1000, 1500, 2000, 3000, 5000))}
    elif flavor == "experience":
        reward["heroExperience"] = rng.choice((1000, 1500, 2500, 5000, 7500, 10000))
    else:
        reward["creatures"] = [{"type": f"core:{rng.choice(PANDORA_CREATURES)}",
                                "amount": rng.randint(3, 10)}]
    return {
        "guardMessage": dict(_RW_TEXT),
        "rewardable": {
            "info": [{"limiter": dict(_RW_LIMITER), "message": dict(_RW_TEXT),
                     "reward": reward, "visitType": 1}],
            "infoWindowType": 0,
            "onSelect": dict(_RW_TEXT),
            "resetParameters": {"period": 0},
            "selectMode": "selectFirst",
            "visitMode": "unlimited",
        },
    }


def _seerhut_reward(rng):
    """The seer hut's own `options.rewardable` payout, paid once its quest's artifact
    condition is met -- same flavour draw as `_pandora_reward` but a tier up (VCMI's own
    RMG seer-hut samples pay in the 5-figure XP / dozens-of-creatures range, well above
    pandora's open-scatter tier: a seer hut costs the hero a whole side-quest, not a
    five-second detour)."""
    reward = dict(_RW_REWARD)
    flavor = rng.choices(("gold", "experience", "creatures"), weights=(35, 40, 25), k=1)[0]
    if flavor == "gold":
        reward["resources"] = {"gold": rng.choice((3000, 5000, 7500, 10000, 15000))}
    elif flavor == "experience":
        reward["heroExperience"] = rng.choice((2500, 5000, 7500, 10000, 15000))
    else:
        reward["creatures"] = [{"type": f"core:{rng.choice(PANDORA_CREATURES)}",
                                "amount": rng.randint(5, 20)}]
    return reward


def _seerhut_quest(rng, artifact_subtype):
    """VCMI 'Quest' + 'Rewardable' payload for a seerHut (schema captured verbatim from two
    real VCMI-RMG .vmap seerHut instances): a MISSION_ARTIFACT quest -- the hero must be
    CARRYING one specific named artifact -- gated via `quest.limiter.artifacts`. The sibling
    `rewardable.info[]` entry (paid out once the quest is satisfied) keeps the plain no-op
    base limiter: the artifact CHECK lives only in `quest.limiter`, confirmed against both
    reference instances, whose own `rewardable` limiter carries no `artifacts` restriction of
    its own."""
    quest_limiter = dict(_RW_LIMITER, artifacts=[f"core:{artifact_subtype}"])
    return {
        "quest": {
            "completedText": dict(_RW_TEXT),
            "firstVisitText": dict(_RW_TEXT),
            "limiter": quest_limiter,
            "nextVisitText": dict(_RW_TEXT),
        },
        "rewardable": {
            "info": [{"limiter": dict(_RW_LIMITER), "message": dict(_RW_TEXT),
                     "reward": _seerhut_reward(rng), "visitType": 1}],
            "infoWindowType": 0,
            "onSelect": dict(_RW_TEXT),
            "resetParameters": {"period": 0},
            "selectMode": "selectFirst",
            "visitMode": "unlimited",
        },
    }


def _place_one(objs, used, reach, rng, st, purpose, pool, x, y,
               ident=None, art_share=0.45, cache=False, bounds=None, options=None,
               interactive_only=False):
    """Shared placement primitive for both scatter and pocket caches: resolve an identity,
    check its footprint against `reach`/`used`, and if legal append the obj and claim its
    cells. Returns whether it landed.

    interactive_only: passed to _legal — only the A-cell is checked/claimed so adjacent
    pickups' V-cells never block each other (use for dense fill passes)."""
    ident = ident or _pick(pool, purpose, st, rng, art_share=art_share)
    if ident is None:
        return False
    if purpose == "GUARD":
        # a guard's mask carries decorative overlay cells (H3's monster sprites always
        # bleed into surrounding scenery) alongside its one interactive cell -- at a
        # genuine chokepoint the surroundings are mostly blocked/unreachable BY
        # DEFINITION, so requiring the whole footprint free (like _legal does) means the
        # guard can almost never actually land on the neck. Only the interactive cell has
        # to be free & reachable; the rest may fall outside `reach`, overlap terrain, or
        # overlap another object's cells -- V cells are pure non-blocking sprite extent,
        # and the pocket the guard seals is BY DESIGN packed with caches up/left of the
        # mouth. Rejecting on `used` overlap silently dropped the guard from 31 of 39
        # earned pockets on a real 72x72 build (every nook north/west of its mouth),
        # leaving the treasure free -- the exact opposite of the cache grammar.
        interactive = OR.mask_interactive_cells(ident["mask"], x, y)
        if not interactive or not all(c in reach and c not in used for c in interactive):
            return False
        cells = [(tx, ty) for tx, ty, _b in OR.mask_cells(ident["mask"], x, y)]
        if bounds is not None:
            bw, bh = bounds
            if any(not (0 <= tx < bw and 0 <= ty < bh) for tx, ty in cells):
                return False
    else:
        cells = _legal(ident, x, y, reach, used, bounds=bounds,
                       interactive_only=interactive_only)
        if cells is None:
            return False
    used.update(cells)
    o = {"x": x, "y": y, "l": 0, "purpose": purpose,
         "type": ident.get("type"), "subtype": ident.get("subtype"),
         "animation": ident["animation"], "mask": ident["mask"],
         "template": {"animation": ident["animation"], "mask": ident["mask"]}}
    if purpose == "GUARD":       # absent => VCMI 'compliant' => every creature joins free
        o["options"] = {"character": "hostile"}
    if options is not None:
        o["options"] = options
    elif ident.get("type") == "pandoraBox":  # absent => legal but permanently empty reward
        o["options"] = _pandora_reward(rng)
    if cache:  # a guarded-pocket pickup, not open scatter — informational marker only,
        o["cache"] = True        # ignored by the vmap exporter, used by tests
    objs.append(o)
    return True


def place_scatter(ts, zones, zid, terrain, open_set, prot, seed=1, bounds=None,
                  entrances=None):
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

    dweb = _web_dist(open_set, prot)
    reach = set(dweb)                                # reachable open tiles only
    op = PG.openness(open_set)
    ed = ZF.edge_dist(ts)
    if entrances is not None:                        # isolation plan: gd measures from the
        bands = [(r, b) for r, b, _o in entrances]   # planned narrow crossings
    else:
        bands = ZF._zone_gate_bands(ts, zones, zid,
                                    open_frac=st.get("border_open_frac", 0.5))
    gd = PG.gate_dist(ts, set().union(*(b for _r, b in bands)) if bands else set())

    pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")

    objs, used = [], set()

    # Open-field scatter is resource piles only — artifacts are reserved for pockets
    # and loot zones where a guard or gate makes them genuinely earned.
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
    return objs, used, reach


def _shrine_spell_level(anim):
    """Spell level a shrine teaches from its animation name (avxlNsh0 → N), or 0 if not a shrine."""
    import re
    m = re.match(r"avxl(\d)sh", anim, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _solo_visit_pool(terrain, exclude_anims=frozenset(), min_shrine_level=None):
    """Objects with exactly one visit tile and no blocking body cells — the 'christmas-green'
    category (shrines, magic wells, fountains, etc.).  These fit inside a single open tile
    and are safe to cache inside pockets.

    exclude_anims: animation names to skip entirely.
    min_shrine_level: when set, shrines teaching spells below this level are excluded
        (non-shrine objects are unaffected)."""
    pool = []
    seen = set()
    for purpose in _SOLO_VIS_PURPOSES:
        for ident in ON.gameplay_pool(terrain, purpose):
            anim = ident.get("animation", "").lower()
            if anim in seen or anim in exclude_anims:
                continue
            if min_shrine_level is not None:
                lvl = _shrine_spell_level(anim)
                if lvl > 0 and lvl < min_shrine_level:
                    continue
            mask = ident.get("mask", [])
            n_visit = sum(1 for row in mask for ch in row if ch in "AX")
            n_body  = sum(1 for row in mask for ch in row if ch == "B")
            if n_visit == 1 and n_body == 0:
                seen.add(anim)
                pool.append(ident)
    return pool


def place_pocket_caches(zone_records, seed=1, bounds=None, border_guards=frozenset()):
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
    _sep_sq = (bounds[0] / 5.0) ** 2 if bounds else 0.0
    _spaced = {}  # type -> [(x, y)] of placed instances in _POCKET_SPACED_TYPES

    def _spaced_ok(typ, tx, ty):
        """True if (tx, ty) is far enough from all prior same-type instances."""
        return (not _sep_sq or typ not in _POCKET_SPACED_TYPES
                or not any((tx - px) ** 2 + (ty - py) ** 2 < _sep_sq
                           for px, py in _spaced.get(typ, ())))

    def _register(ident, tx, ty):
        if ident and ident.get("type") in _POCKET_SPACED_TYPES:
            _spaced.setdefault(ident["type"], []).append((tx, ty))
    for zr in zone_records:
        zid = zr["zid"]
        for t in zr["ts"]:
            zone_of[t] = zid
        terrain_of[zid] = zr["terrain"]
        used |= zr["used"]           # always claim used cells — no double-stacking
        if zr.get("loot_zone"):
            # Include in geometry (global_true) so external tiles adjacent to the loot
            # zone see passable neighbours and don't form false pockets against its wall.
            # Exclude from open/reach so no guard or cache can be placed inside.
            global_true |= zr.get("passable", zr["open_set"])
            continue
        global_open |= zr["open_set"]
        global_true |= zr.get("passable", zr["open_set"])
        global_reach |= (zr["reach"] - zr["used"])
    global_reach8 = _reach8(global_true, global_reach)
    global_place = global_reach8 & global_open

    raw = ZF.find_pockets(global_true)
    blobs = _dedupe_pockets(raw, global_true)
    guard_mask = PG.rnd_monster(1)["mask"]  # uniform across levels 1-7; used to pre-check fit
    objs = []
    placed_mouths = []

    def _pocket_fill(fill_spots, pool_res, pool_art, pool_chest, pool_vis, rng, st,
                     ref_mouth, terrain, reach=None):
        """50 % resource | 25 % chest (non-artifact) | 25 % hero structure for each fill tile.

        reach: placement eligibility set — defaults to global_place (strict: open_set &
        reachable8), but pocket callers pass global_reach8 so that approach cells of
        adjacent objects (excluded from open_set but physically passable) can still
        receive pickups inside the pocket."""
        _r = reach if reach is not None else global_place
        for t in fill_spots:
            roll = rng.random()
            if roll < 0.50:
                _place_one(objs, used, _r, rng, st, "RESOURCE_PILE", pool_res,
                           t[0], t[1], cache=True, bounds=bounds, interactive_only=True)
            elif roll < 0.75:
                avail_c = [i for i in pool_chest if _spaced_ok(i.get("type"), t[0], t[1])]
                ci = rng.choice(avail_c) if avail_c else (rng.choice(pool_chest) if pool_chest else None)
                if not (ci and _place_one(objs, used, _r, rng, st, "REWARD_PICKUP",
                                          pool_art, t[0], t[1], ident=ci, cache=True,
                                          bounds=bounds, interactive_only=True)):
                    _place_one(objs, used, _r, rng, st, "RESOURCE_PILE", pool_res,
                               t[0], t[1], cache=True, bounds=bounds, interactive_only=True)
                else:
                    _register(ci, t[0], t[1])
            else:
                avail_v = [i for i in pool_vis if _spaced_ok(i.get("type"), t[0], t[1])]
                vi = rng.choice(avail_v) if avail_v else (rng.choice(pool_vis) if pool_vis else None)
                if not (vi and _place_one(objs, used, _r, rng, st,
                                         vi.get("purpose", "BONUS_TEMP"), None,
                                         t[0], t[1], ident=vi, cache=True, bounds=bounds,
                                         interactive_only=True)):
                    _place_one(objs, used, _r, rng, st, "RESOURCE_PILE", pool_res,
                               t[0], t[1], cache=True, bounds=bounds, interactive_only=True)
                else:
                    _register(vi, t[0], t[1])

    for candidates in blobs:
        # Find the best guardable candidate (guard fits at the ZoC-centre position
        # whose ZoC seals the pocket and both mouth tiles are within it).
        guard_tile = pocket = zid = None
        ref_g = None  # ZoC-centre (reference for sorting / unguarded fallback)
        for cand_g, cand_pocket, cand_mouth_fs in candidates:
            cand_zid = zone_of.get(cand_g)
            if cand_zid is None:
                for mt in sorted(cand_mouth_fs):
                    cand_zid = zone_of.get(mt)
                    if cand_zid:
                        break
            if cand_zid is None:
                continue
            if ref_g is None:
                ref_g, pocket, zid = cand_g, cand_pocket, cand_zid
            if cand_g in used:
                continue
            if not all(c in global_place and c not in used
                       for c in OR.mask_interactive_cells(guard_mask, cand_g[0], cand_g[1])):
                continue
            if bounds is not None:
                bw, bh = bounds
                gcells = [(tx, ty) for tx, ty, _b in OR.mask_cells(guard_mask, cand_g[0], cand_g[1])]
                if any(not (0 <= tx < bw and 0 <= ty < bh) for tx, ty in gcells):
                    continue
            guard_tile = cand_g
            pocket, zid, ref_g = cand_pocket, cand_zid, cand_g
            break

        if pocket is None:
            continue

        # Size gate: 2–14 tiles only.
        if len(pocket) < 2 or len(pocket) > 14:
            continue

        # 3+ tile pockets require a guard — skip if none could be placed, unless the
        # pocket mouth is already sealed by a border guard (which isn't in global_place).
        if len(pocket) > 2 and guard_tile is None:
            if ref_g not in border_guards:
                continue
            # border guard already seals this pocket — fill without placing a new guard

        # Reference point for distance-sorting (guard tile or ZoC-centre).
        ref = guard_tile if guard_tile is not None else ref_g

        terrain = terrain_of[zid]
        st = PG.mine_gameplay()[terrain]
        pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
        pool_art = ON.gameplay_pool(terrain, "REWARD_PICKUP")
        rng = random.Random(seed ^ (ref_g[0] * 92821) ^ (ref_g[1] * 131071) ^ 0x9C4)
        # Pocket tiles are passable (in global_true) and reachable (in global_reach8);
        # they may be approach cells of adjacent gameplay objects (excluded from open_set /
        # global_place) but are still valid for pickups — use global_reach8 as the
        # eligibility check here, and pass it to _pocket_fill / _place_one below.
        cache_spots = [t for t in pocket if t not in used and t in global_reach8]
        if not cache_spots:
            continue

        # Sort nearest-to-ref (index 0) → deepest (index -1).
        cache_spots.sort(key=lambda t: max(abs(t[0] - ref[0]), abs(t[1] - ref[1])))

        # Chest pool: non-artifact REWARD_PICKUP (treasure chests, campfires).
        pool_chest = [i for i in pool_art if i.get("type") != "artifact"]
        pool_vis = _solo_visit_pool(terrain, exclude_anims=_FILL_EXCL_ANIMS)

        if len(pocket) == 2:
            # 2-tile pocket: no guard, no artifact — resources + structures only.
            _pocket_fill(cache_spots, pool_res, pool_art, pool_chest, pool_vis, rng, st,
                         ref, terrain, reach=global_reach8)
        else:
            # 3-14 tile pocket: guard at mouth tile + one artifact at deepest.
            n_fill = len(cache_spots) - 1  # one slot reserved for artifact
            est_val = int(n_fill * 2.25) + 5
            lvl = min(6, 1 + (est_val >= 4) + (est_val >= 7) + (est_val >= 10) + (est_val >= 13))
            anim = _ART_BY_LVL[lvl - 1]

            if guard_tile is not None:
                # Place a new guard at the pocket mouth.
                gident = PG.rnd_monster(lvl + (1 if rng.random() < 0.25 else 0))
                if not _place_one(objs, used, global_place, rng, st, "GUARD", None,
                                  guard_tile[0], guard_tile[1], ident=gident, bounds=bounds):
                    continue
                placed_mouths.append(guard_tile)

            # Re-derive available spots after guard V cells enter `used`.
            avail = [t for t in cache_spots if t not in used]
            avail.sort(key=lambda t: max(abs(t[0] - ref[0]), abs(t[1] - ref[1])))
            if not avail:
                continue
            art_spot   = avail[-1:]  # deepest tile gets the artifact
            fill_spots = avail[:-1]

            _pocket_fill(fill_spots, pool_res, pool_art, pool_chest, pool_vis, rng, st,
                         ref, terrain, reach=global_reach8)

            # Artifact at the deepest tile — tier matches guard level.
            if art_spot:
                t = art_spot[0]
                _place_one(objs, used, global_reach8, rng, st, "REWARD_PICKUP", pool_art,
                           t[0], t[1], ident=ON.identity_of(anim), cache=True, bounds=bounds,
                           interactive_only=True)

    return objs, len(blobs)


SEERHUT_ZONE_RATIO = 4    # ~1 seer-hut quest per 4 eligible zones -- zone_engine.py's own
                          # corpus-replay convention for the same object
MAX_SEER_HUTS = 6
SEERHUT_MIN_REACH = 8     # a zone needs at least this many free reachable tiles to be worth
                          # drawing into a quest (host EITHER the hut or its artifact)


def place_seer_hut_quests(zone_records, seed=1, bounds=None, used_artifacts=None):
    """One or more Seer Hut quests for the WHOLE level (VCMI RMG convention: a seer hut's
    mission gates on a single named artifact the hero must find and hand-carry to it). Each
    quest links two placements in DIFFERENT zones -- the quest's target artifact (an
    unguarded findable pickup, same "always free in open ground" doctrine as scatter loot --
    see the module docstring) and the seer hut itself (a rigid visitable building) -- with the
    hut's `options.quest.limiter.artifacts` naming the exact artifact identity placed for it.

    Runs once per level, after every zone's own gameplay/vegetation/scatter is finalized and
    the map-level G2/island repair has run (so both placements land on truly reachable
    ground), and BEFORE the pocket-cache pass claims the remaining nooks -- `zone_records`'
    `open_set`/`reach`/`used` are shared with that pass, so tiles this function spends are
    already excluded when pockets are judged.

    `used_artifacts`, when passed, is a set MUTATED in place and shared across every level's
    call for the same map (see `pp_map.build`) -- a named artifact is a map-unique relic in
    vanilla H3, so one quest's target must never double as another level's target too.

    `zone_records` is a list of {"zid", "terrain", "ts", "open_set", "passable", "reach",
    "used"} (see `pp_map._run_level`/`place_pocket_caches`). Returns (objs, n_quests)."""
    import random

    eligible = [zr for zr in zone_records if len(zr["reach"] - zr["used"]) >= SEERHUT_MIN_REACH]
    if len(eligible) < 2:
        return [], 0
    n = min(MAX_SEER_HUTS, max(1, len(eligible) // SEERHUT_ZONE_RATIO))

    rng_pair = random.Random(seed ^ 0xEE47)
    objs = []
    if used_artifacts is None:
        used_artifacts = set()
    placed = 0
    for i in range(n):
        idx_hut, idx_art = rng_pair.sample(range(len(eligible)), 2)
        hut_zr, art_zr = eligible[idx_hut], eligible[idx_art]
        rng = random.Random(seed ^ (i * 92821) ^ 0xEE47)

        pool_art = sorted((a for a in ON.gameplay_pool(art_zr["terrain"], "REWARD_PICKUP")
                          if a.get("type") == "artifact"
                          and a["subtype"] not in used_artifacts),
                          key=lambda a: a["animation"])
        if not pool_art:
            continue
        art_ident = rng.choice(pool_art)

        pool_hut = sorted((h for h in ON.gameplay_pool(hut_zr["terrain"], "QUEST_GATE")
                          if h.get("type") == "seerHut"),
                          key=lambda h: h["animation"])
        if not pool_hut:
            continue
        hut_ident = rng.choice(pool_hut)

        st_art = PG.mine_gameplay()[art_zr["terrain"]]
        art_cands = sorted(art_zr["reach"] - art_zr["used"])
        rng.shuffle(art_cands)
        art_xy = None
        for t in art_cands:
            if _place_one(objs, art_zr["used"], art_zr["reach"], rng, st_art, "REWARD_PICKUP",
                         None, t[0], t[1], ident=art_ident, bounds=bounds):
                art_xy = t
                break
        if art_xy is None:
            continue

        st_hut = PG.mine_gameplay()[hut_zr["terrain"]]
        hut_cands = sorted(hut_zr["reach"] - hut_zr["used"])
        rng.shuffle(hut_cands)
        options = _seerhut_quest(rng, art_ident["subtype"])
        hut_xy = None
        for t in hut_cands:
            if _place_one(objs, hut_zr["used"], hut_zr["reach"], rng, st_hut, "QUEST_GATE",
                         None, t[0], t[1], ident=hut_ident, options=options, bounds=bounds):
                hut_xy = t
                break
        if hut_xy is None:
            # no room for the hut => a dangling quest artifact nobody asked for; drop it
            # rather than leave an orphaned reference
            objs.pop()
            for cx, cy, _b in OR.mask_cells(art_ident["mask"], art_xy[0], art_xy[1]):
                art_zr["used"].discard((cx, cy))
            continue

        used_artifacts.add(art_ident["subtype"])
        placed += 1
    return objs, placed


REWARD_ZONE_ART_W = {"avarnd1": 20, "avarnd2": 40, "avarnd3": 30, "avarand": 10}
#                    ^ portal reward zones skew toward minor/major artifacts — the fight to
#                      get in (guarded portal) must pay better than open scatter (RND_ART
#                      is treasure-heavy: 50/30/15/5).


def place_reward_zone(zr, entry, seed=1, bounds=None):
    """SPECIAL REWARD upgrade for a zone rescued by a guarded two-way monolith (pp_map's
    unreachable-zone pass): the pocket-cache grammar scaled to the whole zone — dense
    resource piles + artifact pickups (major-skewed, all `cache`-tagged) reachable from the
    portal's `entry` tile, plus one interior guard whose strength tracks the accumulated
    value (the cache ladder + 1). Works both for fully-populated zones (extra richness) and
    for bare sub-MIN_AREA slivers the level pass skipped (their only content). Claims its
    cells in `zr["used"]` so the later pocket-cache pass never double-stacks. Returns objs."""
    import random

    terrain = zr["terrain"]
    st = PG.mine_gameplay()[terrain]
    rng = random.Random(seed ^ (entry[0] * 92821) ^ (entry[1] * 131071) ^ 0x907A1)
    ts = zr["ts"]
    used = zr["used"]
    area = len(ts)

    # reach: what the portal's entry tile actually opens up (4-connected within passable)
    passable = zr["passable"]
    reach, q = {entry} if entry in passable else set(), [entry]
    while q:
        x, y = q.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n in passable and n not in reach:
                reach.add(n)
                q.append(n)
    if not reach:
        return []

    n_res = max(4, area // 10)
    n_art = max(2, area // 25)
    pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
    pool_art = ON.gameplay_pool(terrain, "REWARD_PICKUP")
    objs = []
    val = 0

    spots = sorted(reach - used)
    rng.shuffle(spots)
    for t in spots:
        if n_res <= 0:
            break
        if _place_one(objs, used, reach, rng, st, "RESOURCE_PILE", pool_res,
                      t[0], t[1], cache=True, bounds=bounds):
            n_res -= 1
            val += 2
    arts = [a for a in PG.RND_ART]
    for t in spots:
        if n_art <= 0:
            break
        if t in used:
            continue
        anim, _w, av = rng.choices(arts, weights=[REWARD_ZONE_ART_W[a] for a, _w2, _v in arts],
                                   k=1)[0]
        if _place_one(objs, used, reach, rng, st, "REWARD_PICKUP", pool_art,
                      t[0], t[1], ident=ON.identity_of(anim), cache=True, bounds=bounds):
            n_art -= 1
            val += av

    if objs:
        # one interior guard near the zone's own centre: the portal guard gates entry, this
        # one gates the hoard itself — cache ladder (pp_pickup pocket convention) + 1
        cx = sum(x for x, _ in ts) / area
        cy = sum(y for _, y in ts) / area
        lvl = 1 + (val >= 4) + (val >= 7) + (val >= 10) + (val >= 13) + 1
        gident = PG.rnd_monster(lvl)
        for t in sorted(reach - used,
                        key=lambda t: ((t[0] - cx) ** 2 + (t[1] - cy) ** 2, t)):
            if _place_one(objs, used, reach, rng, st, "GUARD", None,
                          t[0], t[1], ident=gident, bounds=bounds):
                break
    return objs


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


def place_loot_zones(zone_records, entrance_plan, objs_existing, seed=1, bounds=None,
                     water_tiles=None):
    """Loot-zone access mechanic for small single-entrance zones.

    A 'loot zone' has ≤ LOOT_ZONE_MAX_TILES tiles, exactly one 8-connected cluster of
    'blue' passage tiles at its boundary (physical single-entrance check), and no town.
    Dense fill (hero-strengthening structures, major/relic artifacts, resource piles) is
    placed in every qualifying zone.  Access mechanic is chosen 50/50 per zone:

      gate   (50 %): BORDER_GATE placed at the entrance + matching-colour KEYMASTER in a
               non-loot zone far from castles and far from other exterior partners.  The
               hero must first find the tent then return to the gate.  All other passage
               tiles are sealed with vegetation.

      mono   (50 %): all passage tiles are FULLY sealed — the zone becomes a walled
               pocket.  A TWO-WAY MONOLITH is placed inside and a matching one outside
               (far from castles and other exterior partners), so the only way in is the
               external monolith.

    The outer object (keymaster / exterior monolith) is pre-checked before the inner
    object is committed, so no permanently impassable gate or unreachable interior is
    ever left on the map.  Returns (objs, n_placements, sealed_zid_set).
    """
    import random

    town_tiles = {(o["x"], o["y"]) for o in objs_existing if o.get("purpose") == "TOWN"}

    # Pre-compute full tile set of all zones for boundary detection.
    # water_tiles comes from the caller (grid-level water, never in zone_records).
    water_ts = set(water_tiles) if water_tiles else set()
    _all_ts = set()
    for _zr in zone_records:
        _all_ts |= _zr["ts"]

    def _passage_components(zr):
        """Count 8-connected clusters of zone tiles that border any tile of another
        zone (terrain-tile adjacency, independent of placed vegetation).  This is the
        topological single-entrance check: 1 cluster = 1 direction of connectivity.
        Returns (n_clusters, frozenset_of_boundary_tiles)."""
        ts = zr["ts"]
        ext_ts = _all_ts - ts
        boundary = {t for t in ts
                    if any((t[0] + dx, t[1] + dy) in ext_ts
                           for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                                          (1, 1), (1, -1), (-1, 1), (-1, -1)])}
        seen, n = set(), 0
        for s in sorted(boundary):
            if s in seen:
                continue
            n += 1
            q = collections.deque([s])
            seen.add(s)
            while q:
                cx, cy = q.popleft()
                for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                    nb = (cx + dx, cy + dy)
                    if nb in boundary and nb not in seen:
                        seen.add(nb)
                        q.append(nb)
        return n, frozenset(boundary)

    _DIRS8 = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]

    loot_zrs = []   # list of (zone_record, passage_tile_frozenset)
    for zr in zone_records:
        if len(zr["ts"]) > LOOT_ZONE_MAX_TILES:
            continue
        if any(t in town_tiles for t in zr["ts"]):
            continue
        n_clusters, passage_tiles = _passage_components(zr)
        if n_clusters != 1:
            continue
        # No water adjacency: no boundary tile may be 8-adjacent to a water tile.
        if water_ts and any(
            (t[0]+dx, t[1]+dy) in water_ts
            for t in passage_tiles
            for dx, dy in _DIRS8
        ):
            continue
        loot_zrs.append((zr, passage_tiles))

    if not loot_zrs:
        return [], 0, set()

    loot_zids = {zr["zid"] for zr, _ in loot_zrs}
    ext_no_castle = [zr for zr in zone_records
                     if zr["zid"] not in loot_zids
                     and not any(t in town_tiles for t in zr["ts"])]
    ext_any = [zr for zr in zone_records if zr["zid"] not in loot_zids]

    placed_ext_tiles = []   # positions of exterior partners already placed

    def _far_score(zr):
        free = zr["reach"] - zr["used"]
        if not free:
            return (-1, 0, 0)
        cx = sum(x for x, _ in zr["ts"]) / len(zr["ts"])
        cy = sum(y for _, y in zr["ts"]) / len(zr["ts"])
        d_castle = (min((cx - tx) ** 2 + (cy - ty) ** 2
                        for tx, ty in town_tiles) ** 0.5
                    if town_tiles else 1e9)
        d_partner = (min((cx - px) ** 2 + (cy - py) ** 2
                         for px, py in placed_ext_tiles) ** 0.5
                     if placed_ext_tiles else 1e9)
        return (d_castle + d_partner, len(free))

    def _find_ext_spot(ext_ident, ext_pool):
        """Return (zone_record, tile) farthest from castles and from existing
        exterior partners (keymasters / exterior monoliths already placed)."""
        for cand in sorted(ext_pool, key=_far_score, reverse=True):
            free = sorted(cand["reach"] - cand["used"])
            if not free:
                continue
            def _tscore(t):
                d_c = (min((t[0] - tx) ** 2 + (t[1] - ty) ** 2
                           for tx, ty in town_tiles) ** 0.5
                       if town_tiles else 1e9)
                d_p = (min((t[0] - px) ** 2 + (t[1] - py) ** 2
                           for px, py in placed_ext_tiles) ** 0.5
                       if placed_ext_tiles else 1e9)
                return d_c + d_p
            free.sort(key=_tscore, reverse=True)
            ts_set = cand["ts"]
            op_set = cand["open_set"]
            for t in free:
                tx, ty = t
                # [N N]   (tx-1,ty-1) (tx,  ty-1)
                # [N X]   (tx-1,ty)   (tx,  ty)  ← anchor
                # All three N-cells must be clear: either outside this zone or
                # inside it and in open_set (not occupied by vegetation or objects).
                if not all(
                    (cx, cy) not in ts_set
                    or ((cx, cy) in op_set and (cx, cy) not in cand["used"])
                    for cx, cy in ((tx - 1, ty - 1), (tx, ty - 1), (tx - 1, ty))
                ):
                    continue
                if _legal(ext_ident, tx, ty, cand["reach"],
                          cand["used"], bounds=bounds) is not None:
                    return cand, t
        return None, None

    def _seal_all_passages(ts, open_set, used, terrain, rng, skip_cells=frozenset()):
        """Fill EVERY boundary tile of the loot zone (tile in ts that is 8-adjacent
        to a tile outside ts) with a single-cell blocking vegetation object, perfectly
        sealing the perimeter including any passable V-overlay cells of the gate.

        skip_cells: the gate's or monolith's interactive tile(s) — the one spot a hero
        must stand on to activate the access object; these are NOT sealed."""
        ext_ts = _all_ts - ts
        veg_pool = ON.decor_pool(terrain, blocking=True, max_cells=1,
                                 exclude_types=_LOOT_EXCL_DECOR)
        if not veg_pool:
            return
        for t in sorted(ts):
            if t in skip_cells:
                continue  # access object's interactive tile — must stay passable
            tx, ty = t
            if not any((tx + dx, ty + dy) in ext_ts
                       for dx, dy in [(1,0),(-1,0),(0,1),(0,-1),
                                      (1,1),(1,-1),(-1,1),(-1,-1)]):
                continue  # interior tile — left for loot
            objs[:] = [o for o in objs if not (o.get("purpose") == "GUARD"
                                                and o["x"] == tx and o["y"] == ty)]
            iv = rng.choice(veg_pool)
            used.add(t)
            objs.append({"x": tx, "y": ty, "l": 0,
                         "type": iv.get("type"), "subtype": iv.get("subtype"),
                         "animation": iv["animation"], "mask": iv["mask"],
                         "template": {"animation": iv["animation"],
                                      "mask": iv["mask"]}})

    def _fill_loot(terrain, st, reach, used, rng):
        """Three-pass loot fill: hero-strengthening structures → mixed rewards → background decor.

        Pass 0 (bg): non-blocking terrain decor on interior tiles (under gameplay objects).
        Pass 1 (30 %): solo-visitable hero-strengthening structures.
        Pass 2: 30 % major/relic artifact, 30 % chest/campfire, 40 % rare resource pile
                (mercury, sulfur, crystal, gems, gold — no wood/ore)."""
        # Pass 0: background — non-blocking terrain decor on interior (non-boundary) tiles.
        ext_ts_inner = _all_ts - reach
        interior = {t for t in reach
                    if not any((t[0]+dx, t[1]+dy) in ext_ts_inner for dx, dy in _DIRS8)}
        pool_bg = ON.decor_pool(terrain, blocking=False, max_cells=1,
                                exclude_types=_LOOT_EXCL_DECOR)
        if pool_bg:
            for t in sorted(interior):
                if rng.random() < 0.5:
                    iv = rng.choice(pool_bg)
                    objs.append({"x": t[0], "y": t[1], "l": 0,
                                 "type": iv.get("type"), "subtype": iv.get("subtype"),
                                 "animation": iv["animation"], "mask": iv["mask"],
                                 "template": {"animation": iv["animation"],
                                              "mask": iv["mask"]}})

        pool_vis = _solo_visit_pool(terrain,
                                    exclude_anims=_FILL_EXCL_ANIMS | _LOOT_VIS_EXCL_ANIMS,
                                    min_shrine_level=_LOOT_SHRINE_MIN_LEVEL)
        pool_art = [i for i in ON.gameplay_pool(terrain, "REWARD_PICKUP")
                    if i.get("type") not in _LOOT_ART_EXCL_TYPES]
        pool_res = ON.gameplay_pool(terrain, "RESOURCE_PILE")
        # chest-type pickups: treasure chests, campfires — 'ava*' is the artifact namespace.
        # chest-type only: treasure chests, campfires — explicitly exclude artifacts so
        # named artifacts with non-'ava' animations (e.g. 'avssword0') can't slip in here.
        pool_chest = [i for i in pool_art if i.get("type") != "artifact"]
        # High-tier artifacts only (major + relic).
        arts_high = [(a, _LOOT_ART_W[a]) for a in ("avarnd3", "avarand") if a in _LOOT_ART_W]
        # Rare resources: mercury(1), sulfur(3), crystal(4), gems(5), gold(6) — no wood(0)/ore(2).
        pool_rare = [i for i in pool_res if i.get("subtype") not in {0, 2}]

        free = sorted(reach - used)
        rng.shuffle(free)

        # Pass 1: hero-strengthening structures — 30 % of available tiles
        n_vis = max(1, len(free) * 3 // 10)
        vis_placed = 0
        for t in free:
            if not pool_vis or vis_placed >= n_vis:
                break
            iv = rng.choice(pool_vis)
            if _place_one(objs, used, reach, rng, st,
                          iv.get("purpose", "BONUS_TEMP"), None,
                          t[0], t[1], ident=iv, cache=True, bounds=bounds,
                          interactive_only=True):
                vis_placed += 1

        # Pass 2: 30 % major/relic artifact | 30 % chest/campfire | 40 % rare resource
        for t in sorted(reach - used):
            roll = rng.random()
            if roll < 0.3:
                if arts_high:
                    ai = ON.identity_of(rng.choices(
                        [a for a, _ in arts_high],
                        weights=[w for _, w in arts_high], k=1)[0])
                    if ai:
                        _place_one(objs, used, reach, rng, st, "REWARD_PICKUP", pool_art,
                                   t[0], t[1], ident=ai, cache=True, bounds=bounds,
                                   interactive_only=True)
            elif roll < 0.6:
                ai = rng.choice(pool_chest) if pool_chest else None
                if ai:
                    _place_one(objs, used, reach, rng, st, "REWARD_PICKUP", pool_art,
                               t[0], t[1], ident=ai, cache=True, bounds=bounds,
                               interactive_only=True)
            else:
                ri = rng.choice(pool_rare) if pool_rare else (rng.choice(pool_res) if pool_res else None)
                if ri:
                    _place_one(objs, used, reach, rng, st, "RESOURCE_PILE", pool_res,
                               t[0], t[1], ident=ri, cache=True, bounds=bounds,
                               interactive_only=True)

    objs, n_placed = [], 0
    processed_loot_zids = set()   # zones whose entrance was actually sealed this run
    gate_count, mono_count = 0, 0

    for loot_zr, passage_tiles in sorted(loot_zrs, key=lambda x: x[0]["zid"]):
        zid      = loot_zr["zid"]
        terrain  = loot_zr["terrain"]
        st       = PG.mine_gameplay()[terrain]
        ts       = loot_zr["ts"]
        reach    = loot_zr["reach"]
        used     = loot_zr["used"]
        open_set = loot_zr.get("open_set")
        rng      = random.Random(seed ^ (zid * 92821) ^ 0xA117)
        ext_pool = ext_no_castle or ext_any
        passage_cx = sum(t[0] for t in passage_tiles) / len(passage_tiles)
        passage_cy = sum(t[1] for t in passage_tiles) / len(passage_tiles)

        # Clear scatter vegetation so the whole interior is available for loot.
        objs_existing[:] = [o for o in objs_existing if (o["x"], o["y"]) not in ts]
        objs[:] = [o for o in objs if (o["x"], o["y"]) not in ts]
        used.clear()
        # After clearing, all zone tiles are passable (loot zones have no gameplay
        # blockers — no town, no mine).  The stored open_set/reach were computed with
        # dense vegetation in place (~70 % blocking) so they cover only ~30 % of ts.
        # Reset both to the full tile set so seal and fill can reach every tile.
        open_set = ts
        reach    = ts

        # Determine which side of the loot zone's bounding box the passage is on.
        ts_xs = [t[0] for t in ts]; ts_ys = [t[1] for t in ts]
        bbox_x0, bbox_x1 = min(ts_xs), max(ts_xs)
        bbox_y0, bbox_y1 = min(ts_ys), max(ts_ys)
        d_top    = passage_cy - bbox_y0
        d_bottom = bbox_y1 - passage_cy
        d_left   = passage_cx - bbox_x0
        d_right  = bbox_x1 - passage_cx
        passage_side = min(
            [("top", d_top), ("bottom", d_bottom), ("left", d_left), ("right", d_right)],
            key=lambda s: s[1]
        )[0]

        use_gate = rng.random() < 0.5
        if use_gate:
            # ── Border Gate + Keymaster ──────────────────────────────────────
            # Gate mask ['VVVV','VBXB']: 4-wide × 2-tall, anchor = bottom-right.
            # V-row at y-1 (passable/exterior), blocking-row at y (loot zone side).
            # Sort candidates so the blocking-row aligns with the passage side:
            #   top/bottom → anchor y at boundary row, x centred on passage
            #   left/right → anchor y at passage cy, x so the gate span covers passage x
            gate_anim, key_anim = _LOOT_COLORS[gate_count % len(_LOOT_COLORS)]
            gate_ident = ON.identity_of(gate_anim)
            key_ident  = ON.identity_of(key_anim)
            if gate_ident is None or key_ident is None:
                continue

            km_zr, km_t = _find_ext_spot(key_ident, ext_pool)
            if km_t is None:
                continue

            def _gate_score(t):
                gx, gy = t
                if passage_side in ("top", "bottom"):
                    bnd_y = bbox_y0 if passage_side == "top" else bbox_y1
                    return (abs(gy - bnd_y), abs((gx - 1.5) - passage_cx))
                else:  # left / right
                    # anchor x so gate's span (gx-3 .. gx) covers the passage x
                    ideal_x = bbox_x0 + 3 if passage_side == "left" else bbox_x1
                    return (abs(gy - passage_cy), abs(gx - ideal_x))

            gate_tile = None
            for t in sorted(ts, key=_gate_score):
                gx, gy = t
                gate_cells = [(cx, cy)
                              for cx, cy, _ in OR.mask_cells(gate_ident["mask"], gx, gy)]
                if bounds:
                    bw, bh = bounds
                    if any(not (0 <= cx < bw and 0 <= cy < bh) for cx, cy in gate_cells):
                        continue
                interactive = OR.mask_interactive_cells(gate_ident["mask"], gx, gy)
                if not all(c in open_set for c in interactive):
                    continue
                # Clear any object (vegetation, guard) whose footprint overlaps the gate's
                # full cell set — including V-row cells that may be in the exterior zone.
                fp = set(gate_cells)
                cleared = set()
                for src in (objs_existing, objs):
                    victims = [o for o in src
                               if any((cx, cy) in fp
                                      for cx, cy, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]
                    for o in victims:
                        src.remove(o)
                        for cx, cy, _ in OR.mask_cells(o["mask"], o["x"], o["y"]):
                            cleared.add((cx, cy))
                for zr in zone_records:
                    zr["used"] -= cleared
                used.update(gate_cells)
                objs.append({"x": gx, "y": gy, "l": 0, "purpose": "QUEST_GATE",
                             "type": gate_ident.get("type"),
                             "subtype": gate_ident.get("subtype"),
                             "animation": gate_ident["animation"],
                             "mask": gate_ident["mask"],
                             "template": {"animation": gate_ident["animation"],
                                          "mask": gate_ident["mask"]}})
                gate_tile = t
                break
            if gate_tile is None:
                continue

            # Verify seal leaves the gate's interactive tile with a passable interior neighbor.
            # Exterior access is guaranteed (interactive tiles are boundary tiles by construction).
            sealed_boundary = passage_tiles - set(interactive)
            passable_after_seal = ts - sealed_boundary
            if not any((sk[0]+dx, sk[1]+dy) in passable_after_seal
                       for sk in interactive for dx, dy in _DIRS8):
                continue

            _seal_all_passages(ts, open_set, used, terrain, rng,
                               skip_cells=set(interactive))
            processed_loot_zids.add(zid)
            _fill_loot(terrain, st, open_set, used, rng)

            km_rng = random.Random(seed ^ (zid * 131071) ^ 0xCEBF)
            km_st  = PG.mine_gameplay()[km_zr["terrain"]]
            placed = _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                "QUEST_GATE", None, km_t[0], km_t[1],
                                ident=key_ident, bounds=bounds)
            if not placed:
                for t in sorted(km_zr["reach"] - km_zr["used"]):
                    if _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                 "QUEST_GATE", None, t[0], t[1],
                                 ident=key_ident, bounds=bounds):
                        placed = True
                        break
            if placed:
                n_placed += 1
                gate_count += 1
                placed_ext_tiles.append(km_t)
                gident_km = PG.rnd_monster(7)
                for t in sorted(km_zr["reach"] - km_zr["used"],
                                key=lambda t: max(abs(t[0] - km_t[0]),
                                                  abs(t[1] - km_t[1]))):
                    if max(abs(t[0] - km_t[0]), abs(t[1] - km_t[1])) > 1:
                        break
                    if _place_one(objs, km_zr["used"], km_zr["reach"], km_rng, km_st,
                                  "GUARD", None, t[0], t[1], ident=gident_km,
                                  bounds=bounds):
                        break

        else:
            # ── Fully sealed + Two-Way Monolith pair ─────────────────────────
            mono_anim  = _LOOT_MONOLITHS[mono_count % len(_LOOT_MONOLITHS)]
            mono_ident = ON.identity_of(mono_anim)
            if mono_ident is None:
                continue

            ext_zr, ext_t = _find_ext_spot(mono_ident, ext_pool)
            if ext_t is None:
                continue

            # Place monolith at zone centroid (deepest interior tile).
            ts_cx = sum(t[0] for t in ts) / len(ts)
            ts_cy = sum(t[1] for t in ts) / len(ts)
            int_t = None
            for t in sorted(reach - used,
                            key=lambda t: (t[0] - ts_cx) ** 2 + (t[1] - ts_cy) ** 2):
                if _legal(mono_ident, t[0], t[1], reach, used, bounds=bounds) is not None:
                    int_t = t
                    break
            if int_t is None:
                continue

            _place_one(objs, used, reach, rng, st, "TRANSPORT", None,
                      int_t[0], int_t[1], ident=mono_ident, bounds=bounds)
            mono_interactive = set(OR.mask_interactive_cells(mono_ident["mask"],
                                                             int_t[0], int_t[1]))
            _seal_all_passages(ts, open_set, used, terrain, rng,
                               skip_cells=mono_interactive)
            processed_loot_zids.add(zid)
            _fill_loot(terrain, st, open_set, used, rng)

            ext_rng = random.Random(seed ^ (zid * 131071) ^ 0xCEBF)
            ext_st  = PG.mine_gameplay()[ext_zr["terrain"]]
            placed  = _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                 "TRANSPORT", None, ext_t[0], ext_t[1],
                                 ident=mono_ident, bounds=bounds)
            if not placed:
                for t in sorted(ext_zr["reach"] - ext_zr["used"]):
                    if _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                 "TRANSPORT", None, t[0], t[1],
                                 ident=mono_ident, bounds=bounds):
                        placed = True
                        break
            if placed:
                n_placed += 1
                mono_count += 1
                placed_ext_tiles.append(ext_t)
                gident_ext = PG.rnd_monster(7)
                for t in sorted(ext_zr["reach"] - ext_zr["used"],
                                key=lambda t: max(abs(t[0] - ext_t[0]),
                                                  abs(t[1] - ext_t[1]))):
                    if max(abs(t[0] - ext_t[0]), abs(t[1] - ext_t[1])) > 1:
                        break
                    if _place_one(objs, ext_zr["used"], ext_zr["reach"], ext_rng, ext_st,
                                  "GUARD", None, t[0], t[1], ident=gident_ext,
                                  bounds=bounds):
                        break

    return objs, n_placed, processed_loot_zids
