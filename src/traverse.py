"""Global traversability gate (tile-level).

The abstract `deps_embed.connectivity` proves the zone GRAPH is connected through
gates; `deps_realize.reachability_repair` only guarantees each object has *some*
walkable approach. Neither proves a hero can actually WALK from the starting town
to every zone, town and mine on the realized grid -- a chokepoint can be carved
but walled off by mountains beyond it, or a reward pocket can be sealed by water.

This module BFS-walks passable land (through carved chokepoints) from the start
town and asserts every zone, every town and every mine is reachable. Wired into
`ralph/verify.sh`, an unreachable map FAILS the gate.
"""
import sys, os, json, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OBJ = json.load(open(f"{ROOT}/out/objlib.json"))
TYPE2PURPOSE = {it["type"]: p for p, terr in OBJ.items() for items in terr.values() for it in items}
WATER = 8
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def _mask_cells(x, y, mask):
    h = len(mask); w = max(len(r) for r in mask)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            yield (x - (w - 1 - c), y - (h - 1 - r), ch)


def _dims(fm):
    terr = fm["terrain"][0]
    return len(terr[0]), len(terr)


def passable_grid(fm):
    """A tile is blocked when it is water or covered by a 'B' (blocking) mask cell.
    Visitable 'A' cells (town approaches, monster tiles) stay passable."""
    W, H = _dims(fm)
    terr = fm["terrain"][0]
    blocked = [[terr[y][x]["t"] == WATER for x in range(W)] for y in range(H)]
    for o in fm["objects"]:
        for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]):
            if 0 <= cx < W and 0 <= cy < H and ch == "B":
                blocked[cy][cx] = True
    return blocked, W, H


def _a_cells(o):
    return [(cx, cy) for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]) if ch == "A"]


def _approaches(o, blocked, W, H):
    """Passable tiles from which object o can be entered (4-adjacent to an 'A'
    cell, plus the 'A' cell itself if it is walkable -- monsters block nothing)."""
    res = set()
    for ax, ay in _a_cells(o):
        if 0 <= ax < W and 0 <= ay < H and not blocked[ay][ax]:
            res.add((ax, ay))
        for dx, dy in NB4:
            nx, ny = ax + dx, ay + dy
            if 0 <= nx < W and 0 <= ny < H and not blocked[ny][nx]:
                res.add((nx, ny))
    return res


def _start_seed(fm, blocked, W, H):
    """Passable tiles next to the player's starting town. main_town is stored at
    (anchor-2, anchor-2); the town object's anchor is therefore main_town+(2,2)."""
    mt = fm.get("main_town")
    towns = [o for o in fm["objects"] if TYPE2PURPOSE.get(o["type"]) == "TOWN"]
    start = None
    if mt is not None:
        ax, ay = mt["x"] + 2, mt["y"] + 2
        start = min(towns, key=lambda o: (o["x"] - ax) ** 2 + (o["y"] - ay) ** 2, default=None)
    if start is None and towns:                     # fallback: town nearest map centre
        start = min(towns, key=lambda o: (o["x"] - W // 2) ** 2 + (o["y"] - H // 2) ** 2)
    if start is None:
        return set(), None
    return _approaches(start, blocked, W, H), start


def traverse(fm, em=None):
    """Return a reachability report for the realized map."""
    blocked, W, H = passable_grid(fm)
    seed, start = _start_seed(fm, blocked, W, H)
    reached = set(seed)
    q = collections.deque(seed)
    while q:
        x, y = q.popleft()
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and not blocked[ny][nx] and (nx, ny) not in reached:
                reached.add((nx, ny)); q.append((nx, ny))

    def obj_reachable(o):
        for ax, ay in _a_cells(o):
            if (ax, ay) in reached:
                return True
            for dx, dy in NB4:
                if (ax + dx, ay + dy) in reached:
                    return True
        return False

    bad_towns, bad_mines = [], []
    for o in fm["objects"]:
        pp = TYPE2PURPOSE.get(o["type"])
        if pp == "TOWN" and not obj_reachable(o):
            bad_towns.append((o["x"], o["y"]))
        elif pp == "MINE" and not obj_reachable(o):
            bad_mines.append((o["x"], o["y"]))

    zones_reached = zones_total = None
    bad_zones = []
    if em is not None:
        zone = em["zone"]; total = em["n_zones"]
        seen_z = set()
        for (x, y) in reached:
            seen_z.add(zone[y][x])
        zones_reached, zones_total = len(seen_z), total
        bad_zones = sorted(set(range(total)) - seen_z)

    n_passable = sum(not blocked[y][x] for y in range(H) for x in range(W))
    ok = (start is not None and not bad_towns and not bad_mines and not bad_zones)
    return {"ok": ok, "start": (start["x"], start["y"]) if start else None,
            "reached_tiles": len(reached), "passable_tiles": n_passable,
            "zones_reached": zones_reached, "zones_total": zones_total,
            "bad_zones": bad_zones, "unreachable_towns": bad_towns,
            "unreachable_mines": bad_mines}


if __name__ == "__main__":
    import deps_realize as R
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    fm, tree, em = R.realize(seed=seed)
    r = traverse(fm, em)
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
