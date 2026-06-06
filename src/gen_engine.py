"""Step 3: generation engine. Samples the factored model and emits the FAITHFUL
representation, written to an editor-valid .vmap via faithful.to_vmap. Places REAL
objects (authentic VCMI ids + animations) drawn from the measured object library.
"""

import json, random, math, collections, sys, heapq
from collections import deque

sys.path.insert(0, "src")
import faithful

ROOT = "/mnt/data/workspace/vcmi-mapgen"
F = json.load(open(f"{ROOT}/out/factors.json"))
LIB = json.load(open(f"{ROOT}/out/objlib.json"))
TOT_TILES = sum(F["proportions"].values())
RATE = {
    p: sum(v["counts"]) / TOT_TILES for p, v in F["budget"].items()
}  # per-tile rate per purpose
LAND = (0, 1, 2, 3, 4, 5)
WATER = 8


def vnoise(w, h, cell, rnd):
    gw, gh = w // cell + 2, h // cell + 2
    g = [[rnd.random() for _ in range(gw)] for _ in range(gh)]
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        gy = y / cell
        y0 = int(gy)
        fy = gy - y0
        for x in range(w):
            gx = x / cell
            x0 = int(gx)
            fx = gx - x0
            a = g[y0][x0] * (1 - fx) + g[y0][x0 + 1] * fx
            b = g[y0 + 1][x0] * (1 - fx) + g[y0 + 1][x0 + 1] * fx
            out[y][x] = a * (1 - fy) + b * fy
    return out


def gen_terrain(W, H, rnd):
    # macro biome scaffold with corpus-like land proportions, then Markov edge-blend
    props = F["proportions"]
    land = {t: props[str(t)] for t in LAND if str(t) in props}
    tot = sum(land.values())
    types = list(land)
    wts = [land[t] / tot for t in types]
    nseeds = max(10, W * H // 500)
    seeds = [
        (rnd.randrange(W), rnd.randrange(H), rnd.choices(types, wts)[0]) for _ in range(nseeds)
    ]
    nz = vnoise(W, H, max(6, W // 10), rnd)
    g = [[2] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            best = 1e18
            bt = 2
            for sx, sy, st in seeds:
                d = ((x - sx) ** 2 + (y - sy) ** 2) * (0.55 + 1.3 * nz[y][x])
                if d < best:
                    best = d
                    bt = st
            g[y][x] = bt
    mk = F["markov_surface"]["full"]
    for _ in range(2):
        for y in range(1, H - 1):
            for x in range(1, W - 1):
                l, u, r, d = g[y][x - 1], g[y - 1][x], g[y][x + 1], g[y + 1][x]
                dist = mk.get(f"{l},{u},{r},{d}")
                ch = {l, u, r, d, g[y][x]}
                if dist:
                    sub = {int(k): v for k, v in dist.items() if int(k) in ch}
                    if sub:
                        tot = sum(sub.values())
                        rr = rnd.random() * tot
                        acc = 0
                        for k, v in sub.items():
                            acc += v
                            if rr <= acc:
                                g[y][x] = k
                                break
    return g


def place_towns(g, K, rnd, sep):
    H = len(g)
    W = len(g[0])
    cand = [
        (x, y)
        for y in range(2, H - 2)
        for x in range(2, W - 2)
        if g[y][x] in (2, 0, 5)
        and all(g[y + dy][x + dx] != WATER for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    ]
    rnd.shuffle(cand)
    towns = []
    for x, y in cand:
        if all((x - tx) ** 2 + (y - ty) ** 2 >= sep * sep for tx, ty in towns):
            towns.append((x, y))
            if len(towns) >= K:
                break
    return towns


def astar(g, s, t):
    H = len(g)
    W = len(g[0])

    def cost(x, y):
        return {WATER: 40, 7: 12, 5: 3, 9: 30}.get(g[y][x], 1)

    pq = [(0, s)]
    dist = {s: 0}
    prev = {}
    while pq:
        c, (x, y) = heapq.heappop(pq)
        if (x, y) == t:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H:
                nc = c + cost(nx, ny)
                if nc < dist.get((nx, ny), 1e18):
                    dist[(nx, ny)] = nc
                    prev[(nx, ny)] = (x, y)
                    heapq.heappush(pq, (nc + abs(nx - t[0]) + abs(ny - t[1]), (nx, ny)))
    path = []
    cur = t
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
    return path


def clearance(W, H, towns, roads, RZONE=9, RROAD=2):
    clr = [[False] * W for _ in range(H)]

    def stamp(cx, cy, r):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) <= r and 0 <= cx + dx < W and 0 <= cy + dy < H:
                    clr[cy + dy][cx + dx] = True

    for tx, ty in towns:
        stamp(tx, ty, RZONE)
    for rx, ry in roads:
        stamp(rx, ry, RROAD)
    dist = [[1e9] * W for _ in range(H)]
    q = deque()
    for y in range(H):
        for x in range(W):
            if clr[y][x]:
                dist[y][x] = 0
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and dist[ny][nx] > dist[y][x] + 1:
                dist[ny][nx] = dist[y][x] + 1
                q.append((nx, ny))
    return clr, dist


def mask_cells(mask, x, y):
    m = mask or ["B"]
    Hh = len(m)
    Ww = max(len(r) for r in m)
    for r in range(Hh):
        for c in range(len(m[r])):
            if m[r][c] == "B":
                yield x - (Ww - 1 - c), y - (Hh - 1 - r)
    yield x, y


def sample_tpl(items, rnd):
    tot = sum(i["weight"] for i in items)
    rr = rnd.random() * tot
    acc = 0
    for i in items:
        acc += i["weight"]
        if rr <= acc:
            return i
    return items[-1]


def generate(W=72, H=72, seed=7):
    rnd = random.Random(seed)
    g = gen_terrain(W, H, rnd)
    towns = place_towns(g, 5, rnd, 20)
    roads = set()
    edges = sorted(
        (math.dist(a, b), i, j) for i, a in enumerate(towns) for j, b in enumerate(towns) if i < j
    )
    par = list(range(len(towns)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    for w, i, j in edges:
        if find(i) != find(j):
            par[find(i)] = find(j)
            for x, y in astar(g, towns[i], towns[j]):
                if g[y][x] != WATER:
                    g[y][x] = 0
                    roads.add((x, y))
    clr, dist = clearance(W, H, towns, roads)
    occ = set()
    objs = []
    gen_tiles = W * H
    # towns (open zones) from library
    if LIB.get("TOWN"):
        any_t = [it for terr in LIB["TOWN"].values() for it in terr]
        for k, (x, y) in enumerate(towns):
            it = sample_tpl(any_t, rnd)
            objs.append(
                {
                    "x": x,
                    "y": y,
                    "l": 0,
                    "type": it["type"],
                    "subtype": it["subtype"],
                    "animation": it["animation"],
                    "mask": it["mask"],
                }
            )
            for cc in mask_cells(it["mask"], x, y):
                occ.add(cc)
    # gameplay objects by purpose budget, on affinity terrain, in wilderness
    GAMEPLAY = [
        "MINE",
        "RESOURCE_PILE",
        "REWARD_PICKUP",
        "GUARD",
        "DWELLING",
        "STAT_PERMANENT",
        "BONUS_TEMP",
        "BANK",
        "INFO",
        "SPELL_SKILL",
        "MANA",
        "TERRAIN_MODIFIER",
        "WATER_TRANSPORT",
        "TRANSPORT",
        "SPECIAL",
        "QUEST_GATE",
    ]
    for p in GAMEPLAY:
        if p not in LIB:
            continue
        target = int(RATE.get(p, 0) * gen_tiles)
        placed = 0
        tries = 0
        while placed < target and tries < target * 30 + 200:
            tries += 1
            x = rnd.randrange(W)
            y = rnd.randrange(H)
            t = g[y][x]
            if clr[y][x] or (x, y) in occ:
                continue
            terr = LIB[p].get(str(t))
            if not terr:
                continue
            it = sample_tpl(terr, rnd)
            cells = list(mask_cells(it["mask"], x, y))
            if any(c in occ or not (0 <= c[0] < W and 0 <= c[1] < H) for c in cells):
                continue
            for cc in cells:
                occ.add(cc)
            objs.append(
                {
                    "x": x,
                    "y": y,
                    "l": 0,
                    "type": it["type"],
                    "subtype": it["subtype"],
                    "animation": it["animation"],
                    "mask": it["mask"],
                }
            )
            placed += 1
    # decoration: wilderness, density ramps from paths, from library
    field = vnoise(W, H, max(6, W // 8), rnd)
    for y in range(H):
        for x in range(W):
            if clr[y][x] or (x, y) in occ:
                continue
            t = g[y][x]
            terr = LIB.get("DECORATION", {}).get(str(t))
            if not terr:
                continue
            dens = (
                F["density"].get(str(t), 0)
                / 1000.0
                * min(1, dist[y][x] / 4)
                * (0.6 + field[y][x] * 0.8)
            )
            if rnd.random() >= min(0.42, dens):
                continue
            it = sample_tpl(terr, rnd)
            cells = list(mask_cells(it["mask"], x, y))
            if any(c in occ or not (0 <= c[0] < W and 0 <= c[1] < H) for c in cells):
                continue
            for cc in cells:
                occ.add(cc)
            objs.append(
                {
                    "x": x,
                    "y": y,
                    "l": 0,
                    "type": it["type"],
                    "subtype": it["subtype"],
                    "animation": it["animation"],
                    "mask": it["mask"],
                }
            )
    terr = [
        [
            {
                "t": g[y][x],
                "view": F.get("terrain_view", {}).get(str(g[y][x]), 0),
                "rt": 0,
                "rd": 0,
                "ot": (2 if (x, y) in roads else 0),
                "od": 0,
                "m": 0,
            }
            for x in range(W)
        ]
        for y in range(H)
    ]
    fm = {
        "name": "gen",
        "width": W,
        "height": H,
        "twoLevel": False,
        "players": len(towns),
        "terrain": [terr],
        "objects": objs,
    }
    return fm


if __name__ == "__main__":
    fm = generate()
    bp = collections.Counter()
    import ontology  # purpose tally by type not available; tally by type

    for o in fm["objects"]:
        bp[o["type"]] += 1
    print(f"generated {fm['width']}x{fm['height']}  objects={len(fm['objects'])}")
    faithful.save(fm, f"{ROOT}/out/gen_engine.json")
    faithful.to_vmap(fm, f"{ROOT}/out/gen_engine.vmap")
    print("top object types:", dict(bp.most_common(10)))
    print("wrote out/gen_engine.vmap")
