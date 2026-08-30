"""Generate a map and render two PNGs: plain map and map with overlays.

Overlays (second PNG):
  - Zone fills: each reachable zone gets a distinct semi-transparent color
    with white borders and a centroid label (zone-id / terrain / area).
  - Grey: blocking footprint cells of background objects (vegetation, mountains,
    rocks — objects with no purpose or purpose==MINE_SEAL).
  - Green: blocking ('B') cells of visitable structures (impassable footprint,
    excluding the visit tile).
  - Dark green: the visit tile ('A'/'X') of each fixed visitable structure that
    has at least one blocking body cell.
  - Christmas green: the single visit tile of structures with NO blocking body
    (pure single-tile visitable objects like shrines, signs, events). These can
    sit inside pockets but are never pocket entrance tiles.
  - Blue: passage tiles — passable tiles that border a passable tile in a
    different zone; the actual walkable seam between zones.
  - Magenta gradient: all passable tiles a hero cannot reach without fighting
    a placed guard (bounded 8-connected component behind the guard's 3×3 ZoC).
    Colour darkens toward the ZoC entrance and lightens at the deepest tile.
  - Red: guard zone of control — the 3×3 area (A cell + 8 neighbours) of every
    placed guard monster. A hero entering any of these 9 tiles triggers combat,
    so the red region shows exactly what each guard seals off.

Usage:
    uv run python -m vcmi_mapgen.render_zone_overlay [--seed N] [--size N]
                                                     [--water-mode none|normal|islands]
                                                     [--players N]
"""
import argparse
import collections
import colorsys
import json
import os
from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import pp_map
from vcmi_mapgen import render_editor as RED
from vcmi_mapgen import terrain_segment as TS
from vcmi_mapgen.vcmi_paths import project_root
from PIL import Image, ImageDraw

TILE = 32
WATER, ROCK = 8, 9
ROOT = project_root()

TNAME = {0: "dirt", 1: "sand", 2: "grass", 3: "snow", 4: "swamp",
         5: "rough", 6: "subterr", 7: "lava", 8: "water", 9: "rock"}

_STRUCTURE_PURPOSES = frozenset({
    "TOWN", "MINE", "DWELLING",
    "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL",
    "BANK", "INFO", "MANA", "QUEST_GATE",
})

_NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
_POCKET_MIN = 2
_POCKET_MAX = 16


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def _palette(n):
    return [
        tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / max(n, 1), 0.70, 0.95)) + (75,)
        for i in range(n)
    ]


def _fill_layer(img_size, tiles, color):
    layer = Image.new("RGBA", img_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for x, y in tiles:
        draw.rectangle(
            [x * TILE, y * TILE, (x + 1) * TILE - 1, (y + 1) * TILE - 1],
            fill=color,
        )
    return layer


# ---------------------------------------------------------------------------
# object classification
# ---------------------------------------------------------------------------

def _classify_objects(objs0):
    """Return (background, struct_body, struct_visit, solo_visit) tile sets.

    solo_visit: structures with NO blocking body cells and exactly ONE visit tile.
    These are pure single-tile visitable objects (shrines, events, signs).  They
    get a distinct overlay and are handled separately in pocket detection.
    """
    background   = set()
    struct_body  = set()
    struct_visit = set()
    solo_visit   = set()

    for o in objs0:
        purpose = o.get("purpose") or ""
        mask, ox, oy = o["mask"], o["x"], o["y"]

        if purpose in _STRUCTURE_PURPOSES:
            visit = set(OR.mask_interactive_cells(mask, ox, oy))
            body = {(cx, cy) for cx, cy, blk in OR.mask_cells(mask, ox, oy)
                    if blk and (cx, cy) not in visit}
            if not body and len(visit) == 1:
                solo_visit |= visit
            else:
                struct_visit |= visit
                struct_body  |= body
        elif purpose == "WATER_TRANSPORT":
            # Seaport: dark green for the X (visit) cell, green for blocking B cells
            visit = set(OR.mask_interactive_cells(mask, ox, oy))
            body = {(cx, cy) for cx, cy, blk in OR.mask_cells(mask, ox, oy)
                    if blk and (cx, cy) not in visit}
            struct_visit |= visit
            struct_body  |= body
        elif not purpose or purpose == "DECORATION":
            for cx, cy, blk in OR.mask_cells(mask, ox, oy):
                if blk:
                    background.add((cx, cy))

    background -= struct_body | struct_visit | solo_visit
    return background, struct_body, struct_visit, solo_visit


# ---------------------------------------------------------------------------
# passable tiles
# ---------------------------------------------------------------------------

def _compute_passable(levels0, objs0):
    H, W = len(levels0), len(levels0[0])
    land = {
        (x, y)
        for y in range(H)
        for x in range(W)
        if levels0[y][x]["t"] not in (WATER, ROCK)
    }
    blocked = {
        (cx, cy)
        for o in objs0
        for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"])
        if blk
    }
    return land - blocked


# ---------------------------------------------------------------------------
# pocket detection  (new definition)
# ---------------------------------------------------------------------------

def _find_pockets(passable, no_entrance=frozenset()):
    """Find pockets matching the user definition:
      - 8-connected set of _POCKET_MIN–_POCKET_MAX passable tiles
      - unique entrance: exactly 1 passable tile, or exactly 2 8-connected
        passable tiles, whose removal disconnects the pocket from the rest.

    no_entrance: tiles that may appear inside pockets but must not be entrance
    candidates (e.g. solo-visit structures — they can sit in a pocket but the
    pocket entrance must not go through them).

    Only wall-adjacent passable tiles are tried as entrance candidates: a hero
    must stand next to a blocking/dark tile to interact with it, so a pocket
    entrance always borders at least one non-passable tile.

    Returns list of (pocket: frozenset, entrance: frozenset).
    When both a 1-tile and 2-tile entrance describe the same pocket, the
    smaller entrance is kept.
    """
    best = {}  # pocket_frozenset -> frozenset(entrance)

    # A passable tile is wall-adjacent when at least one 8-neighbour is not
    # passable (blocking object cell, impassable terrain, or map edge).
    wall_adjacent = {
        t for t in passable
        if any((t[0] + dx, t[1] + dy) not in passable for dx, dy in _NB8)
    }
    entrance_candidates = wall_adjacent - no_entrance

    def _try_entrance(entrance):
        remaining = passable - entrance
        seeds = {
            (gx + dx, gy + dy)
            for gx, gy in entrance
            for dx, dy in _NB8
            if (gx + dx, gy + dy) in remaining
        }
        seen = set()
        for seed in sorted(seeds):
            if seed in seen:
                continue
            # bounded 8-BFS within remaining
            comp = {seed}
            q = collections.deque([seed])
            too_big = False
            while q and not too_big:
                cx, cy = q.popleft()
                for dx, dy in _NB8:
                    nb = (cx + dx, cy + dy)
                    if nb in remaining and nb not in comp:
                        comp.add(nb)
                        if len(comp) > _POCKET_MAX:
                            too_big = True
                            break
                        q.append(nb)
            if too_big:
                continue
            seen |= comp
            if len(comp) < _POCKET_MIN:
                continue
            # unique entrance: no 8-neighbor of any pocket tile leads outside
            has_exit = any(
                (t[0] + dx, t[1] + dy) in passable
                and (t[0] + dx, t[1] + dy) not in comp
                and (t[0] + dx, t[1] + dy) not in entrance
                for t in comp
                for dx, dy in _NB8
            )
            if has_exit:
                continue
            key = frozenset(comp)
            if key not in best or len(entrance) < len(best[key]):
                best[key] = frozenset(entrance)

    # 1-tile entrances (no_entrance tiles are never candidates)
    for g in sorted(entrance_candidates):
        _try_entrance(frozenset({g}))

    # 2-tile entrances (each 8-connected pair, tried once)
    for g1 in sorted(entrance_candidates):
        g1x, g1y = g1
        for dx, dy in _NB8:
            g2 = (g1x + dx, g1y + dy)
            if g2 in entrance_candidates and g2 > g1:
                _try_entrance(frozenset({g1, g2}))

    # Post-filter: reject pockets that are 8-adjacent to (or share tiles with)
    # a larger already-accepted pocket.  Two detected pockets can be 8-adjacent
    # when one pocket's body tile is another's entrance tile, creating an
    # apparent neighbourhood across the pair.  We accept greedily, largest
    # pocket first; on equal size, lex order of the min tile breaks ties.
    #
    # Check: the *candidate's* footprint (its body tiles + their 8-neighbours)
    # must be disjoint from the *body tiles* of already-accepted pockets.
    # Using only body tiles on the accepted side avoids false conflicts through
    # a shared entrance tile.
    candidates = sorted(
        best.items(),
        key=lambda kv: (-len(kv[0]), min(kv[0])),
    )
    accepted: list[tuple] = []
    accepted_tiles: set = set()  # union of body tiles of accepted pockets only
    for pocket, entrance in candidates:
        footprint = pocket | {
            (x + dx, y + dy)
            for x, y in pocket
            for dx, dy in _NB8
        }
        if footprint.isdisjoint(accepted_tiles):
            accepted.append((pocket, entrance))
            accepted_tiles |= pocket   # body tiles only, NOT 8-neighbours
    return accepted


def _pocket_distances(pocket, entrance):
    """BFS distance from entrance into pocket (8-connected). Returns {tile: int}."""
    dist = {}
    q = collections.deque()
    for gx, gy in entrance:
        for dx, dy in _NB8:
            nb = (gx + dx, gy + dy)
            if nb in pocket and nb not in dist:
                dist[nb] = 0
                q.append(nb)
    while q:
        t = q.popleft()
        tx, ty = t
        for dx, dy in _NB8:
            nb = (tx + dx, ty + dy)
            if nb in pocket and nb not in dist:
                dist[nb] = dist[t] + 1
                q.append(nb)
    return dist


def _magenta_at(t):
    """t=0 (entrance, darkest) → t=1 (deepest, lightest). Returns RGBA.
    Alpha is kept low enough that the sprites placed inside pockets show through."""
    v = 0.30 + 0.70 * t
    r, g, b = colorsys.hsv_to_rgb(300 / 360, 0.90, v)
    return (int(r * 255), int(g * 255), int(b * 255), 130)


def _select_mouth(pocket, entrance, passable):
    """Pick the single entrance tile that best guards the pocket.

    Primary criterion: maximise average Chebyshev distance from the pocket
    body (the 'outermost' entrance tile — furthest from the interior, so the
    guard stands at the outer door rather than half-inside the nook).
    Tiebreak: most passable 8-neighbours (most open approach angles).
    For a 1-tile entrance the choice is trivial.
    """
    def _score(t):
        avg_d = (sum(max(abs(t[0] - p[0]), abs(t[1] - p[1])) for p in pocket) / len(pocket)
                 if pocket else 0.0)
        nb_open = sum(1 for dx, dy in _NB8 if (t[0] + dx, t[1] + dy) in passable)
        return (avg_d, nb_open)
    return max(entrance, key=_score)


def _pocket_gradient_layer(img_size, passable, objs0, W, H, no_entrance=frozenset()):
    """RGBA layer: magenta gradient over tiles physically sealed by placed guards.

    For each placed guard's 3×3 ZoC, finds all passable tiles a hero cannot
    reach without triggering combat (bounded 8-connected BFS in passable-ZoC).
    Sealed regions from different guards are merged when they overlap.  The
    gradient darkens near the ZoC entrance and lightens toward the deepest tile.

    `no_entrance` kept for API compatibility; unused in guard-centric approach."""
    _MAX_SEALED = 14  # BFS cap per component; pocket grammar max size

    # Per guard: find bounded passable region behind its ZoC
    regions = []
    for o in objs0:
        if o.get("purpose") != "GUARD":
            continue
        for ax, ay in OR.mask_interactive_cells(o["mask"], o["x"], o["y"]):
            zoc = frozenset(
                (ax + dx, ay + dy)
                for dx in range(-1, 2) for dy in range(-1, 2)
                if 0 <= ax + dx < W and 0 <= ay + dy < H
            )
            passable_no_zoc = passable - zoc
            sealed = set()
            seen = set()
            for tx, ty in sorted(zoc):
                for dx, dy in _NB8:
                    nb = (tx + dx, ty + dy)
                    if nb in zoc or nb in seen or nb not in passable_no_zoc:
                        continue
                    comp = {nb}
                    q = collections.deque([nb])
                    leaked = False
                    while q and not leaked:
                        cx, cy = q.popleft()
                        for ddx, ddy in _NB8:
                            n2 = (cx + ddx, cy + ddy)
                            if n2 in zoc or n2 in comp:
                                continue
                            if n2 not in passable_no_zoc:
                                continue
                            comp.add(n2)
                            if len(comp) > _MAX_SEALED:
                                leaked = True
                                break
                            q.append(n2)
                    if not leaked:
                        seen |= comp
                        sealed |= comp
            if sealed:
                regions.append([set(zoc), sealed])

    # Merge regions that share sealed tiles (same nook guarded by multiple guards)
    merged = []
    used = [False] * len(regions)
    for i in range(len(regions)):
        if used[i]:
            continue
        m_zoc, m_sealed = set(regions[i][0]), set(regions[i][1])
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j in range(len(regions)):
                if used[j]:
                    continue
                if regions[j][1] & m_sealed:
                    m_zoc |= regions[j][0]
                    m_sealed |= regions[j][1]
                    used[j] = True
                    changed = True
        merged.append((frozenset(m_zoc), m_sealed))

    layer = Image.new("RGBA", img_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    mouths = []
    for zoc, pocket in merged:
        dists = _pocket_distances(pocket, zoc)
        max_d = max(dists.values()) if dists else 0
        for (x, y), d in dists.items():
            t = d / max_d if max_d > 0 else 0.0
            draw.rectangle(
                [x * TILE, y * TILE, (x + 1) * TILE - 1, (y + 1) * TILE - 1],
                fill=_magenta_at(t),
            )
        if zoc and pocket:
            cx_p = sum(x for x, _ in pocket) / len(pocket)
            cy_p = sum(y for _, y in pocket) / len(pocket)
            mouths.append(min(zoc, key=lambda t: (t[0] - cx_p) ** 2 + (t[1] - cy_p) ** 2))
    return layer, len(merged), mouths


# ---------------------------------------------------------------------------
# passage tiles
# ---------------------------------------------------------------------------

def _passage_tiles(zone_label, passable, H, W):
    passages = set()
    for x, y in passable:
        zid = zone_label[y][x]
        if zid < 0:
            continue
        for dx, dy in _NB8:
            nx, ny = x + dx, y + dy
            if (nx, ny) in passable and zone_label[ny][nx] != zid:
                passages.add((x, y))
                break
    return passages


_LOOT_ZONE_MAX_TILES = 80


def _loot_zone_tiles(zones, zone_label, objs0, H, W):
    """Identify all tiles belonging to loot zones (area ≤ 80, no town, single
    terrain-tile boundary cluster) using the same detection logic as pp_pickup."""
    zone_ts = collections.defaultdict(set)
    for y in range(H):
        for x in range(W):
            zid = zone_label[y][x]
            if zid >= 0:
                zone_ts[zid].add((x, y))

    town_tiles = set()
    for o in objs0:
        if o.get("purpose") == "TOWN":
            for cx, cy, _ in OR.mask_cells(o["mask"], o["x"], o["y"]):
                town_tiles.add((cx, cy))

    all_ts = set()
    for ts in zone_ts.values():
        all_ts |= ts

    loot_tiles = set()
    for zid, ts in zone_ts.items():
        if len(ts) > _LOOT_ZONE_MAX_TILES:
            continue
        if any(t in town_tiles for t in ts):
            continue
        ext_ts = all_ts - ts
        boundary = {t for t in ts
                    if any((t[0] + dx, t[1] + dy) in ext_ts for dx, dy in _NB8)}
        seen, n_clusters = set(), 0
        for s in sorted(boundary):
            if s in seen:
                continue
            n_clusters += 1
            if n_clusters > 1:
                break
            q = collections.deque([s])
            seen.add(s)
            while q:
                cx, cy = q.popleft()
                for dx, dy in _NB8:
                    nb = (cx + dx, cy + dy)
                    if nb in boundary and nb not in seen:
                        seen.add(nb)
                        q.append(nb)
        if n_clusters == 1:
            loot_tiles |= ts

    return loot_tiles


# ---------------------------------------------------------------------------
# zone overlay builder
# ---------------------------------------------------------------------------

def _zone_layers(base_size, levels0):
    """Returns (fill, border, draw_labels_fn, zones, zone_label)."""
    zones, zone_label = TS.segment(levels0, subdivide=False)
    H, W = len(levels0), len(levels0[0])

    zone_ids = sorted(zones)
    palette = _palette(len(zone_ids))
    color_map = {zid: palette[i] for i, zid in enumerate(zone_ids)}

    fill = Image.new("RGBA", base_size, (0, 0, 0, 0))
    fdraw = ImageDraw.Draw(fill)
    for y in range(H):
        for x in range(W):
            zid = zone_label[y][x]
            if zid >= 0:
                fdraw.rectangle(
                    [x * TILE, y * TILE, (x + 1) * TILE - 1, (y + 1) * TILE - 1],
                    fill=color_map[zid],
                )

    border = Image.new("RGBA", base_size, (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(border)
    for y in range(H):
        for x in range(W):
            zid = zone_label[y][x]
            if x + 1 < W and zone_label[y][x + 1] != zid:
                ex = (x + 1) * TILE
                bdraw.line([(ex, y * TILE), (ex, (y + 1) * TILE - 1)],
                           fill=(255, 255, 255, 200), width=2)
            if y + 1 < H and zone_label[y + 1][x] != zid:
                ey = (y + 1) * TILE
                bdraw.line([(x * TILE, ey), ((x + 1) * TILE - 1, ey)],
                           fill=(255, 255, 255, 200), width=2)

    def draw_labels(draw):
        for zid, z in zones.items():
            cx, cy = z["centroid"]
            tname = TNAME.get(z["terrain_type"], f"t{z['terrain_type']}")
            label = f"z{zid} {tname}\n{z['area']}t"
            px, py = int(cx) * TILE + 2, int(cy) * TILE + 2
            for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                draw.text((px + ox, py + oy), label, fill=(0, 0, 0, 255))
            draw.text((px, py), label, fill=(255, 255, 255, 255))

    return fill, border, draw_labels, zones, zone_label


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(seed=42, size=72, water_mode="normal", players=2):
    print(f"=== render_zone_overlay seed={seed} size={size} "
          f"water={water_mode} players={players} ===")

    out_dir = os.path.join(ROOT, "out", "render", "pp")
    os.makedirs(out_dir, exist_ok=True)

    _cache_path = os.path.join(out_dir, f"ppmap_s{seed}_cache.json")
    _plain_path = os.path.join(out_dir, f"ppmap_s{seed}.png")
    if os.path.exists(_cache_path) and os.path.exists(_plain_path):
        with open(_cache_path) as _cf:
            _cache = json.load(_cf)
        levels0 = _cache["level0"]
        objs0 = _cache["objs0"]
        base_img = Image.open(_plain_path).convert("RGB")
        plain_path = _plain_path
        print(f"  (overlay-only — loaded from cache, skipping map build)")
    else:
        levels, surfs, objs, info, _ = pp_map.build(
            seed=seed, size=size, water_mode=water_mode, players=players,
        )
        print(info)
        objs0 = [o for o in objs if o.get("l", 0) == 0]
        levels0 = levels[0]
        base_img = RED.render_map(surfs[0], objs0)
        plain_path = os.path.join(out_dir, f"ppmap_s{seed}_plain.png")
        base_img.save(plain_path)
        print(f"\n[1/2] plain   -> {plain_path}")

    # --- overlay ---
    zone_fill, zone_border, draw_labels, zones, zone_label = _zone_layers(base_img.size, levels0)

    background_tiles, struct_body_tiles, struct_visit_tiles, solo_visit_tiles = \
        _classify_objects(objs0)
    grey_layer     = _fill_layer(base_img.size, background_tiles,   (130, 130, 130, 160))
    green_layer    = _fill_layer(base_img.size, struct_body_tiles,  (  0, 200,  80, 160))
    dk_green_layer = _fill_layer(base_img.size, struct_visit_tiles, (  0, 120,  40, 220))
    xmas_layer     = _fill_layer(base_img.size, solo_visit_tiles,   (  0, 155,  70, 220))

    passable = _compute_passable(levels0, objs0)
    H, W = len(levels0), len(levels0[0])
    passages = _passage_tiles(zone_label, passable, H, W)
    blue_layer = _fill_layer(base_img.size, passages, (60, 140, 255, 200))

    # Exclude loot zone tiles from pocket detection: loot zones are a distinct
    # access mechanic (gate/monolith) and should never show a magenta overlay.
    loot_tiles = _loot_zone_tiles(zones, zone_label, objs0, H, W)

    # For pocket geometry, treat visit tiles of normal structures as walls
    # (they are passable but "owned" by the structure).  Solo-visit tiles (no
    # blocking body, one visit tile) may sit inside a pocket but must never
    # serve as the pocket entrance.
    passable_for_pockets = passable - struct_visit_tiles - loot_tiles
    magenta_layer, n_pockets, _mouth_tiles = _pocket_gradient_layer(
        base_img.size, passable_for_pockets, objs0, W, H,
    )
    # Red layer: guard (monster) zone of control — the 3×3 area centred on each
    # guard's interactive (A) cell.  In H3/VCMI a wandering monster attacks any
    # hero who steps onto the A tile OR any of its 8 neighbours, so the effective
    # block zone is 9 tiles, not the 4-tile sprite footprint.
    guard_tiles = [
        (ax + dx, ay + dy)
        for o in objs0
        if o.get("purpose") == "GUARD"
        for ax, ay in OR.mask_interactive_cells(o["mask"], o["x"], o["y"])
        for dx, dy in [(0, 0)] + _NB8
        if 0 <= ax + dx < W and 0 <= ay + dy < H
    ]
    mouth_layer = _fill_layer(base_img.size, guard_tiles, (220, 50, 50, 160))

    # composite: base → zones → borders → grey → green → dk-green → xmas-green → blue → pockets → mouths → labels
    result = Image.alpha_composite(base_img.convert("RGBA"), zone_fill)
    result = Image.alpha_composite(result, zone_border)
    result = Image.alpha_composite(result, grey_layer)
    result = Image.alpha_composite(result, green_layer)
    result = Image.alpha_composite(result, dk_green_layer)
    result = Image.alpha_composite(result, xmas_layer)
    result = Image.alpha_composite(result, blue_layer)
    result = Image.alpha_composite(result, magenta_layer)
    result = Image.alpha_composite(result, mouth_layer)
    draw_labels(ImageDraw.Draw(result))

    overlay_path = os.path.join(out_dir, f"ppmap_s{seed}_overlays.png")
    result.convert("RGB").save(overlay_path)
    print(f"[2/2] overlays -> {overlay_path}")

    print(f"\n   zones:                  {len(zones)}")
    for zid in sorted(zones):
        z = zones[zid]
        print(f"   z{zid:3d}  {TNAME.get(z['terrain_type'], '?'):8s}  {z['area']:5d}t")
    print(f"   background (grey)       tiles: {len(background_tiles)}")
    print(f"   struct body (green)     tiles: {len(struct_body_tiles)}")
    print(f"   visit tile (dk green)   tiles: {len(struct_visit_tiles)}")
    print(f"   solo visit (xmas green) tiles: {len(solo_visit_tiles)}")
    print(f"   passages (blue)         tiles: {len(passages)}")
    print(f"   pockets (magenta grad): {n_pockets}")
    n_guards = sum(1 for o in objs0 if o.get("purpose") == "GUARD")
    print(f"   guards (red):           {n_guards} monsters")

    return plain_path, overlay_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--size", type=int, default=72)
    ap.add_argument("--water-mode", choices=["none", "normal", "islands"],
                    default="normal", dest="water_mode")
    ap.add_argument("--players", type=int, default=2)
    args = ap.parse_args()
    main(seed=args.seed, size=args.size, water_mode=args.water_mode, players=args.players)
