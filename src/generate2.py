"""Direction 2 - micro generation: organic biome terrain + human-density decoration.

Takes a real RMG vmap as base (keeps its gameplay objects + header so it opens),
swaps in generated organic terrain and replaces decoration at HUMAN density drawn
from terrain-appropriate pools. Isolates the micro layer for a clean A/B.
"""

import json, glob, random, math, os, sys, collections

sys.path.insert(0, "src")
import vmaplib, vmapwrite, render

ROOT = "/mnt/data/workspace/vcmi-mapgen"
PAL = json.load(open(f"{ROOT}/out/palette.json"))
INT2CODE = {
    0: "dt",
    1: "sa",
    2: "gr",
    3: "sn",
    4: "sw",
    5: "rg",
    6: "sb",
    7: "lv",
    8: "wt",
    9: "rc",
}
HD = {int(k): v for k, v in PAL["human_decor_density"].items()}


def value_noise(w, h, cell, rnd):
    """smooth low-freq noise in [0,1] via bilinear upsample of a coarse random grid."""
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


def gen_terrain(w, h, palette, weights, rnd, nseeds):
    """noise-perturbed Voronoi -> organic biome blobs of terrain ints."""
    types = list(palette)
    wts = [weights[t] for t in types]
    seeds = [
        (rnd.randrange(w), rnd.randrange(h), rnd.choices(types, wts)[0]) for _ in range(nseeds)
    ]
    nz = value_noise(w, h, max(6, w // 12), rnd)
    grid = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            best = 1e18
            bt = types[0]
            for sx, sy, st in seeds:
                d = (x - sx) ** 2 + (y - sy) ** 2
                d *= 0.6 + 1.2 * nz[y][x]  # perturb distance field -> wobbly borders
                if d < best:
                    best = d
                    bt = st
            grid[y][x] = bt
    return grid


def to_tilestrings(grid):
    return [[PAL["fill"].get(INT2CODE[t], "gr52_") for t in row] for row in grid]


def mask_blocked(tpl, x, y):
    """yield occupied (tx,ty) for an object anchored bottom-right at (x,y)."""
    m = tpl.get("mask") or ["B"]
    H = len(m)
    W = max(len(r) for r in m)
    for r in range(H):
        for c in range(len(m[r])):
            if m[r][c] in ("B",):
                yield x - (W - 1 - c), y - (H - 1 - r)
    yield x, y


def scatter_decor(grid, level, occ, counter, scale=1.0):
    h = len(grid)
    w = len(grid[0])
    objs = []
    for y in range(h):
        for x in range(w):
            t = grid[y][x]
            code = INT2CODE[t]
            pool = PAL["decor"].get(code) or []
            if not pool:
                continue
            dens = HD.get(t, 0) / 1000.0 * scale
            if rnd.random() >= dens:
                continue
            tpl = rnd.choice(pool)
            cells = list(mask_blocked(tpl.get("template", {}), x, y))
            if any((cx, cy) in occ or not (0 <= cx < w and 0 <= cy < h) for cx, cy in cells):
                continue
            for cc in cells:
                occ.add(cc)
            counter[0] += 1
            objs.append(
                {
                    "instanceName": f"gen{counter[0]}",
                    "l": level,
                    "subtype": tpl.get("subtype", "object"),
                    "type": tpl.get("type"),
                    "template": tpl.get("template", {}),
                    "x": x,
                    "y": y,
                }
            )
    return objs


LAND = {
    0: 0.12,
    1: 0.10,
    2: 0.42,
    3: 0.10,
    4: 0.08,
    5: 0.10,
    7: 0.05,
}  # surface biome weights
UNDER = {6: 0.55, 9: 0.30, 2: 0.08, 0: 0.07}  # underground: subterranean+rock dominant

if __name__ == "__main__":
    rnd = random.Random(7)
    base = glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0]
    header, surf, under, objs_raw = vmapwrite.read_raw(base)
    twolvl = under is not None
    H = len(surf)
    W = len(surf[0])
    # generate terrain per level
    g_surf = gen_terrain(W, H, LAND, LAND, rnd, nseeds=max(10, W * H // 1400))
    levels_int = [g_surf]
    if twolvl:
        g_under = gen_terrain(W, H, UNDER, UNDER, rnd, nseeds=max(8, W * H // 1800))
        levels_int.append(g_under)
    terrain_strs = [to_tilestrings(g) for g in levels_int]
    # MICRO layer ONLY: terrain + decoration. No visitable/gameplay objects
    # (sensible gameplay placement is the macro problem, Direction 3).
    occ = [set() for _ in levels_int]
    counter = [0]
    gen_objs = []
    for li, g in enumerate(levels_int):
        gen_objs += scatter_decor(g, li, occ[li], counter)
    all_objs = gen_objs
    # neutralize header: drop main-town references so a town-less map still opens
    for pid, pl in list(header.get("players", {}).items()):
        if isinstance(pl, dict):
            pl["mainTown"] = None
    out = f"{ROOT}/out/gen2_micro.vmap"
    vmapwrite.write_vmap(out, header, terrain_strs, all_objs, name="gen2 micro")
    print(f"base {os.path.basename(base)}  {W}x{H} 2lvl={twolvl}")
    print(f"decoration objects placed={len(gen_objs)}  (terrain+decoration only, 0 visitable)")
    # render
    norm = vmaplib.load(out)
    render.render_map(norm, f"{ROOT}/out/render/gen2_micro.png")
    print("wrote", out, "and render gen2_micro.png")
