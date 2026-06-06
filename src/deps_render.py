"""Schematic PNG of a realized faithful-map (out/deps_proto.json): terrain biomes,
water barriers, road overlays, and objects colour-coded by role. Lets us SEE the
dependency form without the editor's GL view."""

import sys, os, json
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TCOL = {
    0: (150, 110, 70),
    1: (220, 205, 150),
    2: (70, 150, 60),
    3: (235, 240, 245),
    4: (90, 120, 80),
    5: (170, 150, 110),
    6: (120, 90, 130),
    7: (70, 60, 60),
    8: (40, 90, 180),
    9: (50, 45, 45),
}


def is_mtn(t):
    t = t.lower()
    return "mount" in t or "hill" in t or t == "rock" or "cliff" in t


def occ_tiles(x, y, mask):
    h = len(mask)
    w = max(len(r) for r in mask)
    for r, row in enumerate(mask):
        for c, ch in enumerate(row):
            if ch != "V":
                yield (x - (w - 1 - c), y - (h - 1 - r))


def role(o):
    t = o["type"]
    if t == "town" or t == "randomTown":
        return (10, 10, 10)  # towns: black
    if "Monster" in t or t == "monster":
        return (220, 30, 30)  # guards: red
    if t.startswith("monolith") or t == "subterraneanGate":
        return (200, 60, 230)  # portals: magenta
    if (
        t in ("mine", "windmill")
        or "Dwelling" in t
        or "Bank" in t
        or t == "creatureBank"
        or t == "crypt"
    ):
        return (255, 180, 0)  # high reward: gold
    if (
        t in ("resource", "randomResource", "treasureChest", "campfire", "scholar")
        or "Artifact" in t
    ):
        return (255, 240, 120)  # pickups: pale gold
    return None


def render(fm, out, scale=9):
    terr = fm["terrain"][0]
    H = len(terr)
    W = len(terr[0])
    img = Image.new("RGB", (W * scale, H * scale))
    px = img.load()
    for y in range(H):
        for x in range(W):
            c = terr[y][x]
            col = TCOL.get(c["t"], (0, 0, 0))
            if c.get("ot"):
                col = (110, 80, 40)  # road overlay: brown
            for dy in range(scale):
                for dx in range(scale):
                    px[x * scale + dx, y * scale + dy] = col
    d = ImageDraw.Draw(img)
    # mountains first: fill their footprint as a rocky range
    for o in fm["objects"]:
        if not is_mtn(o["type"]):
            continue
        for tx, ty in occ_tiles(o["x"], o["y"], o["mask"]):
            if 0 <= tx < W and 0 <= ty < H:
                d.rectangle(
                    [
                        tx * scale,
                        ty * scale,
                        tx * scale + scale - 1,
                        ty * scale + scale - 1,
                    ],
                    fill=(95, 85, 80),
                )
    for o in fm["objects"]:
        if is_mtn(o["type"]):
            continue
        col = role(o)
        if t := col:
            cx, cy = o["x"] * scale + scale // 2, o["y"] * scale + scale // 2
            r = scale // 2 + (2 if col in ((10, 10, 10), (220, 30, 30)) else 0)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col, outline=(0, 0, 0))
        elif not is_mtn(o["type"]):  # vegetation: small green dot
            cx, cy = o["x"] * scale + scale // 2, o["y"] * scale + scale // 2
            d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=(20, 80, 20))
    img.save(out)
    return out


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else f"{ROOT}/out/deps_proto.json"
    fm = json.load(open(src))
    out = f"{ROOT}/out/render/deps_proto.png"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(render(fm, out))
