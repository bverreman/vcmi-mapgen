"""Render a .vmap (or real .h3m-derived map JSON) exactly as the VCMI map editor
shows it: real 32x32 terrain tiles from the H3 sprite LOD, real object sprites
composited at their anchor positions, objects drawn back-to-front (painter's order).

This replaces the dot/blob renders that hid structural problems.

Usage:
  uv run python src/render_editor.py out/ZoneGraph-All_for_One-s0.vmap
  uv run python src/render_editor.py out/ZoneGraph-All_for_One-s0.vmap --compare "All for One"
    (side-by-side: generated left, real right re-rendered from the corpus JSON)
"""

import sys, os, struct, zlib, zipfile, re, json, argparse, collections
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOD_DIR = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Data"
LOD_FILES = ["H3sprite.lod", "H3ab_spr.lod", "H3bitmap.lod", "H3ab_bmp.lod"]

# terrain code (first 2 chars of tile string) -> terrain .def filename
TERR_DEF = {
    "dt": "dirttl.def", "sa": "sandtl.def", "gr": "grastl.def", "sn": "snowtl.def",
    "sw": "swmptl.def", "rg": "rougtl.def", "sb": "subbtl.def", "lv": "lavatl.def",
    "wt": "watrtl.def", "rc": "rocktl.def",
}
TILE = 32        # pixels per map tile
SPECIAL_PALETTE = {0: (0,0,0,0), 1:(0,0,0,0), 4:(0,0,0,0), 5:(0,0,0,0), 6:(0,0,0,0), 7:(0,0,0,0)}


# --------------------------------------------------------------------------- LOD index
class LodIndex:
    def __init__(self):
        self._files = {}   # name.lower() -> (lod_path, offset, size, csize)
        for lodname in LOD_FILES:
            path = os.path.join(LOD_DIR, lodname)
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                f.seek(8); count = struct.unpack("<I", f.read(4))[0]; f.seek(92)
                for _ in range(count):
                    raw = f.read(16)
                    name = raw.rstrip(b"\x00").decode("latin1", "replace").lower().split("\x00")[0]
                    off, size, _, csize = struct.unpack("<IIII", f.read(16))
                    if name not in self._files:
                        self._files[name] = (path, off, size, csize)

    def read(self, name):
        key = name.lower()
        if key not in self._files and not key.endswith(".def"):
            key += ".def"
        if key not in self._files:
            return None
        path, off, size, csize = self._files[key]
        with open(path, "rb") as f:
            f.seek(off); raw = f.read(csize)
        if csize == size or csize == 0:
            return raw
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw   # stored uncompressed despite size mismatch

    def has(self, name):
        k = name.lower(); return k in self._files or (k + ".def") in self._files


_LOD = None
def lod():
    global _LOD
    if _LOD is None:
        _LOD = LodIndex()
    return _LOD


# --------------------------------------------------------------------------- DEF parser
def _decode_frame(data, foff, w, h):
    """Decode one DEF frame to RGBA PIL Image (32-bit).

    H3 SSpriteDef header (8 uint32): size, format, fullW, fullH, frameW, frameH,
    leftMargin, topMargin. Offsets in the formats below are relative to the data
    that starts right after this 32-byte header (`base`).
      format 0 : raw frameW*frameH palette indices.
      format 1 : one uint32 offset per line; segments are (code, len) pairs,
                 code 0xFF = raw run, else a run of palette[code].
      format 2 : one uint16 offset per line; single-byte segments
                 (type=bits7:5, len=bits4:0+1), type 7 = raw, else palette[type].
      format 3 : each line split into 32-px BLOCKS, one uint16 offset PER BLOCK
                 (row-major), each block decoded with the format-2 segment scheme.
    Palette indices 0-7 are special: render shadows (1/4/6) as semi-transparent
    black so objects read like the editor; 0/2/3 transparent, 5 selection and
    7 player-flag transparent for static renders.
    """
    _sz, comp, fw_full, fh_full, fw, fh, fleft, ftop = struct.unpack_from("<IIIIIIII", data, foff)
    pal_raw = data[16:16 + 256 * 3]
    SPECIAL = {0: (0, 0, 0, 0), 1: (0, 0, 0, 64), 2: (0, 0, 0, 0), 3: (0, 0, 0, 0),
               4: (0, 0, 0, 128), 5: (0, 0, 0, 0), 6: (0, 0, 0, 128), 7: (0, 0, 0, 0)}
    palette = [SPECIAL[i] if i < 8 else (pal_raw[i*3], pal_raw[i*3+1], pal_raw[i*3+2], 255)
               for i in range(256)]

    img = Image.new("RGBA", (fw_full, fh_full), (0, 0, 0, 0))
    px = img.load()
    base = foff + 32

    if comp == 0:
        p = base
        for y in range(fh):
            for x in range(fw):
                px[fleft + x, ftop + y] = palette[data[p]]
                p += 1
        return img

    if comp == 1:
        offs = struct.unpack_from("<%dI" % fh, data, base)
        for y in range(fh):
            p, x = base + offs[y], 0
            while x < fw:
                code, length = data[p], data[p + 1] + 1
                p += 2
                if code == 0xFF:
                    for _ in range(length):
                        if x < fw:
                            px[fleft + x, ftop + y] = palette[data[p]]
                        p += 1
                        x += 1
                else:
                    c = palette[code]
                    for _ in range(length):
                        if x < fw:
                            px[fleft + x, ftop + y] = c
                        x += 1
        return img

    if comp == 2:
        offs = struct.unpack_from("<%dH" % fh, data, base)
        for y in range(fh):
            p, x = base + offs[y], 0
            while x < fw:
                seg = data[p]; p += 1
                typ, length = seg >> 5, (seg & 0x1F) + 1
                if typ == 7:
                    for _ in range(length):
                        if x < fw:
                            px[fleft + x, ftop + y] = palette[data[p]]
                        p += 1
                        x += 1
                else:
                    c = palette[typ]
                    for _ in range(length):
                        if x < fw:
                            px[fleft + x, ftop + y] = c
                        x += 1
        return img

    # comp == 3: per-line 32-px blocks, one uint16 offset per block (row-major)
    blocks = (fw + 31) // 32
    offs = struct.unpack_from("<%dH" % (blocks * fh), data, base)
    for y in range(fh):
        for b in range(blocks):
            p = base + offs[y * blocks + b]
            x, xend = b * 32, min(b * 32 + 32, fw)
            while x < xend:
                seg = data[p]; p += 1
                typ, length = seg >> 5, (seg & 0x1F) + 1
                if typ == 7:
                    for _ in range(length):
                        if x < xend:
                            px[fleft + x, ftop + y] = palette[data[p]]
                        p += 1
                        x += 1
                else:
                    c = palette[typ]
                    for _ in range(length):
                        if x < xend:
                            px[fleft + x, ftop + y] = c
                        x += 1
    return img


def parse_def(data):
    """Parse a DEF file -> list of groups, each group = list of PIL Images."""
    dtype, fw, fh, nblocks = struct.unpack_from("<IIII", data, 0)  # 4 fields before palette
    if nblocks == 0 or nblocks > 64:
        nblocks = 1
    pos = 16 + 256 * 3
    groups = []
    for _ in range(max(1, nblocks)):
        if pos + 8 > len(data):
            break
        bid, nframes = struct.unpack_from("<II", data, pos); pos += 16
        if nframes > 200 or nframes <= 0:
            break
        pos += nframes * 13                        # skip names
        if pos + nframes * 4 > len(data):
            break
        offsets = list(struct.unpack_from("<" + "I" * nframes, data, pos)); pos += nframes * 4
        frames = []
        for foff in offsets:
            try:
                img = _decode_frame(data, foff, fw, fh)
                frames.append(img)
            except Exception:
                frames.append(Image.new("RGBA", (fw, fh), (0, 0, 0, 0)))
        groups.append(frames)
    return groups


_def_cache = {}
def get_def(name):
    key = name.lower()
    if key in _def_cache:
        return _def_cache[key]
    data = lod().read(key)
    if data is None:
        _def_cache[key] = None; return None
    try:
        groups = parse_def(data)
    except Exception:
        groups = None
    _def_cache[key] = groups
    return groups


# --------------------------------------------------------------------------- terrain tile decode
def terr_tile_img(tile_str):
    """tile_str e.g. 'dt15_' -> 32x32 RGBA terrain tile image."""
    tc = tile_str[:2]
    rest = tile_str[2:]
    # extract view number (digits before mirror char)
    mir_pos = next((i for i, c in enumerate(rest) if c in "_+-|"), len(rest))
    try:
        view = int(rest[:mir_pos])
    except ValueError:
        view = 0
    mir_char = rest[mir_pos] if mir_pos < len(rest) else "_"
    flip_h = mir_char in ("-", "+")
    flip_v = mir_char in ("|", "+")

    def_name = TERR_DEF.get(tc)
    if def_name is None:
        return Image.new("RGBA", (TILE, TILE), (40, 40, 40, 255))
    groups = get_def(def_name)
    if not groups or not groups[0]:
        return Image.new("RGBA", (TILE, TILE), (80, 40, 80, 255))
    frames = groups[0]
    img = frames[view % len(frames)].copy()
    if img.size != (TILE, TILE):
        img = img.resize((TILE, TILE), Image.NEAREST)
    if flip_h:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if flip_v:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    return img.convert("RGBA")


# --------------------------------------------------------------------------- vmap reader
def _relaxed(t):
    t = re.sub(r"//[^\n]*", "", t)
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    return json.loads(t)


def read_vmap(path):
    z = zipfile.ZipFile(path)
    surf = _relaxed(z.read("surface_terrain.json").decode())
    objs = _relaxed(z.read("objects.json").decode("utf-8", "replace"))
    return surf, objs


def read_real(name):
    """Load from original .h3m file (full tile view/mirror data) + objects with animation."""
    h3m_path = f"{ROOT}/maps/{name}.h3m"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import h3m as H3M, faithful as FA
    hmap = H3M.parse_file(h3m_path)
    # build tile strings from the parsed Tile objects (terrain, view, mirror)
    surf = []
    for row in hmap.terrain[0]:
        surf.append([FA.tile_string({"t": t.terrain, "view": t.view, "m": t.mirror,
                                      "rt": getattr(t, "river_type", 0),
                                      "rd": getattr(t, "river_dir", 0),
                                      "ot": getattr(t, "road_type", 0),
                                      "od": getattr(t, "road_dir", 0)})
                     for t in row])
    objs = []
    for mo in hmap.objects:
        if getattr(mo, "level", 0) != 0:
            continue
        anim = getattr(mo, "animation", "") or ""
        anim = anim.lower().replace(".def", "")
        objs.append({"x": mo.x, "y": mo.y, "l": 0,
                     "template": {"animation": anim, "mask": []},
                     "type": ""})
    return surf, objs


# --------------------------------------------------------------------------- compositing
def render_map(surf, objs, title=""):
    H, W = len(surf), len(surf[0])
    canvas = Image.new("RGB", (W * TILE, H * TILE), (0, 0, 0))

    # 1) terrain tiles
    for y in range(H):
        for x in range(W):
            tile_img = terr_tile_img(surf[y][x])
            canvas.paste(tile_img.convert("RGB"), (x * TILE, y * TILE))

    # 2) objects: painter's order = sort by y asc, then by x asc (back-to-front)
    sorted_objs = sorted(objs, key=lambda o: (o.get("l", 0) != 0, o["y"], o["x"]))
    miss = 0
    for o in sorted_objs:
        if o.get("l", 0) != 0:
            continue
        anim = o.get("template", {}).get("animation", "")
        if not anim:
            continue
        groups = get_def(anim)
        if not groups or not groups[0]:
            miss += 1; continue
        sprite = groups[0][0]                      # frame 0 of group 0
        sw, sh = sprite.size
        # anchor is bottom-right of the object footprint; sprite is drawn so its
        # bottom-right pixel aligns with the bottom-right of the anchor tile.
        px = (o["x"] + 1) * TILE - sw
        py = (o["y"] + 1) * TILE - sh
        canvas.paste(sprite.convert("RGB"), (px, py), sprite.split()[3])
    if miss:
        print(f"  {miss} objects with missing sprites")

    # 3) optional title bar
    if title:
        from PIL import ImageDraw
        ImageDraw.Draw(canvas).text((4, 4), title, fill=(255, 255, 255))
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vmap", help=".vmap path to render")
    ap.add_argument("--compare", default=None, help="corpus map name to render alongside")
    ap.add_argument("--out", default=None, help="output PNG path (default auto)")
    args = ap.parse_args()

    surf, objs = read_vmap(args.vmap)
    gen_img = render_map(surf, objs, title=os.path.basename(args.vmap))

    if args.compare:
        rsurf, robjs = read_real(args.compare)
        real_img = render_map(rsurf, robjs, title=f"REAL: {args.compare}")
        gap = 8
        canvas = Image.new("RGB", (real_img.width + gen_img.width + gap,
                                   max(real_img.height, gen_img.height)), (0, 0, 0))
        canvas.paste(real_img, (0, 0))
        canvas.paste(gen_img, (real_img.width + gap, 0))
        out_img = canvas
    else:
        out_img = gen_img

    out_path = args.out or os.path.join(
        ROOT, "out", "render",
        os.path.basename(args.vmap).replace(".vmap", "_editor.png"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out_img.save(out_path)
    print(f"wrote {out_path}  ({out_img.width}x{out_img.height})")


if __name__ == "__main__":
    main()
