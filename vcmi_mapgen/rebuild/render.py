"""Rendering for the identity-rebuild engine: the zone-tint segmentation PNG, the realistic
editor-sprite side-by-side (original vs rebuilt), and the reconstruct-panel montage."""
import os

from vcmi_mapgen.kit import terrain_segment as TS
from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen.kit import vmap_format as VF
from vcmi_mapgen.kit.render_palette import TERRAIN_RGB as _TERRAIN_RGB
from vcmi_mapgen.rebuild.engine import _bucket_objects, label_zone

ZONE_TINT = [(200, 80, 80), (80, 160, 200), (90, 200, 120), (210, 180, 70),
             (180, 110, 200), (220, 140, 80), (120, 200, 200), (200, 120, 160),
             (150, 170, 90), (110, 130, 220)]

TILE = 9  # px per tile for the schematic segmentation render


def render_segmentation(name: str, out_path: str):
    from PIL import Image, ImageDraw
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    imgs, tables = [], []
    for L, lvl in enumerate(fm["terrain"]):
        zones, zone_label = TS.segment(lvl)
        zone_objs, _ = _bucket_objects(fm["objects"], L, zone_label, zones, W, H)
        img = Image.new("RGB", (W * TILE, H * TILE), (10, 10, 10))
        px = img.load()
        for y in range(H):
            for x in range(W):
                base = _TERRAIN_RGB.get(lvl[y][x]["t"], (0, 0, 0))
                z = zone_label[y][x]
                col = (tuple((b + t) // 2 for b, t in zip(base, ZONE_TINT[z % len(ZONE_TINT)]))
                       if z >= 0 else base)
                for dy in range(TILE):
                    for dx in range(TILE):
                        px[x * TILE + dx, y * TILE + dy] = col
        d = ImageDraw.Draw(img)
        table = []
        for zid in sorted(zones):
            z = zones[zid]
            lab = label_zone(z, zone_objs[zid], W, H)
            table.append((zid, lab, z["area"], len(zone_objs[zid])))
            tx, ty = int(z["centroid"][0] * TILE), int(z["centroid"][1] * TILE)
            d.text((tx - 4, ty - 9), str(zid), fill=(255, 255, 255))
            d.text((tx - 18, ty + 1), lab, fill=(255, 255, 0))
        imgs.append(img)
        tables.append((L, table))
    gap = 12
    canvas = Image.new("RGB", (sum(i.width for i in imgs) + gap * (len(imgs) - 1),
                               max(i.height for i in imgs)), (20, 20, 20))
    x = 0
    for i in imgs:
        canvas.paste(i, (x, 0))
        x += i.width + gap
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return out_path, tables


def _paint_sort(objs):
    """Canonical paint order so overlapping sprites stack identically across renders.
    render_editor.render_map re-sorts stably by (l!=0, y, x), so this only fixes ties."""
    return sorted(objs, key=lambda o: (o["y"], o["x"], o.get("type", ""),
                                       o.get("subtype", ""),
                                       o.get("template", {}).get("animation", "")))


def editor_render(vmap_path: str, out_path: str, compare_vmap: str | None = None,
                  labels=("SOURCE (faithful)", "REBUILT")):
    """Realistic editor-sprite render. With compare_vmap, render both via the SAME
    read_vmap path (surface, object-identical => visually identical)."""
    from vcmi_mapgen import render_editor as RE
    from PIL import Image
    surf, objs = RE.read_vmap(vmap_path)
    gen = RE.render_map(surf, _paint_sort(objs), title=labels[1])
    if compare_vmap:
        ssurf, sobjs = RE.read_vmap(compare_vmap)
        ref = RE.render_map(ssurf, _paint_sort(sobjs), title=labels[0])
        gap = 8
        out = Image.new("RGB", (ref.width + gen.width + gap,
                                max(ref.height, gen.height)), (0, 0, 0))
        out.paste(ref, (0, 0))
        out.paste(gen, (ref.width + gap, 0))
    else:
        out = gen
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.save(out_path)
    return out_path


def _render_panel(pan, title=None):
    """Render ONE zone panel (cropped to the zone) with REAL H3 sprites at editor
    resolution (32px), only the zone's own tiles, transparent elsewhere. RGBA."""
    from vcmi_mapgen import render_editor as RE
    from PIL import Image, ImageDraw
    T = RE.TILE  # 32
    terr, tiles, W, H = pan["terr"], pan["tiles"], pan["W"], pan["H"]
    xs = [x for x, y in tiles]
    ys = [y for x, y in tiles]
    # pre-pass: largest sprite -> crop margin wide enough that bottom-right-anchored
    # sprites (extend up & left) aren't clipped.
    draw = []
    max_sw = max_sh = T
    for o in sorted(pan["objs"], key=lambda o: (_paint_layer(o), o["y"], o["x"])):
        anim = o.get("animation", "")
        groups = RE.get_def(anim) if anim else None
        if not groups or not groups[0]:
            continue
        sp = groups[0][0]
        draw.append((o["x"], o["y"], sp))
        max_sw, max_sh = max(max_sw, sp.size[0]), max(max_sh, sp.size[1])
    ml, mt = -(-max_sw // T), -(-max_sh // T)
    x0, x1 = max(min(xs) - ml, 0), min(max(xs) + 2, W)
    y0, y1 = max(min(ys) - mt, 0), min(max(ys) + 2, H)
    img = Image.new("RGBA", ((x1 - x0) * T, (y1 - y0) * T), (0, 0, 0, 0))
    for y in range(y0, y1):
        for x in range(x0, x1):
            if (x, y) in tiles:
                img.paste(RE.terr_tile_img(VF.tile_string(terr[y][x])),
                          ((x - x0) * T, (y - y0) * T))
    for (ox, oy, sp) in draw:
        img.paste(sp, ((ox - x0 + 1) * T - sp.size[0], (oy - y0 + 1) * T - sp.size[1]), sp)
    t = title if title is not None else pan.get("title")
    if t:
        ImageDraw.Draw(img).text((4, 4), t, fill=(255, 255, 255, 255))
    return img


def _compose_panels(imgs, out_path, gap=14):
    from PIL import Image
    Wt = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    Ht = max(i.height for i in imgs)
    canvas = Image.new("RGBA", (Wt, Ht), (0, 0, 0, 0))  # transparent background
    x = 0
    for i in imgs:
        canvas.paste(i, (x, 0), i)
        x += i.width + gap
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return out_path


def render_zone_compare(panels, out_path):
    """Side-by-side zone panels (real H3 sprites, zone-only, transparent elsewhere)."""
    return _compose_panels([_render_panel(p) for p in panels], out_path)


# ---------------------------------------------------------------------------
# Patch inspection — render every stored patch in ISOLATION (its own shape +
# objects, no placement/stretch) so the patch CONTENT can be eyeballed apart
# from how generation lays it down. Mirrors the library tree for traceability.
# ---------------------------------------------------------------------------

PATCH_BG = (28, 28, 32)


def _paint_layer(o):
    """Paint band so stacked objects (multiple per tile) don't hide each other: flat
    terrain overlays (cursed ground / magic plains / rocklands, AVX*) at the bottom,
    scenery decoration above, gameplay on top. Within a band, normal (y,x) back-to-front."""
    p = o.get("_purpose")
    if p == "TERRAIN_MODIFIER":
        return 0
    if p == "DECORATION":
        return 1
    return 2
