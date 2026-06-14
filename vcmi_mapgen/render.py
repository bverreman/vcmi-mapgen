"""Schematic map renderer -> PNG.

Renders a parsed map (our normalized JSON, or a generated map of the same shape)
as a colored terrain grid with gameplay objects overlaid as markers coloured by
PURPOSE. Decoration is drawn faintly so the gameplay skeleton stands out -- this is
a STRUCTURAL view for comparing 'does this look human', not an art-faithful render.
"""

import json, sys, os
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON

TILE = 9  # px per map tile

TERRAIN_RGB = {
    0: (120, 92, 56),  # dirt
    1: (214, 191, 130),  # sand
    2: (86, 140, 56),  # grass
    3: (225, 232, 238),  # snow
    4: (78, 108, 80),  # swamp
    5: (150, 124, 70),  # rough
    6: (92, 78, 104),  # subterranean
    7: (70, 60, 58),  # lava
    8: (54, 104, 168),  # water
    9: (64, 60, 64),  # rock
    10: (120, 150, 70),  # highlands (HotA)
    11: (150, 95, 70),  # wasteland (HotA)
}
# gameplay purpose -> marker colour
PURPOSE_RGB = {
    "TOWN": (255, 255, 255),
    "MINE": (255, 170, 0),
    "RESOURCE_PILE": (255, 215, 0),
    "REWARD_PICKUP": (255, 105, 180),
    "GUARD": (220, 20, 20),
    "DWELLING": (160, 32, 240),
    "STAT_PERMANENT": (0, 200, 255),
    "BONUS_TEMP": (0, 255, 170),
    "MANA": (80, 80, 255),
    "SPELL_SKILL": (140, 0, 200),
    "INFO": (200, 200, 200),
    "BANK": (255, 60, 60),
    "QUEST_GATE": (255, 140, 0),
    "TRANSPORT": (0, 255, 0),
    "WATER_TRANSPORT": (0, 255, 255),
    "TERRAIN_MODIFIER": (180, 180, 0),
    "HERO": (255, 0, 255),
    "SPECIAL": (150, 150, 150),
}
BIG = {"TOWN", "TRANSPORT", "MINE", "BANK", "DWELLING", "QUEST_GATE"}  # draw larger


def render_level(level_terrain, objects, w, h, title=""):
    img = Image.new("RGB", (w * TILE, h * TILE), (0, 0, 0))
    px = img.load()
    for y, row in enumerate(level_terrain):
        for x, cell in enumerate(row):
            t = cell["t"] if isinstance(cell, dict) else cell
            r, g, b = TERRAIN_RGB.get(t, (0, 0, 0))
            if isinstance(cell, dict) and cell.get("road"):
                r, g, b = min(r + 40, 255), min(g + 30, 255), b // 2
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy] = (r, g, b)
    d = ImageDraw.Draw(img)
    for o in objects:
        if o["l"] != level_terrain_level:
            continue
        if "purpose" in o:  # pre-resolved (e.g. from vmap)
            info = {"purpose": o["purpose"], "name": o.get("name", "")}
        else:
            info = ON.resolve(o["class"], o["subclass"])
        x, y = o["x"], o["y"]
        cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2
        if info["purpose"] == "DECORATION":
            d.point((cx, cy), fill=(0, 0, 0))  # faint dot
            continue
        col = PURPOSE_RGB.get(info["purpose"], (255, 255, 255))
        rad = 4 if info["name"] in BIG else 2
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col, outline=(0, 0, 0))
    return img


level_terrain_level = 0


def render_map(mapjson, out_png):
    global level_terrain_level
    levels = mapjson["terrain"]
    w = mapjson["width"]
    h = mapjson["height"]
    imgs = []
    for li, lvl in enumerate(levels):
        level_terrain_level = li
        imgs.append(render_level(lvl, mapjson["objects"], w, h))
    # stack levels horizontally with a gap
    gap = 12
    W = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    H = max(i.height for i in imgs)
    canvas = Image.new("RGB", (W, H), (20, 20, 20))
    x = 0
    for i in imgs:
        canvas.paste(i, (x, 0))
        x += i.width + gap
    canvas.save(out_png)
    return out_png


if __name__ == "__main__":
    import glob

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sel = sys.argv[1:] or ["Elbow Room", "A Viking We Shall Go.h3m", "Free for All"]
    os.makedirs(f"{_ROOT}/out/render", exist_ok=True)
    for key in sel:
        f = [
            p
            for p in glob.glob(f"{_ROOT}/maps_json/*.json")
            if key.replace(".h3m", "") in p
        ]
        if not f:
            print("no match for", key)
            continue
        m = json.load(open(f[0]))
        outp = f"{_ROOT}/out/render/{os.path.basename(f[0]).replace('.json', '')}.png"
        render_map(m, outp)
        print(
            f"rendered {m['name']} ({m['width']}x{m['height']}, {len(m['terrain'])} lvl) -> {outp}"
        )
