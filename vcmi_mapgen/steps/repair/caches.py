"""Guarded pocket caches + Seer Hut quests — Repair-only (always ran from
`_repair_and_finish_level`, never from `_run_level`'s per-zone passes).
"""
import collections

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import zone_field as ZF
from vcmi_mapgen.steps.gate.gates import rnd_monster
from vcmi_mapgen.steps.gameplay.mines import mine_gameplay
from vcmi_mapgen.steps.pickup.loot_zones import _FILL_EXCL_ANIMS, _solo_visit_pool
from vcmi_mapgen.steps.pickup.scatter import (
    PANDORA_CREATURES, _RW_LIMITER, _RW_REWARD, _RW_TEXT, _place_one, place_scatter,
)

# Artifact tier (animation name from RND_ART) indexed by monster level 1-6:
# treasure(1-2) → minor(3) → major(4-5) → any/relic(6).
# The monster level is derived from resources + visitable structures placed in the pocket;
# the artifact at the deepest tile then matches that level so the guard's strength is
# always proportional to the prize behind it.
_ART_BY_LVL = ["avarnd1", "avarnd1", "avarnd2", "avarnd3", "avarnd3", "avarand"]

# Types that must maintain a minimum map-fraction separation between any two instances in pockets.
_POCKET_SPACED_TYPES = frozenset({"magicWell", "warriorTomb"})


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


def place_pocket_caches(zone_records, seed=1, bounds=None, border_guards=frozenset(),
                        precomputed_pockets=None):
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

    raw = precomputed_pockets if precomputed_pockets is not None else ZF.find_pockets(global_true)
    blobs = _dedupe_pockets(raw, global_true)
    guard_mask = rnd_monster(1)["mask"]  # uniform across levels 1-7; used to pre-check fit
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
        st = mine_gameplay()[terrain]
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
            # Use global_place (not global_reach8) so caches never land on already-placed
            # gameplay footprints or their approach tiles.
            _pocket_fill(cache_spots, pool_res, pool_art, pool_chest, pool_vis, rng, st,
                         ref, terrain, reach=global_place)
        else:
            # 3-14 tile pocket: guard at mouth tile + one artifact at deepest.
            n_fill = len(cache_spots) - 1  # one slot reserved for artifact
            est_val = int(n_fill * 2.25) + 5
            lvl = min(6, 1 + (est_val >= 4) + (est_val >= 7) + (est_val >= 10) + (est_val >= 13))
            anim = _ART_BY_LVL[lvl - 1]

            if guard_tile is not None:
                # Place a new guard at the pocket mouth.
                gident = rnd_monster(lvl + (1 if rng.random() < 0.25 else 0))
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

            # Use global_place so fill and artifact never stack on top of gameplay objects.
            _pocket_fill(fill_spots, pool_res, pool_art, pool_chest, pool_vis, rng, st,
                         ref, terrain, reach=global_place)

            # Artifact at the deepest tile — tier matches guard level.
            if art_spot:
                t = art_spot[0]
                _place_one(objs, used, global_place, rng, st, "REWARD_PICKUP", pool_art,
                           t[0], t[1], ident=ON.identity_of(anim), cache=True, bounds=bounds,
                           interactive_only=True)

    return objs, len(blobs)


SEERHUT_ZONE_RATIO = 4    # ~1 seer-hut quest per 4 eligible zones -- zone_engine.py's own
                          # corpus-replay convention for the same object
MAX_SEER_HUTS = 6
SEERHUT_MIN_REACH = 8     # a zone needs at least this many free reachable tiles to be worth
                          # drawing into a quest (host EITHER the hut or its artifact)


def place_seer_hut_quests(zone_records, seed=1, bounds=None, used_artifacts=None,
                          pocket_tiles=None):
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
    # Pre-compute which zones have pocket tiles so the per-attempt loop can skip quickly.
    if pocket_tiles is not None:
        _ptiles_global = pocket_tiles
    else:
        passable_all = set().union(*(zr.get("passable", zr["reach"]) for zr in zone_records))
        raw_p = ZF.find_pockets(passable_all)
        _ptiles_global = set()
        for _g, (pt, _mf) in raw_p.items():
            if len(pt) >= 3:
                _ptiles_global |= set(pt)

    for i in range(n):
        idx_hut, idx_art = rng_pair.sample(range(len(eligible)), 2)
        hut_zr = eligible[idx_hut]
        rng = random.Random(seed ^ (i * 92821) ^ 0xEE47)

        pool_hut = sorted((h for h in ON.gameplay_pool(hut_zr["terrain"], "QUEST_GATE")
                          if h.get("type") == "seerHut"),
                          key=lambda h: h["animation"])
        if not pool_hut:
            continue
        hut_ident = rng.choice(pool_hut)

        # Find an art zone with a ≥3-tile pocket; start with the random pick then
        # try other eligible zones to avoid getting 0 quests when the chosen zone
        # has no pocket tiles available.
        art_zr_order = [eligible[idx_art]] + [
            zr for j, zr in enumerate(eligible) if j != idx_art and j != idx_hut]
        art_zr = art_ident = None
        for cand_art_zr in art_zr_order:
            art_eligible = _ptiles_global & (cand_art_zr["reach"] - cand_art_zr["used"])
            if not art_eligible:
                continue
            cand_pool_art = sorted((a for a in ON.gameplay_pool(cand_art_zr["terrain"],
                                                                "REWARD_PICKUP")
                                    if a.get("type") == "artifact"
                                    and a["subtype"] not in used_artifacts),
                                   key=lambda a: a["animation"])
            if not cand_pool_art:
                continue
            art_zr, art_ident = cand_art_zr, rng.choice(cand_pool_art)
            break
        if art_zr is None:
            continue   # no eligible art zone with a ≥3-tile pocket

        st_art = mine_gameplay()[art_zr["terrain"]]
        art_eligible = _ptiles_global & (art_zr["reach"] - art_zr["used"])
        art_cands = sorted(art_eligible)
        rng.shuffle(art_cands)
        art_xy = None
        for t in art_cands:
            if _place_one(objs, art_zr["used"], art_zr["reach"], rng, st_art, "REWARD_PICKUP",
                         None, t[0], t[1], ident=art_ident, bounds=bounds):
                art_xy = t
                break
        if art_xy is None:
            continue

        st_hut = mine_gameplay()[hut_zr["terrain"]]
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
