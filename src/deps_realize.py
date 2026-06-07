"""End-to-end realizer: dependency tree -> spatial embedding -> editor-loadable .vmap.

    deps_gen.generate_tree   (skeleton: nodes/edges; tunable via params)
 -> deps_embed.embed         (zones + barriers + chokepoints + portals)
 -> realize (this file)      (biome terrain, MOUNTAIN seams, guards at gates,
                              gated rewards in pockets, portals, road overlays,
                              + a DENSITY layer that scatters ambient objects and
                              vegetation to target per-purpose densities)
 -> faithful.to_vmap         (editor-valid writer)

`params` exposes the knobs (region count, depth, portal fraction, biome mix,
per-purpose density) so a caller can dial the generator toward a target map and
prove the real map lies inside the generator's reachable space.
"""

import sys, os, json, random, math, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_gen, deps_embed, faithful

OBJ = json.load(open(f"{ROOT}/out/objlib.json"))
FAC = json.load(open(f"{ROOT}/out/factors.json"))
VIEW = {int(k): v for k, v in FAC["terrain_view"].items()}
ROAD_DIR = FAC.get("road_dir", 13)
TYPE2PURPOSE = {it["type"]: p for p, terr in OBJ.items() for items in terr.values() for it in items}

# default per-purpose density (objects per 1000 tiles) from the corpus budget
_TOT = sum(FAC["proportions"].values())
DEFAULT_DENSITY = {p: 1000.0 * sum(v["counts"]) / _TOT for p, v in FAC["budget"].items()}

# corpus-average prior (deps_prior.py): defaults so a plain generate is realistic
try:
    _PR = json.load(open(f"{ROOT}/out/spatial_prior.json"))
    PRIOR_DENSITY = _PR.get("density", {})
    PRIOR_BIOME = _PR.get("biome_weights", {})
    PRIOR_WATER = _PR.get("water_frac", 0.0)
    PRIOR_SIG = {(a, b): v for a, b, v in _PR.get("sig", [])}
except Exception:
    PRIOR_DENSITY, PRIOR_BIOME, PRIOR_WATER, PRIOR_SIG = {}, {}, 0.0, {}

LAND_BIOMES = [2, 0, 1, 3, 4, 5, 6, 7]
ROOT_BIOME = 2
WATER = 8
SCATTER = [
    "MINE",
    "DWELLING",
    "BANK",
    "REWARD_PICKUP",
    "RESOURCE_PILE",
    "STAT_PERMANENT",
    "BONUS_TEMP",
    "SPELL_SKILL",
    "INFO",
    "MANA",
    "SPECIAL",
]

# object-adjacency graph (deps_adj.py): per anchor purpose -> its neighbour
# distribution {avg_degree, neighbours:[(purpose, share, median_dist)]}. Objects
# are GROWN from this graph (real motifs: mine+guard+resources) rather than scattered.
try:
    _ADJ = json.load(open(f"{ROOT}/out/adjacency.json")).get("profiles", {})
except Exception:
    _ADJ = {}
# Pre-process each neighbour's offset list: sort by distance then deduplicate.
# The corpus samples many instances of the same offset (e.g. 40+ copies of
# [-1,1] for mine→guard), so grow_touch[:16] would hammer the same tile
# repeatedly. Deduplication gives 16 *distinct* positions spanning the full
# corpus distance range so we can match the target signature distance.
for _p in _ADJ.values():
    for _n in _p.get("neighbours", []):
        if _n.get("offsets"):
            seen, deduped = set(), []
            for _d in sorted(_n["offsets"], key=lambda d: d[0] ** 2 + d[1] ** 2):
                _t = (_d[0], _d[1])
                if _t not in seen:
                    seen.add(_t)
                    deduped.append(_d)
            _n["offsets"] = deduped
GAMEPLAY_PUR = [
    "MINE",
    "DWELLING",
    "BANK",
    "REWARD_PICKUP",
    "RESOURCE_PILE",
    "STAT_PERMANENT",
    "BONUS_TEMP",
    "SPELL_SKILL",
    "INFO",
    "MANA",
    "SPECIAL",
    "GUARD",
]

# specific TYPE -> object entry (animation/mask/subtype), per terrain, so the graph
# can place the exact type it asks for (oakTrees on grass vs pineTrees on snow).
TYPE2ENTRY = collections.defaultdict(dict)  # terr_int -> type -> entry
TYPE2ENTRY_ANY = {}
for _purp, _terrs in OBJ.items():
    for _ts, _items in _terrs.items():
        for _e in _items:
            TYPE2ENTRY[int(_ts)].setdefault(_e["type"], _e)
            TYPE2ENTRY_ANY.setdefault(_e["type"], _e)


def is_mountain(e):
    t = (e["type"] if isinstance(e, str) else e["type"]).lower()
    return "mount" in t or "hill" in t or t == "rock" or "cliff" in t


def wpick(pool, rnd):
    if not pool:
        return None
    tot = sum(e.get("weight", 1) for e in pool)
    r = rnd.random() * tot
    for e in pool:
        r -= e.get("weight", 1)
        if r <= 0:
            return e
    return pool[-1]


def pool_for(purpose, terr):
    p = OBJ.get(purpose, {})
    return p.get(str(terr)) or p.get("2") or next((v for v in p.values() if v), [])


def mountain_pool(terr):
    return [e for e in pool_for("DECORATION", terr) if is_mountain(e)] or [
        e for e in pool_for("DECORATION", 2) if is_mountain(e)
    ]


def veg_pool(terr):
    return [e for e in pool_for("DECORATION", terr) if not is_mountain(e)] or [
        e for e in pool_for("DECORATION", 2) if not is_mountain(e)
    ]


def blocked_count(mask):
    return sum(ch != "V" for r in mask for ch in r)


def occ_tiles(x, y, mask):
    h = len(mask)
    w = max(len(r) for r in mask)
    yield (x, y)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            if ch != "V":
                yield (x - (w - 1 - c), y - (h - 1 - r))


def guard_level(strength):
    return min(7, max(1, round(strength / 13.0)))


def realize(W=72, H=72, seed=7, params=None):
    p = params or {}
    rnd = random.Random(seed)
    density = dict(DEFAULT_DENSITY)
    density.update(PRIOR_DENSITY)
    density.update(p.get("density", {}))
    biome_w = p.get("biome_weights") or PRIOR_BIOME  # {terr: weight}, default = corpus prior
    tree = deps_gen.generate_tree(
        W,
        H,
        seed=seed,
        n_target=p.get("n_target"),
        max_depth=p.get("max_depth"),
        portal_frac=p.get("portal_frac"),
    )
    em = deps_embed.embed(tree)
    zone = em["zone"]
    pos = em["pos"]
    barrier = [row[:] for row in em["barrier"]]
    chokes = {(c["x"], c["y"]): c for c in em["chokepoints"]}
    for (cx, cy), c in chokes.items():
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H and zone[y][x] in (c["a"], c["b"]):
                    barrier[y][x] = False

    def near_choke(x, y, r=2):
        return any(max(abs(x - cx), abs(y - cy)) <= r for (cx, cy) in chokes)

    # biomes: assign per zone to MATCH the target's tile proportions -- the
    # largest zone takes the biome with the most remaining budget, so the huge
    # commons no longer blows up one colour (fixes the grass overshoot).
    tiles = W * H
    water_frac = p.get("water_frac", PRIOR_WATER)
    zone_size = collections.Counter()
    for row in zone:
        for z in row:
            zone_size[z] += 1
    land_tiles = tiles * (1 - water_frac)
    if biome_w:
        budget = {
            int(t): w * land_tiles for t, w in biome_w.items() if int(t) in LAND_BIOMES and w > 0
        }
    else:
        budget = {b: land_tiles / len(LAND_BIOMES) for b in LAND_BIOMES}
    if not budget:
        budget = {ROOT_BIOME: land_tiles}
    biome = {}
    for zid, sz in zone_size.most_common():
        t = max(budget, key=budget.get)
        biome[zid] = t
        budget[t] -= sz

    terr = [
        [{"t": biome[zone[y][x]], "view": VIEW.get(biome[zone[y][x]], 0)} for x in range(W)]
        for y in range(H)
    ]

    occupied = set()
    objs = []

    # WATER: carve organic lakes to the target water fraction, keeping gates and
    # town seeds on land; roads are reverted to land where they cross water.
    water = set()
    if water_frac > 0.02:
        nfw = deps_embed._noise(W, H, max(6, min(W, H) // 6), rnd)
        seeds_xy = [(int(pos[i][0]), int(pos[i][1])) for i in pos]
        elig = []
        for y in range(H):
            for x in range(W):
                if barrier[y][x] or near_choke(x, y, 3):
                    continue
                if any((x - sx) ** 2 + (y - sy) ** 2 < 25 for sx, sy in seeds_xy):
                    continue
                elig.append((nfw(x, y), x, y))
        elig.sort()
        for _, x, y in elig[: int(water_frac * tiles)]:
            water.add((x, y))
            occupied.add((x, y))
            terr[y][x] = {"t": WATER, "view": VIEW.get(WATER, 24)}

    def emit(e, x, y):
        t = list(occ_tiles(x, y, e["mask"]))
        if any(not (0 <= tx < W and 0 <= ty < H) or (tx, ty) in occupied for tx, ty in t):
            return False
        for q in t:
            occupied.add(q)
        objs.append(
            {
                "type": e["type"],
                "subtype": e["subtype"],
                "animation": e["animation"],
                "mask": e["mask"],
                "x": x,
                "y": y,
                "l": 0,
            }
        )
        return True

    def place(purpose, tcode, x, y):
        e = wpick(pool_for(purpose, tcode), rnd)
        return emit(e, x, y) if e else False

    def reserve_approach(o):
        """Reserve a visitable object's entrance tiles so nothing later blocks the
        only way in. Towns are entered from the sides/below (visitableFrom
        ["---","+-+","+++"]); sealing that tile strands the whole zone behind it."""
        for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]):
            if ch != "A":
                continue
            for dx, dy in ((-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H:
                    occupied.add((x, y))

    def free_near(zid, cx, cy, r=6):
        for rad in range(r):
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    if max(abs(dx), abs(dy)) != rad:
                        continue
                    x, y = cx + dx, cy + dy
                    if (
                        0 <= x < W
                        and 0 <= y < H
                        and (x, y) not in occupied
                        and not barrier[y][x]
                        and zone[y][x] == zid
                    ):
                        yield x, y

    # towns: match TOWN density, dispersed; richest pockets first
    sig = p.get("sig") or PRIOR_SIG
    town_sep = sig.get(("TOWN", "TOWN"), 14)
    town_target = max(1, round(density.get("TOWN", 1.0) * tiles / 1000.0))
    towns = []

    def add_town(zid, cx, cy):
        for x, y in free_near(zid, cx, cy, r=9):
            if all(
                (x - tx) ** 2 + (y - ty) ** 2 >= (0.8 * town_sep) ** 2 for tx, ty in towns
            ) and place("TOWN", biome[zid], x, y):
                reserve_approach(objs[-1])
                towns.append((x, y))
                return (x, y)
        return None

    root_town = add_town(0, int(pos[0][0]), int(pos[0][1]))
    for n in sorted((n for n in tree["nodes"] if n["id"]), key=lambda n: -n["value"]):
        if len(towns) >= town_target:
            break
        add_town(n["id"], int(pos[n["id"]][0]), int(pos[n["id"]][1]))

    # SURFACE end of the subterranean gate (two-level maps): place at
    # sig[(TRANSPORT, TOWN)] tiles from the start town when that distance is reachable
    # within zone 0; fall back to r=16 scatter when no sig-distance tile exists.
    # (Many maps have the dungeon entrance far from the start town — e.g. Twins 34 tiles,
    # Search for the Grail 23 tiles — placing it at r=16 produces a large sig error.)
    ug_xy = None
    if p.get("two_level") and root_town:
        _cx0, _cy0 = int(pos[0][0]), int(pos[0][1])
        _ug_tgt = sig.get(("TRANSPORT", "TOWN"))
        _ug_sig_cands: list = []
        if _ug_tgt is not None and _ug_tgt > 16:
            _r_ug = int(_ug_tgt + 9) + 1
            for _dy in range(-_r_ug, _r_ug + 1):
                for _dx in range(-_r_ug, _r_ug + 1):
                    _d = math.sqrt(_dx * _dx + _dy * _dy)
                    if abs(_d - _ug_tgt) <= 8.0:
                        _nx, _ny = _cx0 + _dx, _cy0 + _dy
                        if (
                            0 <= _nx < W and 0 <= _ny < H
                            and zone[_ny][_nx] == 0
                            and not barrier[_ny][_nx]
                            and (_nx, _ny) not in water
                            and (_nx - root_town[0]) ** 2 + (_ny - root_town[1]) ** 2 >= 36
                        ):
                            _ug_sig_cands.append((abs(_d - _ug_tgt), _nx, _ny))
            _ug_sig_cands.sort()
        _ug_iter = [(nx, ny) for _, nx, ny in _ug_sig_cands[:64]] + list(
            free_near(0, _cx0, _cy0, r=16)
        )
        for x, y in _ug_iter:
            if (x - root_town[0]) ** 2 + (y - root_town[1]) ** 2 < 36 or (x, y) in water:
                continue
            pool = [
                e
                for e in pool_for("TRANSPORT", biome[zone[y][x]])
                if e["type"] == "subterraneanGate"
            ]
            e = wpick(pool or pool_for("TRANSPORT", biome[zone[y][x]]), rnd)
            if e and emit(e, x, y):
                reserve_approach(objs[-1])
                ug_xy = (x, y)
                break

    # guards at chokepoints
    for (cx, cy), c in chokes.items():
        lvl = guard_level(c["strength"])
        pool = [
            e for e in pool_for("GUARD", biome[c["a"]]) if e["type"] == f"randomMonsterLevel{lvl}"
        ] or pool_for("GUARD", biome[c["a"]])
        e = wpick(pool, rnd)
        if e and (cx, cy) not in occupied:
            occupied.add((cx, cy))
            objs.append(
                {
                    "type": e["type"],
                    "subtype": e["subtype"],
                    "animation": e["animation"],
                    "mask": e["mask"],
                    "x": cx,
                    "y": cy,
                    "l": 0,
                }
            )

    # gated rewards in pockets (BANK left to the density layer so it isn't overshot;
    # DWELLING excluded so _place_anchored handles all dwellings at the correct
    # sig-derived town distance -- gated rewards placed dwellings at node centers
    # (r=5 scatter) before _place_anchored fired, pre-filling the budget and leaving
    # all dwellings 2-3 tiles from each other instead of sig-specified 14-16 tiles)
    TIERS = [
        (12, ["REWARD_PICKUP", "MINE"]),
        (6, ["MINE", "REWARD_PICKUP"]),
        (0, ["REWARD_PICKUP", "RESOURCE_PILE"]),
    ]
    for n in tree["nodes"]:
        if n["id"] == 0 or n["value"] <= 0:
            continue
        spots = list(free_near(n["id"], int(pos[n["id"]][0]), int(pos[n["id"]][1]), r=5))
        budget = n["value"]
        tries = 0
        while budget > 0 and spots and tries < 6:
            purposes = next(ps for thr, ps in TIERS if budget >= thr)
            x, y = spots.pop(rnd.randrange(len(spots)))
            if place(rnd.choice(purposes), biome[n["id"]], x, y):
                budget -= 6
            tries += 1

    def _self_dist_ok(pur: str, nx: int, ny: int) -> bool:
        """True if (nx,ny) is at least min_d tiles from all already-placed objects of
        the same purpose.  min_d comes from sig[(pur,pur)] minus a 4-tile tolerance.
        """
        self_tgt = sig.get((pur, pur), 0.0)
        min_d = self_tgt - 4.0
        if min_d <= 2.0:
            return True
        min_d2 = min_d * min_d
        for o in objs:
            if o.get("l", 0) == 0 and TYPE2PURPOSE.get(o["type"]) == pur:
                if (nx - o["x"]) ** 2 + (ny - o["y"]) ** 2 < min_d2:
                    return False
        return True

    # portals: place at sig[(TRANSPORT, TOWN)] tiles from towns within the portal zone.
    # r=4 from zone center ignores sig and lands portals at ~10 tiles from towns even
    # when the real map has them at 30+ tiles (e.g. Twins: real=34.1, old gen=10.8).
    _ptrans_tgt = sig.get(("TRANSPORT", "TOWN"))
    _portal_town_pts = [
        (o["x"], o["y"])
        for o in objs
        if TYPE2PURPOSE.get(o["type"]) == "TOWN" and o.get("l", 0) == 0
    ]
    for pt in em["portals"]:
        placed_portal = False
        if _ptrans_tgt is not None and _portal_town_pts and _ptrans_tgt <= W:
            r_t = int(_ptrans_tgt + 6) + 1
            cands = []
            for tx, ty in _portal_town_pts:
                for dy in range(-r_t, r_t + 1):
                    for dx in range(-r_t, r_t + 1):
                        d_val = math.sqrt(dx * dx + dy * dy)
                        if abs(d_val - _ptrans_tgt) <= 5.0:
                            nx, ny = tx + dx, ty + dy
                            if (
                                0 <= nx < W
                                and 0 <= ny < H
                                and zone[ny][nx] == pt["zone"]
                                and not barrier[ny][nx]
                                and (nx, ny) not in occupied
                                and not near_choke(nx, ny, 2)
                            ):
                                cands.append((abs(d_val - _ptrans_tgt), nx, ny))
            cands.sort()
            for _, nx, ny in cands[:32]:
                if place("TRANSPORT", biome.get(pt["zone"], 2), nx, ny):
                    placed_portal = True
                    break
        if not placed_portal:
            for x, y in free_near(pt["zone"], pt["x"], pt["y"], r=40):
                if place("TRANSPORT", biome.get(pt["zone"], 2), x, y):
                    break

    # road overlay on gated paths
    def line(x0, y0, x1, y1):
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        pts = []
        while True:
            pts.append((x0, y0))
            if (x0, y0) == (x1, y1):
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy
        return pts

    road = set()
    for (cx, cy), c in chokes.items():
        for sx, sy in (pos[c["a"]], pos[c["b"]]):
            for x, y in line(cx, cy, int(sx), int(sy)):
                if 0 <= x < W and 0 <= y < H and not barrier[y][x]:
                    if (x, y) in water:  # a road bridges the lake -> revert to land
                        water.discard((x, y))
                        occupied.discard((x, y))
                        terr[y][x] = {
                            "t": biome[zone[y][x]],
                            "view": VIEW.get(biome[zone[y][x]], 0),
                        }
                    terr[y][x]["ot"] = 2
                    terr[y][x]["od"] = ROAD_DIR
                    road.add((x, y))

    # mountain ranges on the seams
    def can_mtn(t):
        return all(
            0 <= tx < W
            and 0 <= ty < H
            and (tx, ty) not in occupied
            and (tx, ty) not in road
            and not near_choke(tx, ty, 2)
            for tx, ty in t
        )

    seam = [(x, y) for y in range(H) for x in range(W) if barrier[y][x]]
    rnd.shuffle(seam)
    mtn_cover = set()
    for x, y in seam:
        if (x, y) in mtn_cover or (x, y) in occupied or near_choke(x, y, 2):
            continue
        big = [e for e in mountain_pool(biome[zone[y][x]]) if 3 <= blocked_count(e["mask"]) <= 6]
        e = wpick(big or mountain_pool(biome[zone[y][x]]), rnd)
        if not e:
            continue
        t = list(occ_tiles(x, y, e["mask"]))
        if not can_mtn(t):
            continue
        for q in t:
            mtn_cover.add(q)
            occupied.add(q)
        objs.append(
            {
                "type": e["type"],
                "subtype": e["subtype"],
                "animation": e["animation"],
                "mask": e["mask"],
                "x": x,
                "y": y,
                "l": 0,
            }
        )
    for x, y in seam:
        if (x, y) in mtn_cover or (x, y) in occupied or near_choke(x, y, 1):
            continue
        e = wpick(
            [e for e in mountain_pool(biome[zone[y][x]]) if blocked_count(e["mask"]) == 1],
            rnd,
        )
        if e:
            mtn_cover.add((x, y))
            occupied.add((x, y))
            objs.append(
                {
                    "type": e["type"],
                    "subtype": e["subtype"],
                    "animation": e["animation"],
                    "mask": e["mask"],
                    "x": x,
                    "y": y,
                    "l": 0,
                }
            )

    # interior tiles available for ambient objects / vegetation (paths kept clear)
    road_clear = set()
    for x, y in road:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                road_clear.add((x + dx, y + dy))
    interior = [
        (x, y)
        for y in range(H)
        for x in range(W)
        if not barrier[y][x]
        and (x, y) not in occupied
        and (x, y) not in road_clear
        and not near_choke(x, y, 2)
    ]
    rnd.shuffle(interior)
    cursor = [0]

    def next_spot():
        while cursor[0] < len(interior):
            x, y = interior[cursor[0]]
            cursor[0] += 1
            if (x, y) not in occupied:
                return x, y
        return None

    # OBJECT PLACEMENT BY THE IMMEDIATE-TOUCH ADJACENCY GRAPH ------------------
    # Terrain/macro is fixed; we only place the objects ON it. Each placed object
    # grows the SPECIFIC types that abut it ON ITS TERRAIN, at real touching offsets:
    # a grass mine pulls oakTrees + a guard; a snow mine pulls pineTrees +
    # deadVegetation; oakTrees pull oakTrees (a forest). Per-purpose density is only a
    # CAP; the graph sets the shape, and guard<->treasure coupling is emergent.
    NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

    def ok_tile(x, y):
        return (
            0 <= x < W
            and 0 <= y < H
            and not barrier[y][x]
            and (x, y) not in occupied
            and (x, y) not in road_clear
            and not near_choke(x, y, 2)
        )

    decor_anchor = set()

    def emit_decor(e, x, y):
        # decoration MAY overlap other decoration (even multi-tile) -- it only has to
        # avoid gameplay/barrier/water tiles, and not stack on the same anchor. This
        # is what lets trees pack into forests instead of small rock filling the gaps.
        if (x, y) in decor_anchor:
            return False
        tiles = list(occ_tiles(x, y, e["mask"]))
        if any(not (0 <= tx < W and 0 <= ty < H) or (tx, ty) in occupied for tx, ty in tiles):
            return False
        decor_anchor.add((x, y))
        objs.append(
            {
                "type": e["type"],
                "subtype": e["subtype"],
                "animation": e["animation"],
                "mask": e["mask"],
                "x": x,
                "y": y,
                "l": 0,
            }
        )
        return True

    def place_type(T, x, y):  # place a SPECIFIC type, terrain-correct
        e = TYPE2ENTRY.get(terr[y][x]["t"], {}).get(T) or TYPE2ENTRY_ANY.get(T)
        if not e:
            return False
        return emit_decor(e, x, y) if TYPE2PURPOSE.get(T) == "DECORATION" else emit(e, x, y)

    targets = {pur: int(round(density.get(pur, 0) * tiles / 1000.0)) for pur in GAMEPLAY_PUR}
    decor_target = int(round(density.get("DECORATION", 140) * tiles / 1000.0))
    placed = collections.Counter(TYPE2PURPOSE.get(o["type"], "?") for o in objs)

    def cap_ok(T2):
        pur = TYPE2PURPOSE.get(T2)
        if pur == "DECORATION":
            return placed["DECORATION"] < decor_target
        return pur in targets and placed[pur] < targets[pur]

    _town_pts = [
        (o["x"], o["y"])
        for o in objs
        if TYPE2PURPOSE.get(o["type"]) == "TOWN" and o.get("l", 0) == 0
    ]
    _town_ci: dict = {}  # per-purpose round-robin index into _town_pts

    def _place_anchored(pur: str) -> bool:
        """Place one object of `pur` at sig[(pur,"TOWN")] tiles from a town.
        Enforces a minimum self-distance between same-purpose objects so that
        round-robin placement to the same town ring does not cluster them.
        Returns True (after incrementing placed[pur]) on success, False if no
        valid tile found within the ring (caller falls back to next_spot()).
        """
        if not _town_pts:
            return False
        tgt = sig.get((pur, "TOWN"))
        if tgt is None or tgt > 35:
            return False
        ci = _town_ci.get(pur, 0)
        r = int(tgt + 4) + 1
        for attempt in range(len(_town_pts)):
            tx, ty = _town_pts[(ci + attempt) % len(_town_pts)]
            cands = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    d = math.sqrt(dx * dx + dy * dy)
                    if abs(d - tgt) <= 3.5:
                        nx, ny = tx + dx, ty + dy
                        if ok_tile(nx, ny) and _self_dist_ok(pur, nx, ny):
                            cands.append((abs(d - tgt), nx, ny))
            cands.sort()
            for _, nx, ny in cands[:32]:
                if ok_tile(nx, ny) and _self_dist_ok(pur, nx, ny) and place(pur, terr[ny][nx]["t"], nx, ny):
                    placed[pur] += 1
                    _town_ci[pur] = (ci + attempt + 1) % len(_town_pts)
                    return True
        return False

    # Mine/dwelling/bank seeds: use town-relative placement so they land at the
    # corpus-derived distance from towns rather than fully random scatter. This
    # ensures the mine→guard grow motif ends up at approximately the right town
    # distance, and fixes the main spreading problem (mines in random zones far
    # from any town inflate every pair involving mines, overwhelming the adjacency
    # signal). Falls back to next_spot() if no sig entry or no free ring tile.
    #
    # Bank exception: when sig[(MINE,BANK)] > sig[(MINE,TOWN)] + sig[(BANK,TOWN)],
    # mines and banks CANNOT be on the same island (triangle inequality violated).
    # Town-ring places banks near the same towns as mines → small mine-bank distance.
    # Instead, use mine-anchored placement at the correct sig[(MINE,BANK)] distance,
    # which lands banks on different islands from mines (matching real-map geometry).
    _mb_tgt = sig.get(("MINE", "BANK"))
    _mine_town_d = sig.get(("MINE", "TOWN")) or 0.0
    _bank_town_d = sig.get(("BANK", "TOWN")) or 0.0
    _use_mine_anch_bank = bool(
        _mb_tgt is not None
        and _mb_tgt <= 35
        and _mb_tgt > _mine_town_d + _bank_town_d
    )

    for anchor in ("MINE", "DWELLING"):
        while placed[anchor] < targets.get(anchor, 0):
            if not _place_anchored(anchor):
                s = next_spot()
                if s is None:
                    break
                if place(anchor, terr[s[1]][s[0]]["t"], *s):
                    placed[anchor] += 1

    # Mine positions used for mine-anchored placement (BANK pre-placement + leftover fill).
    _mine_pts = [
        (o["x"], o["y"])
        for o in objs
        if TYPE2PURPOSE.get(o["type"]) == "MINE" and o.get("l", 0) == 0
    ]
    _mine_anch_ci: dict = {}  # per-purpose round-robin index into _mine_pts
    def _place_mine_anchored(pur: str, tgt: float) -> bool:
        """Place one pur object at tgt tiles from a mine (round-robin across mines).

        Only fires when tgt ≤ 35 and there are mines to anchor to.  Used for:
        - GUARD when tgt > 9.5 (grow step skipped this pair; mine-ring matches corpus)
        - BANK when mines and banks must be on different islands (SEP condition)
        """
        if not _mine_pts or tgt > 35:
            return False
        ci = _mine_anch_ci.get(pur, 0)
        r = int(tgt + 4) + 1
        for attempt in range(len(_mine_pts)):
            mx, my = _mine_pts[(ci + attempt) % len(_mine_pts)]
            cands = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    d = math.sqrt(dx * dx + dy * dy)
                    if abs(d - tgt) <= 3.5:
                        nx, ny = mx + dx, my + dy
                        if ok_tile(nx, ny) and _self_dist_ok(pur, nx, ny):
                            cands.append((abs(d - tgt), nx, ny))
            cands.sort()
            for _, nx, ny in cands[:32]:
                if ok_tile(nx, ny) and _self_dist_ok(pur, nx, ny) and place(pur, terr[ny][nx]["t"], nx, ny):
                    placed[pur] += 1
                    _mine_anch_ci[pur] = (ci + attempt + 1) % len(_mine_pts)
                    return True
        return False

    # Purpose-to-purpose anchored placement: when sig[(Q, P)] ≤ P2P_THRESH tiles,
    # place P near already-placed Q objects at that corpus distance. This fixes the
    # dominant failure mode where every purpose ends up in the same town rings
    # (wrong inter-purpose distances): BANK→BANK clustering, GUARD near rewards, etc.
    _P2P_THRESH = 17.0
    _p2p_anch_ci: dict = {}  # per-(pur, anchor_pur) round-robin index

    def _place_p2p_anchored(pur: str, anchor_pur: str, tgt: float) -> bool:
        """Place one pur object at tgt tiles from an anchor_pur object on level 0.
        Round-robins over already-placed anchor_pur objects. Mirrors _place_mine_anchored."""
        if tgt > 35:
            return False
        anch_pts = [
            (o["x"], o["y"])
            for o in objs
            if TYPE2PURPOSE.get(o["type"]) == anchor_pur and o.get("l", 0) == 0
        ]
        if not anch_pts:
            return False
        # Town-ring guard: only accept positions that are also within ±7 tiles of the
        # purpose's target town distance.  Without this, self-anchor chains objects
        # across the map (each placed 9-10 tiles from the last, drifting >35 tiles
        # from any town and breaking all other pairs that depend on town proximity).
        town_tgt = sig.get((pur, "TOWN"))
        _P2P_TOWN_TOL = 7.0
        ci_key = (pur, anchor_pur)
        ci = _p2p_anch_ci.get(ci_key, 0)
        r = int(tgt + 4) + 1
        for attempt in range(len(anch_pts)):
            px, py = anch_pts[(ci + attempt) % len(anch_pts)]
            cands = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    d = math.sqrt(dx * dx + dy * dy)
                    if abs(d - tgt) <= 3.5:
                        nx, ny = px + dx, py + dy
                        if not ok_tile(nx, ny):
                            continue
                        if town_tgt is not None and town_tgt <= 30 and _town_pts:
                            if not any(
                                abs(math.sqrt((nx - tx) ** 2 + (ny - ty) ** 2) - town_tgt)
                                <= _P2P_TOWN_TOL
                                for tx, ty in _town_pts
                            ):
                                continue
                        cands.append((abs(d - tgt), nx, ny))
            cands.sort()
            for _, nx, ny in cands[:32]:
                if ok_tile(nx, ny) and _self_dist_ok(pur, nx, ny) and place(pur, terr[ny][nx]["t"], nx, ny):
                    placed[pur] += 1
                    _p2p_anch_ci[ci_key] = (ci + attempt + 1) % len(anch_pts)
                    return True
        return False

    def _try_p2p(pur: str) -> bool:
        """Self-cluster: place pur at sig[(pur,pur)] tiles from an already-placed pur
        object.  Only fires when the self-distance is tight (≤ P2P_THRESH) and at
        least one pur object is already placed.  Cross-purpose anchoring is deliberately
        excluded: it moves objects away from their town rings, inflating town-distance
        errors for other pairs."""
        tgt = sig.get((pur, pur))
        if tgt is None or tgt > _P2P_THRESH:
            return False
        return _place_p2p_anchored(pur, pur, tgt)

    # When banks are naturally close-clustered (sig(BANK,BANK) ≤ _P2P_THRESH), alternate
    # mine-anchor (even index = seed) and p2p (odd index = pair partner).  This replicates
    # real maps where banks form pairs ~sig(BANK,BANK) tiles apart near different mines,
    # giving correct BANK→BANK spacing without collapsing all banks into one cluster.
    # (Full p2p chain: degeneration for large bank counts, e.g. Twins 12 banks → 2-tile median;
    #  pure mine-anchor: over-disperses, e.g. Rebellion 5 banks → 24-tile median vs sig 9.8.)
    _bank_bb = sig.get(("BANK", "BANK"))
    _tight_bank = _bank_bb is not None and _bank_bb <= _P2P_THRESH

    while placed["BANK"] < targets.get("BANK", 0):
        _bank_is_pair = _tight_bank and placed["BANK"] % 2 == 1
        if _bank_is_pair and _try_p2p("BANK"):
            continue
        if _use_mine_anch_bank and _place_mine_anchored("BANK", _mb_tgt):
            continue
        if _place_anchored("BANK"):
            continue
        if _tight_bank and not _bank_is_pair and _try_p2p("BANK"):
            continue
        s = next_spot()
        if s is None:
            break
        if place("BANK", terr[s[1]][s[0]]["t"], *s):
            placed["BANK"] += 1

    def grow_touch(o, T2, offs):
        T2_pur = TYPE2PURPOSE.get(T2)
        tgt_d = None
        if not offs:
            candidates = NB8
        else:
            anc_pur = TYPE2PURPOSE.get(o["type"])
            tgt_d = sig.get((anc_pur, T2_pur)) if anc_pur and T2_pur else None
            if tgt_d is not None:
                # All adjacency offsets are immediate-touch (1–8 tiles). When the
                # target places this pair further apart than any offset can reach,
                # forced-close growth makes sp_mean larger, not smaller: the
                # generated distance (~3 tiles) is more wrong than density-implied
                # scatter (~tgt_d tiles). Skip; leftover fill matches target spacing.
                max_off_d = max(math.sqrt(d[0] ** 2 + d[1] ** 2) for d in offs)
                if tgt_d > max_off_d + 1.5:
                    return False
                # Sort deduplicated offsets by closeness to the target signature
                # distance for this pair so placed objects match the real map's
                # inter-object spacing rather than always landing at 1-2 tiles.
                candidates = sorted(
                    offs, key=lambda d: abs(math.sqrt(d[0] ** 2 + d[1] ** 2) - tgt_d)
                )[:16]
            else:
                # No target signature (plain generation): prefer closest offsets.
                candidates = offs[:16]
        for dx, dy in candidates:
            if tgt_d is not None:
                actual_d = math.sqrt(dx ** 2 + dy ** 2)
                if abs(actual_d - tgt_d) > 2.0:
                    break  # remaining candidates are further from tgt_d (sorted)
            x, y = o["x"] + dx, o["y"] + dy
            if not ok_tile(x, y):
                continue
            if place_type(T2, x, y):
                return True
        return False

    def drain(queue, allow_decor=True, seed_only=False):
        qi = 0
        seed_end = len(queue) if seed_only else None
        while qi < len(queue) and qi < 40000:
            if seed_end is not None and qi >= seed_end:
                break  # don't cascade: only process objects that were seeds
            o = queue[qi]
            qi += 1
            X = terr[o["y"]][o["x"]]["t"] if 0 <= o["x"] < W and 0 <= o["y"] < H else 2
            prof = _ADJ.get(f"{X}|{o['type']}")
            if not prof:
                continue
            deg = prof.get("avg_degree", 0)
            anc_pur = TYPE2PURPOSE.get(o["type"])
            for entry in prof.get("neighbours", []):
                T2 = entry["type"]
                if not allow_decor and TYPE2PURPOSE.get(T2) == "DECORATION":
                    continue
                # In the gameplay pass (allow_decor=False), skip RESOURCE_PILE and
                # REWARD_PICKUP growth.  Placing them adjacent to mines or chokepoint
                # guards puts them at wrong cross-purpose distances from SPELL_SKILL,
                # BANK, etc. — the shuffled control beats the graph for those pairs.
                # Leftover fill places them at sig-derived town/mine distances instead.
                if not allow_decor and TYPE2PURPOSE.get(T2) in ("RESOURCE_PILE", "REWARD_PICKUP"):
                    continue
                # Decoration anchors only grow decoration: gameplay objects placed by
                # decoration-adjacency end up at tree positions, not at their target
                # signature distances.  Leftover fill handles them with town-anchoring.
                if anc_pur == "DECORATION" and TYPE2PURPOSE.get(T2) != "DECORATION":
                    continue
                if not cap_ok(T2):
                    continue
                lam = entry["share"] * deg
                n = int(lam) + (1 if rnd.random() < (lam - int(lam)) else 0)
                offs = entry.get("offsets")
                for _ in range(n):
                    if not cap_ok(T2):
                        break
                    if grow_touch(o, T2, offs):
                        placed[TYPE2PURPOSE.get(T2, "?")] += 1
                        queue.append(objs[-1])

    # grow GAMEPLAY motifs from anchors first (mine -> guard + resources); decoration
    # is handled by the forest blanket below so small rock doesn't crowd out trees.
    # seed_only=True: stop after one level — grown objects do not cascade further.
    # Without this limit the cascade packs mine/town zones with 4+ objects each,
    # exhausting the good interior tiles and pushing BONUS_TEMP / BANK / etc. to
    # the outer zones far from towns, which mirrors the shuffled control or worse.
    _PRI = {"MINE": 0, "DWELLING": 0, "BANK": 0, "TOWN": 1, "GUARD": 1}
    drain(
        sorted(
            (o for o in objs if f"{terr[o['y']][o['x']]['t']}|{o['type']}" in _ADJ),
            key=lambda o: _PRI.get(TYPE2PURPOSE.get(o["type"]), 2),
        ),
        allow_decor=False,
        seed_only=True,
    )

    # Guarantee mine→guard coupling when the target map actually has tight coupling
    # (sig MINE→GUARD < 3.0 tiles). Without this condition, forcing guards within
    # 1-2 tiles of mines on maps where guards are far from mines increases the
    # signature error, making the graph worse than the shuffled control on those maps.
    _mg_sig = sig.get(("MINE", "GUARD"))
    if _mg_sig is not None and _mg_sig < 3.0:
        guard_xy = {(q["x"], q["y"]) for q in objs if TYPE2PURPOSE.get(q["type"]) == "GUARD"}
        for anc in [o for o in objs if TYPE2PURPOSE.get(o["type"]) == "MINE"]:
            if any(max(abs(gx - anc["x"]), abs(gy - anc["y"])) <= 5 for gx, gy in guard_xy):
                continue
            aterr = terr[anc["y"]][anc["x"]]["t"] if 0 <= anc["x"] < W and 0 <= anc["y"] < H else 2
            aprof = _ADJ.get(f"{aterr}|{anc['type']}")
            if not aprof:
                continue
            for aentry in sorted(
                [e for e in aprof.get("neighbours", []) if TYPE2PURPOSE.get(e["type"]) == "GUARD"],
                key=lambda e: -e["share"],
            ):
                if not cap_ok(aentry["type"]):
                    continue
                if grow_touch(anc, aentry["type"], aentry.get("offsets")):
                    placed[TYPE2PURPOSE.get(aentry["type"], "?")] += 1
                    guard_xy.add((objs[-1]["x"], objs[-1]["y"]))
                    break

    # blanket the rest with terrain-correct decoration forests grown from seeds
    while placed["DECORATION"] < decor_target:
        s = next_spot()
        if s is None:
            break
        x, y = s
        e = wpick(veg_pool(terr[y][x]["t"]), rnd)
        if e and emit_decor(e, x, y):
            placed["DECORATION"] += 1
            drain([objs[-1]])

    # leftover fill: top up any gameplay purpose still under its cap.
    # Mine-anchored fill is used for:
    #   GUARD: when the grow step skipped the mine→guard pair (tgt > adjacency reach
    #          ≈ 9.5 tiles), mine-anchored leftover fill places guards at the corpus
    #          mine→guard distance instead of clustering them in town rings.
    #   BANK: when mines and banks are on different islands (SEP condition), continue
    #         mine-anchored placement in leftover fill (same policy as pre-placement).
    #   RESOURCE_PILE: grow step no longer places these adjacent to mines; mine-
    #          anchored fill restores correct MINE→RESOURCE_PILE spacing when the sig
    #          distance is small (≤ 12 tiles, reachable in a typical zone).
    #
    # Process rarest purposes first so high-count purposes (RESOURCE_PILE,
    # REWARD_PICKUP) do not saturate the town-adjacent rings before rare auxiliaries
    # (SPELL_SKILL, MANA, BONUS_TEMP, STAT_PERMANENT) get their correct positions.
    # Tight self-clustering: when sig[(pur,pur)] ≤ _TIGHT_SELF tiles, try p2p
    # BEFORE town-ring once the first object is placed.  This prevents purposes that
    # should cluster (BONUS_TEMP self-dist 5.5 tiles, REWARD_PICKUP 1-3 tiles, etc.)
    # from scattering to separate town rings, which makes graph worse than shuffle.
    _TIGHT_SELF = 12.0
    _fill_order = sorted(GAMEPLAY_PUR, key=lambda pur: targets.get(pur, 0))
    for pur in _fill_order:
        while placed[pur] < targets.get(pur, 0):
            if pur == "GUARD":
                _mg_tgt = sig.get(("MINE", "GUARD"))
                if _mg_tgt is not None and _mg_tgt > 9.5 and _place_mine_anchored("GUARD", _mg_tgt):
                    continue
            elif pur == "BANK":
                _bank_is_pair = _tight_bank and placed["BANK"] % 2 == 1
                if _bank_is_pair and _try_p2p("BANK"):
                    continue
                if _use_mine_anch_bank and _place_mine_anchored("BANK", _mb_tgt):
                    continue
            elif pur == "SPELL_SKILL":
                # Mine-far: some maps have SPELL_SKILL far from mines (sig > 15 tiles).
                # Town-ring placement puts SS near mines (when mines are near towns);
                # mine-anchored placement at sig(MINE,SS) ensures the correct distance.
                _ms_ss = sig.get(("MINE", "SPELL_SKILL"))
                if _ms_ss is not None and _ms_ss > 15 and _place_mine_anchored("SPELL_SKILL", _ms_ss):
                    continue
            elif pur == "RESOURCE_PILE":
                # Restore MINE→RESOURCE_PILE spacing: grow step no longer places these
                # adjacent to mines, so use mine-anchored fill when the sig distance is
                # reachable within a zone (≤ 12 tiles).
                _mr_tgt = sig.get(("MINE", "RESOURCE_PILE"))
                if _mr_tgt is not None and _mr_tgt <= 12 and _place_mine_anchored("RESOURCE_PILE", _mr_tgt):
                    continue
            # Tight self-clustering: for purposes that should cluster tightly (small
            # self-distance in the real map), prefer p2p over town-ring scatter once
            # the first object is correctly placed.  This prevents objects with small
            # real-map self-distance (e.g. BONUS_TEMP=5.5 tiles) from ending up in
            # separate town rings 20+ tiles apart, which the shuffled control beats.
            _pur_self_d = sig.get((pur, pur), 99)
            if _pur_self_d <= _TIGHT_SELF and placed[pur] >= 1 and _try_p2p(pur):
                continue
            if _place_anchored(pur):
                continue
            if _try_p2p(pur):
                continue
            s = next_spot()
            if s is None:
                break
            if place(pur, terr[s[1]][s[0]]["t"], *s):
                placed[pur] += 1

    objs = reachability_repair(objs, terr, W, H, water, biome, zone)

    # UNDERGROUND LEVEL: ~65% of the corpus is two-level (rock-filled with
    # subterranean caverns). Build level 1 only when the target is two-level so
    # the single-level default path is byte-identical. The surface places half of
    # each purpose's budget; the cavern places the other half -> together they hit
    # the per-(2x-tile) corpus density, and rock/subterranean terrain now appears.
    levels = [terr]
    if p.get("two_level") and ug_xy:
        terr_u, uobjs = build_underground(W, H, rnd, density, p, ug_xy[0], ug_xy[1])
        levels.append(terr_u)
        objs += uobjs

    fm = {"name": f"deps_fit_s{seed}", "terrain": levels, "objects": objs}
    # main town: the editor matches a player's mainTown to the town at
    # (anchor-2, anchor-2) -- the corpus offset for the town footprint anchor.
    if root_town:
        rx, ry = root_town
        fm["main_town"] = {"x": rx - 2, "y": ry - 2, "l": 0}
    return fm, tree, em


def _mask_cells(x, y, mask):
    h = len(mask)
    w = max(len(r) for r in mask)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            yield (x - (w - 1 - c), y - (h - 1 - r), ch)


def reachability_repair(objs, terr, W, H, water, biome, zone):
    """Every visitable object must have a walkable approach connected to the map.
    Carve the shortest path from any walled-in object to the main land mass,
    removing only decoration/mountains and converting water -- never gameplay
    objects. Clears the editor's 'no visitable directions' and isolated pockets."""
    import heapq

    has_visit = lambda o: any("A" in row for row in o["mask"])
    blocked = [[False] * W for _ in range(H)]
    owner = [[-1] * W for _ in range(H)]  # removable (decor/mountain) blocker per tile
    for idx, o in enumerate(objs):
        rem = not has_visit(o)
        for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]):
            if 0 <= cx < W and 0 <= cy < H and ch == "B":
                blocked[cy][cx] = True
                if rem and owner[cy][cx] < 0:
                    owner[cy][cx] = idx
    for x, y in water:
        blocked[y][x] = True

    comp = [[-1] * W for _ in range(H)]
    sizes = []
    for y in range(H):
        for x in range(W):
            if not blocked[y][x] and comp[y][x] < 0:
                cid = len(sizes)
                st = [(x, y)]
                comp[y][x] = cid
                n = 0
                while st:
                    a, b = st.pop()
                    n += 1
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = a + dx, b + dy
                        if 0 <= nx < W and 0 <= ny < H and not blocked[ny][nx] and comp[ny][nx] < 0:
                            comp[ny][nx] = cid
                            st.append((nx, ny))
                sizes.append(n)
    main_id = max(range(len(sizes)), key=lambda c: sizes[c]) if sizes else -1
    main_extra = set()
    is_main = lambda x, y: comp[y][x] == main_id or (x, y) in main_extra

    to_remove = set()
    to_land = set()
    for o in objs:
        if not has_visit(o):
            continue
        Av = [
            (cx, cy)
            for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"])
            if ch == "A" and 0 <= cx < W and 0 <= cy < H
        ]
        if not Av:
            continue
        if any(
            0 <= ax + dx < W
            and 0 <= ay + dy < H
            and not blocked[ay + dy][ax + dx]
            and is_main(ax + dx, ay + dy)
            for (ax, ay) in Av
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
        ):
            continue  # already reachable
        dist = {}
        prev = {}
        pq = []
        for ax, ay in Av:
            dist[(ax, ay)] = 0
            heapq.heappush(pq, (0, ax, ay))
        found = None
        while pq:
            d, x, y = heapq.heappop(pq)
            if d > dist.get((x, y), 1e9) or d > 10:
                continue
            if not blocked[y][x] and is_main(x, y):
                found = (x, y)
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                if not blocked[ny][nx]:
                    c = 0
                elif (nx, ny) in water or owner[ny][nx] >= 0:
                    c = 1
                else:
                    continue  # gameplay block = wall
                nd = d + c
                if nd < dist.get((nx, ny), 1e9):
                    dist[(nx, ny)] = nd
                    prev[(nx, ny)] = (x, y)
                    heapq.heappush(pq, (nd, nx, ny))
        if found:
            cur = found
            while cur is not None:
                x, y = cur
                if cur in water:
                    to_land.add(cur)
                if owner[y][x] >= 0:
                    to_remove.add(owner[y][x])
                blocked[y][x] = False
                main_extra.add(cur)
                cur = prev.get(cur)

    for x, y in to_land:
        b = biome[zone[y][x]]
        terr[y][x] = {"t": b, "view": VIEW.get(b, 0)}
    return [o for i, o in enumerate(objs) if i not in to_remove]


def build_underground(W, H, rnd, density, p, gx, gy):
    """Second map level for two-level targets: rock fill (terrain 9) + one connected
    subterranean cavern (terrain 6) grown organically from the shared gate tile,
    with gameplay objects + guards scattered in the cavern. Returns
    (terrain_grid, objects[l=1]). The underground subterranean gate shares (gx, gy)
    with the surface gate so VCMI auto-links the pair (descend/ascend works)."""
    import heapq

    ROCK, SUB = 9, 6
    terr_u = [[{"t": ROCK, "view": VIEW.get(ROCK, 2)} for _ in range(W)] for _ in range(H)]
    tt = p.get("terrain_target", {})
    g = lambda k: tt.get(k, tt.get(str(k), 0.0))
    f6, f9 = g(6), g(9)
    sub_frac = f6 / (f6 + f9) if (f6 + f9) > 0 else 0.15
    sub_frac = max(0.05, min(0.45, sub_frac))
    target = max(20, int(sub_frac * W * H))
    nf = deps_embed._noise(W, H, max(6, min(W, H) // 6), rnd)

    def carve(x, y):
        if 0 <= x < W and 0 <= y < H:
            terr_u[y][x] = {"t": SUB, "view": VIEW.get(SUB, 53)}
            walk.add((x, y))

    walk = set()
    carve(gx, gy)
    pq = []

    def push(x, y):
        if 0 <= x < W and 0 <= y < H and (x, y) not in walk:
            heapq.heappush(pq, (-(nf(x, y) + 1.0), x, y))

    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        push(gx + dx, gy + dy)
    while pq and len(walk) < target:
        _, x, y = heapq.heappop(pq)
        if (x, y) in walk:
            continue
        carve(x, y)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            push(x + dx, y + dy)
    for dx in (-1, 0, 1):  # guarantee the gate has clearance
        for dy in (-1, 0, 1):
            carve(gx + dx, gy + dy)

    occ = set()
    objs = []
    blockedB = set()
    NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def _cells(e, x, y, ch_want):
        return [
            (cx, cy)
            for cx, cy, ch in _mask_cells(x, y, e["mask"])
            if ch == ch_want and 0 <= cx < W and 0 <= cy < H
        ]

    def flood(seeds, extra_block):
        """Passable cavern tiles reachable from `seeds`: SUB tiles minus object 'B'
        footprints (blockedB) minus a tentative extra-block set."""
        seen, st = set(), []
        for s in seeds:
            if s in walk and s not in blockedB and s not in extra_block:
                seen.add(s)
                st.append(s)
        while st:
            x, y = st.pop()
            for dx, dy in NB4:
                n = (x + dx, y + dy)
                if n in walk and n not in blockedB and n not in extra_block and n not in seen:
                    seen.add(n)
                    st.append(n)
        return seen

    gate_seeds = []
    reach = set(walk)  # before the gate: whole cavern
    gate = wpick(
        [e for e in pool_for("TRANSPORT", SUB) if e["type"] == "subterraneanGate"]
        or pool_for("TRANSPORT", SUB),
        rnd,
    )
    if gate:
        for q in occ_tiles(gx, gy, gate["mask"]):
            occ.add(q)
        blockedB |= set(_cells(gate, gx, gy, "B"))
        objs.append(
            {
                "type": gate["type"],
                "subtype": gate["subtype"],
                "animation": gate["animation"],
                "mask": gate["mask"],
                "x": gx,
                "y": gy,
                "l": 1,
            }
        )
        gate_seeds = _cells(gate, gx, gy, "A") or [(gx, gy)]
        # The gate's only walkable entrance is one tile (mask 'BAB'); reserve the
        # A cell and its ring so no scattered object seals the cavern off from the
        # descend point (mirrors the surface reserve_approach for towns).
        for ax, ay in gate_seeds:
            occ.add((ax, ay))
            for dx, dy in NB8:
                occ.add((ax + dx, ay + dy))
        reach = flood(gate_seeds, set())  # walkable cavern from the gate

    def emit_u(e, x, y):
        foot = list(occ_tiles(x, y, e["mask"]))
        if any(not (0 <= tx < W and 0 <= ty < H) or (tx, ty) in occ for tx, ty in foot):
            return False
        appr = _cells(e, x, y, "A") or [(x, y)]
        if any(a not in reach for a in appr):  # entrance reachable FROM the gate
            return False
        nb = set(_cells(e, x, y, "B"))
        if nb:  # connectivity-preserving: this
            new_reach = flood(gate_seeds, nb)  # footprint must not strand any
            if not (reach - nb) <= new_reach:  # currently-reachable cavern tile
                return False
            reach.clear()
            reach.update(new_reach)
        for q in foot:
            occ.add(q)
        blockedB.update(nb)
        objs.append(
            {
                "type": e["type"],
                "subtype": e["subtype"],
                "animation": e["animation"],
                "mask": e["mask"],
                "x": x,
                "y": y,
                "l": 1,
            }
        )
        return True

    cells = [xy for xy in walk if xy not in occ]
    rnd.shuffle(cells)
    cur = [0]

    def nxt():
        while cur[0] < len(cells):
            xy = cells[cur[0]]
            cur[0] += 1
            if xy not in occ:
                return xy
        return None

    UND = [
        "MINE",
        "RESOURCE_PILE",
        "REWARD_PICKUP",
        "DWELLING",
        "BANK",
        "STAT_PERMANENT",
        "SPELL_SKILL",
        "BONUS_TEMP",
        "MANA",
        "SPECIAL",
        "INFO",
    ]
    placed_xy = []
    for purpose in UND:
        need = int(round(density.get(purpose, 0) * W * H / 1000.0))
        tries = 0
        while need > 0 and tries < need * 30 + 5:
            tries += 1
            xy = nxt()
            if xy is None:
                break
            x, y = xy
            e = wpick(pool_for(purpose, SUB), rnd)
            if e and emit_u(e, x, y):
                placed_xy.append((x, y))
                need -= 1

    gbudget = int(round(density.get("GUARD", 0) * W * H / 1000.0))
    rnd.shuffle(placed_xy)
    for ox, oy in placed_xy:
        if gbudget <= 0:
            break
        nbs = NB8[:]
        rnd.shuffle(nbs)
        for dx, dy in nbs:
            x, y = ox + dx, oy + dy
            if (x, y) in walk and (x, y) not in occ:
                e = wpick(pool_for("GUARD", SUB), rnd)
                if e:
                    occ.add((x, y))
                    objs.append(
                        {
                            "type": e["type"],
                            "subtype": e["subtype"],
                            "animation": e["animation"],
                            "mask": e["mask"],
                            "x": x,
                            "y": y,
                            "l": 1,
                        }
                    )
                    gbudget -= 1
                break
    return terr_u, objs


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    fm, tree, em = realize(seed=seed)
    print("objects:", len(fm["objects"]))
    faithful.save(fm, f"{ROOT}/out/deps_proto.json")
    print("wrote", faithful.to_vmap(fm, f"{ROOT}/out/deps_proto.vmap", name=fm["name"]))
