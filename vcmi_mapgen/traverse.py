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

import os, json, collections

from vcmi_mapgen.kit.paths import project_root

ROOT = project_root()
OBJ = json.load(open(str(ROOT / "data" / "objlib.json")))
TYPE2PURPOSE = {it["type"]: p for p, terr in OBJ.items() for items in terr.values() for it in items}
WATER = 8
ROCK = 9
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def _mask_cells(x, y, mask):
    # anchor (x,y) = bottom-right tile; mask rows are stored left-to-right (sprite-aligned) so
    # col 0 is the leftmost tile -> tx = x - (ww-1-c). Matches obj_resolve.mask_cells.
    h = len(mask)
    for r, row in enumerate(mask):
        w = len(row)
        for c, ch in enumerate(row):
            yield (x - (w - 1 - c), y - (h - 1 - r), ch)


def _dims(fm):
    terr = fm["terrain"][0]
    return len(terr[0]), len(terr)


def passable_grid(fm, l=0):
    """A tile on level `l` is blocked when it is water/rock or covered by a blocking mask cell
    ('B' or 'X') of an object ON THAT LEVEL. Passable visitable 'A' cells (the tile a hero stands
    on) stay passable; 'X' (a building action tile) blocks and is visited from an adjacent tile.
    Rock (9) walls the underground."""
    terr = fm["terrain"][l]
    W, H = len(terr[0]), len(terr)
    blocked = [[terr[y][x]["t"] in (WATER, ROCK) for x in range(W)] for y in range(H)]
    for o in fm["objects"]:
        if o.get("l", 0) != l:
            continue
        for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]):
            if 0 <= cx < W and 0 <= cy < H and ch in ("B", "X"):
                blocked[cy][cx] = True
    return blocked, W, H


def _a_cells(o):
    # visitable anchors: 'A' (stand on) and 'X' (blocked building tile, visited from adjacent — its
    # own tile is blocked so `_approaches` will yield its passable neighbours, not the tile itself).
    return [(cx, cy) for cx, cy, ch in _mask_cells(o["x"], o["y"], o["mask"]) if ch in ("A", "X")]


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
    if start is None and towns:  # fallback: town nearest map centre
        start = min(towns, key=lambda o: (o["x"] - W // 2) ** 2 + (o["y"] - H // 2) ** 2)
    if start is None:
        return set(), None
    return _approaches(start, blocked, W, H), start


def _gate_links(fm, grids):
    """Teleport networks. Subterranean gates come in pairs sharing (x, y) across levels;
    two-way monoliths network ALL ends of the same subtype (H3 semantics — used by
    steps.repair.geometry to rescue otherwise-unreachable zones as guarded reward zones).
    Stepping onto any end
    teleports the hero to the others. Returns trigger map: reaching any (x,y,l) approach
    tile of an end enqueues every partner end's approach tiles (x,y,l')."""
    by_key = collections.defaultdict(list)
    for o in fm["objects"]:
        if o["type"] == "subterraneanGate":
            by_key[("sg", o["x"], o["y"])].append(o)
        elif o["type"] == "monolithTwoWay":
            by_key[("m2", o.get("subtype"))].append(o)
    trigger = collections.defaultdict(set)
    for ends in by_key.values():
        appr = []
        for o in ends:
            l = o.get("l", 0)
            if l not in grids:
                continue
            blocked, W, H = grids[l]
            appr.append({(x, y, l) for x, y in _approaches(o, blocked, W, H)})
        for i in range(len(appr)):
            for j in range(len(appr)):
                if i != j:
                    for t in appr[i]:
                        trigger[t] |= appr[j]
    return trigger


def traverse(fm, em=None):
    """Return a reachability report for the realized (possibly two-level) map.
    BFS walks passable land from the start town, descending/ascending through
    subterranean-gate pairs, so cavern objects are reachable only if the surface
    gate is reachable and the cavern is connected to it."""
    grids = {l: passable_grid(fm, l) for l in range(len(fm["terrain"]))}
    blocked, W, H = grids[0]
    seed, start = _start_seed(fm, blocked, W, H)
    trigger = _gate_links(fm, grids)

    reached = set((x, y, 0) for x, y in seed)
    q = collections.deque(reached)
    while q:
        x, y, l = q.popleft()
        for s in trigger.get((x, y, l), ()):  # gate teleport across levels
            if s not in reached:
                reached.add(s)
                q.append(s)
        bl, lw, lh = grids[l]
        for dx, dy in NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < lw and 0 <= ny < lh and not bl[ny][nx] and (nx, ny, l) not in reached:
                reached.add((nx, ny, l))
                q.append((nx, ny, l))

    def obj_reachable(o):
        l = o.get("l", 0)
        for ax, ay in _a_cells(o):
            if (ax, ay, l) in reached:
                return True
            for dx, dy in NB4:
                if (ax + dx, ay + dy, l) in reached:
                    return True
        return False

    bad_towns, bad_mines = [], []
    for o in fm["objects"]:
        pp = TYPE2PURPOSE.get(o["type"])
        if pp == "TOWN" and not obj_reachable(o):
            bad_towns.append((o["x"], o["y"], o.get("l", 0)))
        elif pp == "MINE" and not obj_reachable(o):
            bad_mines.append((o["x"], o["y"], o.get("l", 0)))

    zones_reached = zones_total = None
    bad_zones = []
    if em is not None:
        zone = em["zone"]
        total = em["n_zones"]
        seen_z = set(zone[y][x] for (x, y, l) in reached if l == 0)
        zones_reached, zones_total = len(seen_z), total
        bad_zones = sorted(set(range(total)) - seen_z)

    n_passable = sum(not blocked[y][x] for y in range(H) for x in range(W))
    cavern_reached = sum(1 for (x, y, l) in reached if l == 1) if len(grids) > 1 else None
    ok = start is not None and not bad_towns and not bad_mines and not bad_zones
    return {
        "ok": ok,
        "start": (start["x"], start["y"]) if start else None,
        "levels": len(grids),
        "reached_tiles": len(reached),
        "passable_tiles": n_passable,
        "cavern_reached_tiles": cavern_reached,
        "zones_reached": zones_reached,
        "zones_total": zones_total,
        "bad_zones": bad_zones,
        "unreachable_towns": bad_towns,
        "unreachable_mines": bad_mines,
    }


# traverse() is imported as a library by faithful.to_vmap (visitableFrom wiring).
# The former __main__ self-test depended on the removed deps_realize experiment.
