"""Spatially realize a dependency tree (from deps_gen.py) onto a W x H grid.

The tree is the law; the geometry obeys it:
  - each node gets a ZONE (an organic blob grown around a sprawling-tree seed)
  - EVERY inter-zone border is an impassable BARRIER ...
  - ... EXCEPT a one-tile CHOKEPOINT on each parent->child guard edge, where the
    guard stands. Portal edges get a monolith pair instead of a land passage.
This guarantees the only way from the root to any zone is through its gate, in
dependency order -- the form the local engine could never produce.

Output: zone grid, barrier mask, chokepoint list (with guard strength), portal
pairs. Run as a script for an ASCII preview.
"""

import sys, os, json, math, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deps_gen

NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _kids(tree):
    kids = collections.defaultdict(list)
    for n in tree["nodes"]:
        if n["parent"] is not None:
            kids[n["parent"]].append(n["id"])
    return kids


def organic_layout(tree, rnd):
    """Sprawling tree layout: each branch grows OUTWARD from its parent in a
    jittered direction (children continue the parent's heading, not an evenly
    divided wedge), with min-separation rejection. Breaks radial symmetry."""
    W, H = tree["W"], tree["H"]
    kids = _kids(tree)
    ring = min(W, H) / (2.0 * (tree["max_depth"] + 1.4))
    clamp = lambda v, hi: min(hi - 3, max(2, v))
    pos, placed, heading = {}, [], {}

    # root: jittered around centre
    pos[0] = (
        clamp(W / 2 + rnd.uniform(-0.12, 0.12) * W, W),
        clamp(H / 2 + rnd.uniform(-0.12, 0.12) * H, H),
    )
    placed.append(pos[0])
    heading[0] = rnd.uniform(0, 2 * math.pi)

    def far_enough(x, y, sep):
        return all((x - px) ** 2 + (y - py) ** 2 >= sep * sep for px, py in placed)

    # breadth-first so a parent is always positioned before its children
    queue = collections.deque([0])
    while queue:
        p = queue.popleft()
        ch = kids[p]
        for i, c in enumerate(ch):
            if p == 0:  # root children fan out in any direction
                base = (
                    rnd.uniform(0, 2 * math.pi)
                    if len(ch) == 1
                    else (2 * math.pi * i / len(ch) + rnd.uniform(-0.7, 0.7))
                )
            else:  # deeper: continue outward with a kink
                base = heading[p] + rnd.uniform(-0.75, 0.75)
            spot = None
            for attempt in range(16):
                ang = base + rnd.uniform(-0.5, 0.5) * attempt / 16
                dist = ring * rnd.uniform(0.8, 1.55)
                x = clamp(pos[p][0] + dist * math.cos(ang), W)
                y = clamp(pos[p][1] + dist * math.sin(ang), H)
                if far_enough(x, y, ring * 0.72):
                    spot = (x, y, ang)
                    break
            if spot is None:  # accept a closer spot rather than fail
                x = clamp(pos[p][0] + ring * 0.8 * math.cos(base), W)
                y = clamp(pos[p][1] + ring * 0.8 * math.sin(base), H)
                spot = (x, y, base)
            pos[c] = (spot[0], spot[1])
            placed.append(pos[c])
            heading[c] = spot[2]
            queue.append(c)
    return pos


def grow_zones(tree, pos, rnd):
    """Randomized capacity-bounded region growth (organic blobs, wiggly borders).
    Targets sized by depth so the commons is large and pockets are small, as in
    the corpus (commons ~58 tiles, pocket ~5)."""
    W, H = tree["W"], tree["H"]
    nodes = {n["id"]: n for n in tree["nodes"]}

    # unique integer seed tile per node
    zone = [[-1] * W for _ in range(H)]
    seed_tile = {}
    for nid in sorted(pos):
        x, y = int(round(pos[nid][0])), int(round(pos[nid][1]))
        x, y = min(W - 1, max(0, x)), min(H - 1, max(0, y))
        while zone[y][x] != -1:
            x = min(W - 1, max(0, x + rnd.choice((-1, 1))))
            y = min(H - 1, max(0, y + rnd.choice((-1, 1))))
        zone[y][x] = nid
        seed_tile[nid] = (x, y)

    # target area by depth (commons big -> pockets small)
    dw = lambda d: 11.0 if d == 0 else 3.2 if d == 1 else 1.6 if d == 2 else 1.0
    w = {nid: dw(nodes[nid]["depth"]) for nid in pos}
    sw = sum(w.values())
    total = W * H
    target = {nid: max(6, total * w[nid] / sw) for nid in pos}

    area = collections.Counter({nid: 1 for nid in pos})
    frontier = []
    for nid, (x, y) in seed_tile.items():
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H:
                frontier.append((nx, ny, nid))
    while frontier:
        i = rnd.randrange(len(frontier))
        frontier[i], frontier[-1] = frontier[-1], frontier[i]
        x, y, zid = frontier.pop()
        if zone[y][x] != -1 or area[zid] >= target[zid]:
            continue
        zone[y][x] = zid
        area[zid] += 1
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and zone[ny][nx] == -1:
                frontier.append((nx, ny, zid))

    # fill any tiles left after every zone hit capacity: BFS from claimed land
    q = collections.deque((x, y) for y in range(H) for x in range(W) if zone[y][x] != -1)
    while q:
        x, y = q.popleft()
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and zone[ny][nx] == -1:
                zone[ny][nx] = zone[y][x]
                q.append((nx, ny))

    # domain-warp the labels with a smooth noise field -> organic, wiggly borders
    ring = min(W, H) / (2.0 * (tree["max_depth"] + 1.4))
    nfx = _noise(W, H, max(4, int(ring * 1.3)), rnd)
    nfy = _noise(W, H, max(4, int(ring * 1.3)), rnd)
    amp = ring * 0.35
    warped = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            sx = min(W - 1, max(0, int(x + amp * nfx(x, y))))
            sy = min(H - 1, max(0, int(y + amp * nfy(x, y))))
            warped[y][x] = zone[sy][sx]
    for nid, (x, y) in seed_tile.items():  # keep each seed inside its own zone
        warped[y][x] = nid
    return warped


def _noise(W, H, cell, rnd):
    """Smooth value-noise sampler: coarse random grid, bilinear interpolation."""
    gw, gh = W // cell + 3, H // cell + 3
    g = [[rnd.uniform(-1, 1) for _ in range(gw)] for _ in range(gh)]

    def at(x, y):
        fx, fy = x / cell, y / cell
        ix, iy = int(fx), int(fy)
        tx, ty = fx - ix, fy - iy
        a, b = g[iy][ix], g[iy][ix + 1]
        c, d = g[iy + 1][ix], g[iy + 1][ix + 1]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    return at


def embed(tree):
    W, H = tree["W"], tree["H"]
    rnd = __import__("random").Random(tree.get("seed", 0) * 9973 + 17)
    pos = organic_layout(tree, rnd)
    zone = grow_zones(tree, pos, rnd)

    # border tiles (4-neighbour zone differs) and the zone pairs they separate
    border = [[False] * W for _ in range(H)]
    pair_tiles = collections.defaultdict(list)
    for y in range(H):
        for x in range(W):
            for dx, dy in NB4:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and zone[ny][nx] != zone[y][x]:
                    border[y][x] = True
                    a, b = sorted((zone[y][x], zone[ny][nx]))
                    pair_tiles[(a, b)].append((x, y))

    # which zone pairs are PASSABLE land gates (guard tree-edges)
    guard_pairs = {}
    portal_pairs = []
    for e in tree["edges"]:
        a, b = sorted((e["a"], e["b"]))
        if e["type"] == "guard":
            guard_pairs[(a, b)] = e
        else:
            portal_pairs.append(e)

    # barrier = every border tile, minus the chokepoint we open per guard edge
    barrier = [[border[y][x] for x in range(W)] for y in range(H)]
    chokepoints = []
    for (a, b), e in guard_pairs.items():
        tiles = pair_tiles.get((a, b))
        if not tiles:
            continue  # zones not physically adjacent (rare); edge unrealizable here
        # open the border tile nearest the midpoint of the two seeds
        sa = pos[a]
        sb = pos[b]
        mx, my = (sa[0] + sb[0]) / 2, (sa[1] + sb[1]) / 2
        cx, cy = min(tiles, key=lambda t: (t[0] - mx) ** 2 + (t[1] - my) ** 2)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H and zone[y][x] in (a, b):
                    barrier[y][x] = False  # carve a small opening
        chokepoints.append({"x": cx, "y": cy, "a": a, "b": b, "strength": e["strength"]})

    # portals: one monolith per endpoint zone, placed at that zone's seed
    portals = []
    for i, e in enumerate(portal_pairs):
        for nid in (e["a"], e["b"]):
            px, py = int(pos[nid][0]), int(pos[nid][1])
            portals.append({"x": px, "y": py, "zone": nid, "pair": i})

    return {
        "W": W,
        "H": H,
        "zone": zone,
        "barrier": barrier,
        "chokepoints": chokepoints,
        "portals": portals,
        "pos": pos,
        "n_zones": len(tree["nodes"]),
    }


def connectivity(tree, em):
    """Verify every zone is reachable from the root through chokepoints+portals."""
    adj = collections.defaultdict(set)
    for c in em["chokepoints"]:
        adj[c["a"]].add(c["b"])
        adj[c["b"]].add(c["a"])
    bypair = collections.defaultdict(list)
    for p in em["portals"]:
        bypair[p["pair"]].append(p["zone"])
    for pr, zs in bypair.items():
        for i in range(len(zs) - 1):
            adj[zs[i]].add(zs[i + 1])
            adj[zs[i + 1]].add(zs[i])
    seen = {0}
    q = collections.deque([0])
    while q:
        z = q.popleft()
        for nb in adj[z]:
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    return len(seen), em["n_zones"]


def ascii_preview(tree, em, cell=1):
    W, H = em["W"], em["H"]
    zone, barrier = em["zone"], em["barrier"]
    choke = {(c["x"], c["y"]) for c in em["chokepoints"]}
    portal = {(p["x"], p["y"]) for p in em["portals"]}
    seedpos = {(int(x), int(y)): nid for nid, (x, y) in em["pos"].items()}
    glyph = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lines = []
    for y in range(0, H, cell):
        row = []
        for x in range(0, W, cell):
            if (x, y) in choke:
                row.append("+")
            elif (x, y) in portal:
                row.append("O")
            elif (x, y) in seedpos:
                row.append("@")
            elif barrier[y][x]:
                row.append("#")
            else:
                row.append(glyph[zone[y][x] % len(glyph)])
        lines.append("".join(row))
    return "\n".join(lines)


if __name__ == "__main__":
    W = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    H = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    tree = deps_gen.generate_tree(W, H, seed=seed)
    em = embed(tree)
    reach, total = connectivity(tree, em)
    deps_gen.describe(tree)
    print(
        f"embed: {em['n_zones']} zones  chokepoints={len(em['chokepoints'])}  "
        f"portals={len(em['portals'])}  reachable {reach}/{total}"
    )
    print(ascii_preview(tree, em))
