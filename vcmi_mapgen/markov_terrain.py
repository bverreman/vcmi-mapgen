"""Terrain Markov chain LEARNED from the 159 real maps (the user's idea #1).

P(terrain[x,y] | left, up, up-left), estimated from real surface terrain, sampled
in raster order with back-off. This reproduces the real LOCAL texture (patch sizes,
coastlines, how terrains border each other) instead of arbitrary noise blobs.
"""

import json, glob, random, collections, os
import pathlib

from vcmi_mapgen import render

ROOT = pathlib.Path(__file__).parent.parent


def learn(level_index):
    """counts for P(center | left, up, upleft) over real maps at the given level."""
    full = collections.defaultdict(collections.Counter)  # (l,u,ul)->center
    pair = collections.defaultdict(collections.Counter)  # (l,u)->center
    one = collections.defaultdict(collections.Counter)  # (l,)->center
    marg = collections.Counter()
    for f in glob.glob(f"{ROOT}/maps_json/*.json"):
        m = json.load(open(f))
        if level_index >= len(m["terrain"]):
            continue
        g = m["terrain"][level_index]
        H = len(g)
        W = len(g[0])
        T = [[c["t"] for c in row] for row in g]
        for y in range(H):
            for x in range(W):
                c = T[y][x]
                marg[c] += 1
                l = T[y][x - 1] if x > 0 else None
                u = T[y - 1][x] if y > 0 else None
                ul = T[y - 1][x - 1] if (x > 0 and y > 0) else None
                if l is not None and u is not None and ul is not None:
                    full[(l, u, ul)][c] += 1
                if l is not None and u is not None:
                    pair[(l, u)][c] += 1
                if l is not None:
                    one[(l,)][c] += 1
    return {"full": full, "pair": pair, "one": one, "marg": marg}


def _sample(counter, rnd):
    tot = sum(counter.values())
    r = rnd.random() * tot
    acc = 0
    for k, v in counter.items():
        acc += v
        if r <= acc:
            return k
    return next(iter(counter))


def generate(model, W, H, rnd, thresh=12):
    g = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            l = g[y][x - 1] if x > 0 else None
            u = g[y - 1][x] if y > 0 else None
            ul = g[y - 1][x - 1] if (x > 0 and y > 0) else None
            dist = None
            if (
                l is not None
                and u is not None
                and ul is not None
                and sum(model["full"][(l, u, ul)].values()) >= thresh
            ):
                dist = model["full"][(l, u, ul)]
            elif l is not None and u is not None and sum(model["pair"][(l, u)].values()) >= thresh:
                dist = model["pair"][(l, u)]
            elif l is not None and sum(model["one"][(l,)].values()) >= 1:
                dist = model["one"][(l,)]
            else:
                dist = model["marg"]
            g[y][x] = _sample(dist, rnd)
    return g


def learn4(level_index):
    """P(center | left,up,right,down) for isotropic Gibbs, with back-off tables."""
    full = collections.defaultdict(collections.Counter)  # (l,u,r,d)->c
    pair = collections.defaultdict(
        collections.Counter
    )  # frozenset-ish (sorted 2 of nbrs) backoff via opposite pairs
    horiz = collections.defaultdict(collections.Counter)  # (l,r)->c
    vert = collections.defaultdict(collections.Counter)  # (u,d)->c
    for f in glob.glob(f"{ROOT}/maps_json/*.json"):
        m = json.load(open(f))
        if level_index >= len(m["terrain"]):
            continue
        g = m["terrain"][level_index]
        H = len(g)
        W = len(g[0])
        T = [[c["t"] for c in row] for row in g]
        for y in range(1, H - 1):
            for x in range(1, W - 1):
                c = T[y][x]
                l = T[y][x - 1]
                u = T[y - 1][x]
                r = T[y][x + 1]
                d = T[y + 1][x]
                full[(l, u, r, d)][c] += 1
                horiz[(l, r)][c] += 1
                vert[(u, d)][c] += 1
    return {"full": full, "horiz": horiz, "vert": vert}


def gibbs(grid, M4, marg, rnd, sweeps=5, thresh=10):
    H = len(grid)
    W = len(grid[0])
    for s in range(sweeps):
        for y in range(1, H - 1):
            for x in range(1, W - 1):
                l = grid[y][x - 1]
                u = grid[y - 1][x]
                r = grid[y][x + 1]
                d = grid[y + 1][x]
                if sum(M4["full"][(l, u, r, d)].values()) >= thresh:
                    dist = M4["full"][(l, u, r, d)]
                else:
                    dist = collections.Counter()
                    dist.update(M4["horiz"][(l, r)])
                    dist.update(M4["vert"][(u, d)])
                    if not dist:
                        dist = marg
                grid[y][x] = _sample(dist, rnd)
    return grid


if __name__ == "__main__":
    rnd = random.Random(3)
    print("learning surface terrain Markov from 159 maps...")
    M = learn(0)
    M4 = learn4(0)
    print(
        f"  contexts: full={len(M['full'])} pair={len(M['pair'])}  marginal terrains={dict(M['marg'].most_common())}"
    )
    W = H = 72
    os.makedirs(f"{ROOT}/out/render", exist_ok=True)
    gen = generate(M, W, H, rnd)  # raster init

    def asmap(g, name):
        return {
            "name": name,
            "width": W,
            "height": H,
            "terrain": [[[{"t": t, "river": False, "road": False} for t in row] for row in g]],
            "objects": [],
        }

    render.render_map(asmap(gen, "raster"), f"{ROOT}/out/render/markov_terrain.png")
    geng = [row[:] for row in gen]
    gibbs(geng, M4, M["marg"], rnd, sweeps=6)  # isotropic smoothing
    hist = collections.Counter(t for row in geng for t in row)
    print("  post-Gibbs terrain histogram:", dict(hist.most_common()))
    render.render_map(asmap(geng, "markov gibbs"), f"{ROOT}/out/render/markov_terrain_gibbs.png")
    # a real map of similar size, terrain only, for side-by-side
    real = None
    for f in glob.glob(f"{ROOT}/maps_json/*.json"):
        m = json.load(open(f))
        if m["width"] <= margin if False else (60 <= m["width"] <= 90 and not m["twoLevel"]):
            real = m
            break
    if real:
        real_terronly = dict(real)
        real_terronly["objects"] = []
        render.render_map(real_terronly, f"{ROOT}/out/render/real_terrain_{real['width']}.png")
        print(f"  real comparison: {real['name']} {real['width']}x{real['height']}")
    print("wrote render/markov_terrain.png", "and real_terrain_*.png" if real else "")
