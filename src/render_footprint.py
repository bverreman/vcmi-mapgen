"""Footprint-FAITHFUL renderer -- the honest view.

Our schematic renderer (render.py) draws every object as a single dot and draws
decoration as a 1px black speck, so it CANNOT show what the VCMI editor shows:
real sprites occupy multi-tile footprints, and ~1000 decorations cover real ground.
This renderer fills each object's actual mask footprint so coverage / clustering /
overlap are visible -- the same reality the editor renders.

Real maps: re-parse the .h3m to recover each object's 6x8 block/visit mask.
Generated maps: read the .vmap; objects carry a VBA mask already.
Both are drawn identically so the comparison is apples-to-apples.
"""

import sys, os
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m
import vmapwrite
import vmaplib
import render

TILE = 8

# occupancy classes
BLOCK = 2   # impassable sprite tile (trunk / building / rock)
VISIT = 1   # visitable / passable-overlay sprite tile
EMPTY = 0


def real_cells(path):
    """[(l,x,y,kind)] sprite-occupied cells for every object in a real .h3m.
    H3 mask: 6 rows x 8 cols, anchor = bottom-right. block bit CLEAR => blocked;
    visit bit SET => visitable. Union of the two = the tiles the sprite covers."""
    m = h3m.parse_file(path)
    cells = []
    for o in m.objects:
        t = m.templates[o.template_index]
        for r in range(6):
            bb = t.block_mask[r]
            vb = t.visit_mask[r]
            for c in range(8):
                bit = 7 - c                     # bit7 = leftmost column
                blocked = not (bb & (1 << bit))  # clear bit => blocked
                visit = bool(vb & (1 << bit))
                if not (blocked or visit):
                    continue
                wx = o.x - (7 - c)
                wy = o.y - (5 - r)
                cells.append((o.l, wx, wy, BLOCK if blocked else VISIT))
    return cells, m.width, m.height, (2 if m.two_level else 1)


def gen_cells(path):
    """[(l,x,y,kind)] for a generated .vmap. mask rows, anchor bottom-right;
    B=blocking, A=visitable, V=passable-overlay (all are drawn sprite tiles)."""
    h, surf, under, objs = vmapwrite.read_raw(path)
    H, W = len(surf), len(surf[0])
    cells = []
    for o in objs:
        mask = o.get("template", {}).get("mask", []) or o.get("mask", [])
        if not mask:
            continue
        hh = len(mask)
        ww = max(len(r) for r in mask)
        for r, row in enumerate(mask):
            for c, ch in enumerate(row):
                if ch == " ":
                    continue
                wx = o["x"] - (ww - 1 - c)
                wy = o["y"] - (hh - 1 - r)
                kind = BLOCK if ch == "B" else VISIT
                cells.append((o.get("l", 0), wx, wy, kind))
    return cells, W, H


def terr_of_real(path):
    m = h3m.parse_file(path)
    return [[[t.terrain for t in row] for row in lvl] for lvl in m.terrain]


def draw(level_terr, cells, W, H, title=""):
    img = Image.new("RGB", (W * TILE, H * TILE + 16), (15, 15, 15))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = level_terr[y][x]
            r, g, b = render.TERRAIN_RGB.get(t, (0, 0, 0))
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy + 16] = (r, g, b)
    # overlay occupancy: track kind + how many sprite cells land on each tile
    kindmap = {}
    cnt = {}
    for (x, y, kind) in cells:
        if 0 <= x < W and 0 <= y < H:
            kindmap[(x, y)] = max(kindmap.get((x, y), 0), kind)
            cnt[(x, y)] = cnt.get((x, y), 0) + 1
    for (x, y), kind in kindmap.items():
        overlap = cnt[(x, y)] > 1
        if overlap:
            col = (255, 0, 0)             # RED = overlapping sprites (editor shows clipping)
        elif kind == BLOCK:
            col = (25, 25, 30)            # dark = impassable sprite tile
        else:
            col = (235, 225, 120)         # yellow-ish = visitable sprite tile
        for dy in range(TILE):
            for dx in range(TILE):
                if 1 <= dx < TILE - 1 and 1 <= dy < TILE - 1:
                    px[x * TILE + dx, y * TILE + dy + 16] = col
    d = ImageDraw.Draw(img)
    d.text((3, 3), title, fill=(255, 255, 255))
    return img


def main():
    real_h3m = os.path.expanduser("~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m")
    gen_vmap = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/data/workspace/vcmi-mapgen/out/DLGen-AllForOne-s0.vmap"
    out = "/mnt/data/workspace/vcmi-mapgen/out/render/footprint_compare.png"

    rc, rW, rH, rL = real_cells(real_h3m)
    rterr = terr_of_real(real_h3m)
    real_l0 = [(x, y, k) for (l, x, y, k) in rc if l == 0]
    rimg = draw(rterr[0], real_l0, rW, rH, "REAL All for One (editor footprints)")

    gc, gW, gH = gen_cells(gen_vmap)
    h, surf, under, objs = vmapwrite.read_raw(gen_vmap)
    gterr = [[vmaplib.TERR.get(c[:2], 2) for c in row] for row in surf]
    gen_l0 = [(x, y, k) for (l, x, y, k) in gc if l == 0]
    gimg = draw(gterr, gen_l0, gW, gH, "GENERATED s0 (editor footprints)")

    gap = 16
    cw = rimg.width + gimg.width + gap
    ch = max(rimg.height, gimg.height)
    canvas = Image.new("RGB", (cw, ch), (0, 0, 0))
    canvas.paste(rimg, (0, 0))
    canvas.paste(gimg, (rimg.width + gap, 0))
    canvas.save(out)

    # coverage stats
    def cov(cells, W, H):
        tiles = {(x, y) for (x, y, k) in cells if 0 <= x < W and 0 <= y < H}
        from collections import Counter
        cnt = Counter((x, y) for (x, y, k) in cells if 0 <= x < W and 0 <= y < H)
        over = sum(1 for v in cnt.values() if v > 1)
        return len(tiles), 100.0 * len(tiles) / (W * H), over
    rt, rp, ro = cov(real_l0, rW, rH)
    gt, gp, go = cov(gen_l0, gW, gH)
    print(f"REAL : {rt} occupied tiles ({rp:.1f}% of map), {ro} overlap tiles")
    print(f"GEN  : {gt} occupied tiles ({gp:.1f}% of map), {go} overlap tiles")
    print("wrote", out)


if __name__ == "__main__":
    main()
