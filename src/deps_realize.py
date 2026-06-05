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
SCATTER = ["MINE", "DWELLING", "BANK", "REWARD_PICKUP", "RESOURCE_PILE",
           "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL", "INFO", "MANA", "SPECIAL"]


def is_mountain(e):
    t = (e["type"] if isinstance(e, str) else e["type"]).lower()
    return ("mount" in t or "hill" in t or t == "rock" or "cliff" in t)


def wpick(pool, rnd):
    if not pool: return None
    tot = sum(e.get("weight", 1) for e in pool)
    r = rnd.random() * tot
    for e in pool:
        r -= e.get("weight", 1)
        if r <= 0: return e
    return pool[-1]


def pool_for(purpose, terr):
    p = OBJ.get(purpose, {})
    return p.get(str(terr)) or p.get("2") or next((v for v in p.values() if v), [])


def mountain_pool(terr):
    return [e for e in pool_for("DECORATION", terr) if is_mountain(e)] or \
           [e for e in pool_for("DECORATION", 2) if is_mountain(e)]


def veg_pool(terr):
    return [e for e in pool_for("DECORATION", terr) if not is_mountain(e)] or \
           [e for e in pool_for("DECORATION", 2) if not is_mountain(e)]


def blocked_count(mask):
    return sum(ch != 'V' for r in mask for ch in r)


def occ_tiles(x, y, mask):
    h = len(mask); w = max(len(r) for r in mask)
    yield (x, y)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            if ch != 'V':
                yield (x - (w - 1 - c), y - (h - 1 - r))


def guard_level(strength):
    return min(7, max(1, round(strength / 13.0)))


def realize(W=72, H=72, seed=7, params=None):
    p = params or {}
    rnd = random.Random(seed)
    density = dict(DEFAULT_DENSITY); density.update(PRIOR_DENSITY); density.update(p.get("density", {}))
    biome_w = p.get("biome_weights") or PRIOR_BIOME   # {terr: weight}, default = corpus prior
    tree = deps_gen.generate_tree(W, H, seed=seed, n_target=p.get("n_target"),
                                  max_depth=p.get("max_depth"), portal_frac=p.get("portal_frac"))
    em = deps_embed.embed(tree)
    zone = em["zone"]; pos = em["pos"]
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
        for z in row: zone_size[z] += 1
    land_tiles = tiles * (1 - water_frac)
    if biome_w:
        budget = {int(t): w * land_tiles for t, w in biome_w.items()
                  if int(t) in LAND_BIOMES and w > 0}
    else:
        budget = {b: land_tiles / len(LAND_BIOMES) for b in LAND_BIOMES}
    if not budget: budget = {ROOT_BIOME: land_tiles}
    biome = {}
    for zid, sz in zone_size.most_common():
        t = max(budget, key=budget.get)
        biome[zid] = t; budget[t] -= sz

    terr = [[{"t": biome[zone[y][x]], "view": VIEW.get(biome[zone[y][x]], 0)}
             for x in range(W)] for y in range(H)]

    occupied = set(); objs = []

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
        for _, x, y in elig[:int(water_frac * tiles)]:
            water.add((x, y)); occupied.add((x, y))
            terr[y][x] = {"t": WATER, "view": VIEW.get(WATER, 24)}

    def emit(e, x, y):
        t = list(occ_tiles(x, y, e["mask"]))
        if any(not (0 <= tx < W and 0 <= ty < H) or (tx, ty) in occupied for tx, ty in t):
            return False
        for q in t: occupied.add(q)
        objs.append({"type": e["type"], "subtype": e["subtype"], "animation": e["animation"],
                     "mask": e["mask"], "x": x, "y": y, "l": 0})
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
                    if max(abs(dx), abs(dy)) != rad: continue
                    x, y = cx + dx, cy + dy
                    if 0 <= x < W and 0 <= y < H and (x, y) not in occupied \
                       and not barrier[y][x] and zone[y][x] == zid:
                        yield x, y

    # towns: match TOWN density, dispersed; richest pockets first
    sig = p.get("sig") or PRIOR_SIG
    town_sep = sig.get(("TOWN", "TOWN"), 14)
    town_target = max(1, round(density.get("TOWN", 1.0) * tiles / 1000.0))
    towns = []
    def add_town(zid, cx, cy):
        for x, y in free_near(zid, cx, cy, r=9):
            if all((x - tx) ** 2 + (y - ty) ** 2 >= (0.8 * town_sep) ** 2 for tx, ty in towns) \
               and place("TOWN", biome[zid], x, y):
                reserve_approach(objs[-1]); towns.append((x, y)); return (x, y)
        return None
    root_town = add_town(0, int(pos[0][0]), int(pos[0][1]))
    for n in sorted((n for n in tree["nodes"] if n["id"]), key=lambda n: -n["value"]):
        if len(towns) >= town_target: break
        add_town(n["id"], int(pos[n["id"]][0]), int(pos[n["id"]][1]))

    # guards at chokepoints
    for (cx, cy), c in chokes.items():
        lvl = guard_level(c["strength"])
        pool = [e for e in pool_for("GUARD", biome[c["a"]]) if e["type"] == f"randomMonsterLevel{lvl}"] \
               or pool_for("GUARD", biome[c["a"]])
        e = wpick(pool, rnd)
        if e and (cx, cy) not in occupied:
            occupied.add((cx, cy))
            objs.append({"type": e["type"], "subtype": e["subtype"], "animation": e["animation"],
                         "mask": e["mask"], "x": cx, "y": cy, "l": 0})

    # gated rewards in pockets (BANK left to the density layer so it isn't overshot)
    TIERS = [(12, ["DWELLING", "MINE"]), (6, ["MINE", "REWARD_PICKUP"]),
             (0, ["REWARD_PICKUP", "RESOURCE_PILE"])]
    for n in tree["nodes"]:
        if n["id"] == 0 or n["value"] <= 0: continue
        spots = list(free_near(n["id"], int(pos[n["id"]][0]), int(pos[n["id"]][1]), r=5))
        budget = n["value"]; tries = 0
        while budget > 0 and spots and tries < 6:
            purposes = next(ps for thr, ps in TIERS if budget >= thr)
            x, y = spots.pop(rnd.randrange(len(spots)))
            if place(rnd.choice(purposes), biome[n["id"]], x, y): budget -= 6
            tries += 1

    # portals
    for pt in em["portals"]:
        for x, y in free_near(pt["zone"], pt["x"], pt["y"], r=4):
            if place("TRANSPORT", biome.get(pt["zone"], 2), x, y): break

    # road overlay on gated paths
    def line(x0, y0, x1, y1):
        dx, dy = abs(x1 - x0), abs(y1 - y0); sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
        err = dx - dy; pts = []
        while True:
            pts.append((x0, y0))
            if (x0, y0) == (x1, y1): break
            e2 = 2 * err
            if e2 > -dy: err -= dy; x0 += sx
            if e2 < dx: err += dx; y0 += sy
        return pts
    road = set()
    for (cx, cy), c in chokes.items():
        for (sx, sy) in (pos[c["a"]], pos[c["b"]]):
            for (x, y) in line(cx, cy, int(sx), int(sy)):
                if 0 <= x < W and 0 <= y < H and not barrier[y][x]:
                    if (x, y) in water:                # a road bridges the lake -> revert to land
                        water.discard((x, y)); occupied.discard((x, y))
                        terr[y][x] = {"t": biome[zone[y][x]], "view": VIEW.get(biome[zone[y][x]], 0)}
                    terr[y][x]["ot"] = 2; terr[y][x]["od"] = ROAD_DIR; road.add((x, y))

    # mountain ranges on the seams
    def can_mtn(t):
        return all(0 <= tx < W and 0 <= ty < H and (tx, ty) not in occupied
                   and (tx, ty) not in road and not near_choke(tx, ty, 2) for tx, ty in t)
    seam = [(x, y) for y in range(H) for x in range(W) if barrier[y][x]]
    rnd.shuffle(seam); mtn_cover = set()
    for (x, y) in seam:
        if (x, y) in mtn_cover or (x, y) in occupied or near_choke(x, y, 2): continue
        big = [e for e in mountain_pool(biome[zone[y][x]]) if 3 <= blocked_count(e["mask"]) <= 6]
        e = wpick(big or mountain_pool(biome[zone[y][x]]), rnd)
        if not e: continue
        t = list(occ_tiles(x, y, e["mask"]))
        if not can_mtn(t): continue
        for q in t: mtn_cover.add(q); occupied.add(q)
        objs.append({"type": e["type"], "subtype": e["subtype"], "animation": e["animation"],
                     "mask": e["mask"], "x": x, "y": y, "l": 0})
    for (x, y) in seam:
        if (x, y) in mtn_cover or (x, y) in occupied or near_choke(x, y, 1): continue
        e = wpick([e for e in mountain_pool(biome[zone[y][x]]) if blocked_count(e["mask"]) == 1], rnd)
        if e:
            mtn_cover.add((x, y)); occupied.add((x, y))
            objs.append({"type": e["type"], "subtype": e["subtype"], "animation": e["animation"],
                         "mask": e["mask"], "x": x, "y": y, "l": 0})

    # interior tiles available for ambient objects / vegetation (paths kept clear)
    road_clear = set()
    for (x, y) in road:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1): road_clear.add((x + dx, y + dy))
    interior = [(x, y) for y in range(H) for x in range(W)
                if not barrier[y][x] and (x, y) not in occupied
                and (x, y) not in road_clear and not near_choke(x, y, 2)]
    rnd.shuffle(interior)
    cursor = [0]
    def next_spot():
        while cursor[0] < len(interior):
            x, y = interior[cursor[0]]; cursor[0] += 1
            if (x, y) not in occupied: return x, y
        return None

    # DENSITY LAYER: scatter ambient objects to per-purpose density, but matching
    # the target's SPATIAL signature -- near towns at the right distance, and
    # dispersed from same-purpose neighbours.
    have = collections.Counter(TYPE2PURPOSE.get(o["type"], "?") for o in objs)
    placed_by = collections.defaultdict(list)
    for o in objs:
        pp = TYPE2PURPOSE.get(o["type"])
        if pp: placed_by[pp].append((o["x"], o["y"]))

    def ok_tile(x, y):
        return 0 <= x < W and 0 <= y < H and not barrier[y][x] and (x, y) not in occupied \
            and (x, y) not in road_clear and not near_choke(x, y, 2)

    for purpose in SCATTER:
        target = int(round(density.get(purpose, 0) * tiles / 1000.0))
        need = target - have.get(purpose, 0)
        sep = sig.get((purpose, purpose), 0)
        twn = sig.get((purpose, "TOWN"))
        tries = 0
        while need > 0 and tries < need * 45:
            tries += 1
            if twn and towns and rnd.random() < 0.85:          # town-biased candidate
                tx, ty = rnd.choice(towns)
                ang = rnd.random() * 6.2832; rad = twn * rnd.uniform(0.5, 1.4)
                x, y = int(tx + rad * math.cos(ang)), int(ty + rad * math.sin(ang))
                if not ok_tile(x, y): continue
            else:
                s = next_spot()
                if s is None: break
                x, y = s
            if sep > 1 and any((x - px) ** 2 + (y - py) ** 2 < (0.7 * sep) ** 2
                               for px, py in placed_by[purpose]):
                continue
            if place(purpose, terr[y][x]["t"], x, y):
                placed_by[purpose].append((x, y)); need -= 1

    # PER-TREASURE GUARDS: mines/dwellings/banks are ALWAYS guarded (as in H3);
    # remaining guard budget goes to other loot by the target's coupling order.
    NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    guard_target = round(density.get("GUARD", 0) * tiles / 1000.0)
    budget = guard_target - sum(1 for o in objs if TYPE2PURPOSE.get(o["type"]) == "GUARD")
    ALWAYS = {"MINE", "DWELLING", "BANK"}
    OPTIONAL = ["STAT_PERMANENT", "BONUS_TEMP", "REWARD_PICKUP", "SPELL_SKILL",
                "RESOURCE_PILE", "MANA", "SPECIAL"]
    must = [o for o in objs if TYPE2PURPOSE.get(o["type"]) in ALWAYS]
    rnd.shuffle(must)
    rest = []
    for pp in sorted(OPTIONAL, key=lambda q: sig.get((q, "GUARD"), 99)):
        op = [o for o in objs if TYPE2PURPOSE.get(o["type"]) == pp]
        rnd.shuffle(op); rest += op
    new_guards = []
    for o in must + rest:
        forced = TYPE2PURPOSE.get(o["type"]) in ALWAYS
        if budget <= 0 and not forced: break
        nbs = NB8[:]; rnd.shuffle(nbs)
        for dx, dy in nbs:                                # guard an approach tile of the object
            x, y = o["x"] + dx, o["y"] + dy
            if 0 <= x < W and 0 <= y < H and (x, y) not in occupied and not barrier[y][x]:
                e = wpick(pool_for("GUARD", terr[y][x]["t"]), rnd)
                if e:
                    occupied.add((x, y))
                    new_guards.append({"type": e["type"], "subtype": e["subtype"],
                                       "animation": e["animation"], "mask": e["mask"],
                                       "x": x, "y": y, "l": 0})
                    budget -= 1
                break
    objs += new_guards

    # vegetation to target density, clumped (forest/clearing)
    nf = deps_embed._noise(W, H, 9, rnd)
    decor_target = int(round(density.get("DECORATION", 140) * tiles / 1000.0))
    decor_n = 0; guard = 0
    while decor_n < decor_target and guard < decor_target * 30:
        guard += 1
        s = next_spot()
        if s is None: break
        x, y = s
        if rnd.random() > 0.18 + 0.7 * ((nf(x, y) + 1) / 2): continue
        e = wpick(veg_pool(terr[y][x]["t"]), rnd)
        if e and emit(e, x, y): decor_n += 1

    objs = reachability_repair(objs, terr, W, H, water, biome, zone)
    fm = {"name": f"deps_fit_s{seed}", "terrain": [terr], "objects": objs}
    # main town: the editor matches a player's mainTown to the town at
    # (anchor-2, anchor-2) -- the corpus offset for the town footprint anchor.
    if root_town:
        rx, ry = root_town
        fm["main_town"] = {"x": rx - 2, "y": ry - 2, "l": 0}
    return fm, tree, em


def _mask_cells(x, y, mask):
    h = len(mask); w = max(len(r) for r in mask)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            yield (x - (w - 1 - c), y - (h - 1 - r), ch)


def reachability_repair(objs, terr, W, H, water, biome, zone):
    """Every visitable object must have a walkable approach connected to the map.
    Carve the shortest path from any walled-in object to the main land mass,
    removing only decoration/mountains and converting water -- never gameplay
    objects. Clears the editor's 'no visitable directions' and isolated pockets."""
    import heapq
    has_visit = lambda o: any('A' in row for row in o["mask"])
    blocked = [[False] * W for _ in range(H)]
    owner = [[-1] * W for _ in range(H)]                  # removable (decor/mountain) blocker per tile
    for idx, o in enumerate(objs):
        rem = not has_visit(o)
        for (cx, cy, ch) in _mask_cells(o["x"], o["y"], o["mask"]):
            if 0 <= cx < W and 0 <= cy < H and ch == 'B':
                blocked[cy][cx] = True
                if rem and owner[cy][cx] < 0:
                    owner[cy][cx] = idx
    for (x, y) in water:
        blocked[y][x] = True

    comp = [[-1] * W for _ in range(H)]; sizes = []
    for y in range(H):
        for x in range(W):
            if not blocked[y][x] and comp[y][x] < 0:
                cid = len(sizes); st = [(x, y)]; comp[y][x] = cid; n = 0
                while st:
                    a, b = st.pop(); n += 1
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = a + dx, b + dy
                        if 0 <= nx < W and 0 <= ny < H and not blocked[ny][nx] and comp[ny][nx] < 0:
                            comp[ny][nx] = cid; st.append((nx, ny))
                sizes.append(n)
    main_id = max(range(len(sizes)), key=lambda c: sizes[c]) if sizes else -1
    main_extra = set()
    is_main = lambda x, y: comp[y][x] == main_id or (x, y) in main_extra

    to_remove = set(); to_land = set()
    for o in objs:
        if not has_visit(o):
            continue
        Av = [(cx, cy) for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"])
              if ch == 'A' and 0 <= cx < W and 0 <= cy < H]
        if not Av:
            continue
        if any(0 <= ax + dx < W and 0 <= ay + dy < H and not blocked[ay + dy][ax + dx]
               and is_main(ax + dx, ay + dy)
               for (ax, ay) in Av for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            continue                                       # already reachable
        dist = {}; prev = {}; pq = []
        for (ax, ay) in Av:
            dist[(ax, ay)] = 0; heapq.heappush(pq, (0, ax, ay))
        found = None
        while pq:
            d, x, y = heapq.heappop(pq)
            if d > dist.get((x, y), 1e9) or d > 10:
                continue
            if not blocked[y][x] and is_main(x, y):
                found = (x, y); break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                if not blocked[ny][nx]:
                    c = 0
                elif (nx, ny) in water or owner[ny][nx] >= 0:
                    c = 1
                else:
                    continue                               # gameplay block = wall
                nd = d + c
                if nd < dist.get((nx, ny), 1e9):
                    dist[(nx, ny)] = nd; prev[(nx, ny)] = (x, y); heapq.heappush(pq, (nd, nx, ny))
        if found:
            cur = found
            while cur is not None:
                x, y = cur
                if cur in water:
                    to_land.add(cur)
                if owner[y][x] >= 0:
                    to_remove.add(owner[y][x])
                blocked[y][x] = False; main_extra.add(cur)
                cur = prev.get(cur)

    for (x, y) in to_land:
        b = biome[zone[y][x]]
        terr[y][x] = {"t": b, "view": VIEW.get(b, 0)}
    return [o for i, o in enumerate(objs) if i not in to_remove]


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    fm, tree, em = realize(seed=seed)
    print("objects:", len(fm["objects"]))
    faithful.save(fm, f"{ROOT}/out/deps_proto.json")
    print("wrote", faithful.to_vmap(fm, f"{ROOT}/out/deps_proto.vmap", name=fm["name"]))
