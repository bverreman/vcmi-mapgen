"""Unguarded scatter loot over the finished open field (L4a) — Pickup-only.

Runs AFTER vegetation — the open field is known, so the classic H3 treasure grammar
applies: resource piles/artifacts lying in the open along routes are ALWAYS free
(user-mandated: a guard only belongs at a genuine chokepoint, never planted beside loot
that sits in open terrain and can be walked around).

Also owns `_place_one` (the placement primitive shared with
`steps.repair.caches.place_pocket_caches`/`place_seer_hut_quests` and
`steps.pickup.loot_zones.place_loot_zones` — Pickup is the first step in pipeline order to
need it) and its pandoraBox-reward helpers.
"""
import collections

from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import zone_field as ZF
from vcmi_mapgen.steps.gameplay import mines as PG
from vcmi_mapgen.steps.gameplay.water import _legal, _pick

CAPS = {"RESOURCE_PILE": 16, "REWARD_PICKUP": 8}   # base floors; caps scale (scatter only --
                                # pocket guards/caches are deterministic, see place_pickups)
SCATTER_ART_SHARE = 0.15        # unguarded scatter: mostly LOOT (chests/campfires); the
                                # tiered random artifacts live behind cache guards instead

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
        if not cands:                                 # zone has no reachable open tile
            return
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
