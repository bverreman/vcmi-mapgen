"""Fidelity showcase: run the structure-finding algorithm ON a real map and show
what it RETRIEVES, next to the real map itself.

If our representation is faithful, the dependency-extraction should recover the
real map's regions, its gates, and the rooted progression tree -- i.e. retrieve
the map's actual design from raw tiles+objects. Renders two PNGs (real | found)
and prints the algorithm's trace.
"""
import sys, os, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m, deps
from PIL import Image, ImageDraw, ImageFont
from deps_render import TCOL
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GAMEPLAY = set(deps.VALUE) | set(deps.GATE_TYPE) | {98, 77, 34}

def obj_color(cls):
    if cls in (98, 77): return (10, 10, 10)            # town: black
    if cls in deps.MONSTER: return (225, 30, 30)       # guard: red
    if cls in deps.PORTAL: return (200, 60, 230)       # portal: magenta
    if cls in (53, 220): return (255, 180, 0)          # mine: gold
    if cls in (17, 18, 19, 20, 216, 217, 218): return (255, 140, 0)   # dwelling: orange
    if cls in (16, 25, 84, 24, 85, 108, 63): return (255, 110, 0)     # bank
    if cls in (5, 65, 66, 67, 68, 69, 93): return (0, 210, 210)       # artifact: cyan
    if cls in (79, 76, 101, 12, 82, 28): return (255, 240, 130)       # pickup: pale
    return None

DEPTHCOL = [(70, 200, 80), (150, 210, 60), (220, 210, 50), (240, 165, 40),
            (235, 110, 35), (220, 60, 40), (180, 40, 60), (140, 40, 90), (90, 40, 110)]
def depth_color(dep):
    return DEPTHCOL[min(dep, len(DEPTHCOL) - 1)]


def render_real(m, lvl, scale):
    W, H = m.width, m.height
    img = Image.new("RGB", (W * scale, H * scale)); px = img.load()
    for y in range(H):
        for x in range(W):
            col = TCOL.get(m.terrain[lvl][y][x].terrain, (0, 0, 0))
            for dy in range(scale):
                for dx in range(scale):
                    px[x * scale + dx, y * scale + dy] = col
    d = ImageDraw.Draw(img)
    for o in m.objects:                                # blocking decoration (mountains/forests) -> gray
        if o.l != lvl or o.obj_class in GAMEPLAY: continue
        tmpl = m.templates[o.template_index]
        for (bx, by) in deps.blocked_tiles(o, tmpl):
            if 0 <= bx < W and 0 <= by < H:
                d.rectangle([bx * scale, by * scale, bx * scale + scale - 1, by * scale + scale - 1],
                            fill=(95, 85, 80))
    for o in m.objects:
        if o.l != lvl: continue
        col = obj_color(o.obj_class)
        if col:
            cx, cy = o.x * scale + scale // 2, o.y * scale + scale // 2
            r = scale // 2 + (1 if col in ((10, 10, 10), (225, 30, 30)) else 0)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col, outline=(0, 0, 0))
    return img


def render_structure(m, dd, lvl, scale):
    W, H = m.width, m.height
    label = dd["label"][lvl]
    img = Image.new("RGB", (W * scale, H * scale)); px = img.load()
    for y in range(H):
        for x in range(W):
            rid = label[y][x]
            if rid < 0:
                col = (60, 60, 75)                     # impassable or guarded (not free space)
            elif rid in dd["depth"]:
                col = depth_color(dd["depth"][rid])    # reachable region, coloured by progression depth
            else:
                col = (110, 110, 110)                  # region not reached from a start
            for dy in range(scale):
                for dx in range(scale):
                    px[x * scale + dx, y * scale + dy] = col
    d = ImageDraw.Draw(img)
    # region centroids on this level
    acc = collections.defaultdict(lambda: [0, 0, 0])
    for y in range(H):
        for x in range(W):
            rid = label[y][x]
            if rid >= 0:
                a = acc[rid]; a[0] += x; a[1] += y; a[2] += 1
    cent = {r: (a[0] / a[2], a[1] / a[2]) for r, a in acc.items()}
    # dependency-tree edges between region centroids
    for r, e in dd["parent_gate"].items():
        a, b = e["a"], e["b"]
        if a in cent and b in cent:
            (ax, ay), (bx, by) = cent[a], cent[b]
            d.line([ax * scale, ay * scale, bx * scale, by * scale], fill=(255, 255, 255), width=2)
    # gates discovered
    GC = {"guard": (225, 30, 30), "portal": (200, 60, 230), "key": (0, 220, 220),
          "garrison": (255, 150, 0), "quest": (240, 240, 240)}
    for g in dd["gates"]:
        if g["l"] != lvl: continue
        cx, cy = g["x"] * scale + scale // 2, g["y"] * scale + scale // 2
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=GC.get(g["type"], (0, 0, 0)), outline=(0, 0, 0))
    # start regions ringed
    for r in dd["start_regions"]:
        if r in cent:
            cx, cy = cent[r][0] * scale, cent[r][1] * scale
            d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], outline=(0, 0, 0), width=2)
    return img


def trace(dd):
    print(f"\n=== algorithm trace: {dd['name']}  {dd['W']}x{dd['H']}x{dd['levels']} ===")
    print(f"free regions found : {dd['n_regions']}   reachable from a start: {dd['n_reachable']}")
    print(f"gates found        : {dd['n_gates']}  {dict(dd['gate_types'])}")
    maxd = max(dd["depth"].values()) if dd["depth"] else 0
    print(f"progression depth  : {maxd}")
    bydepth = collections.Counter(dd["depth"].values())
    for dep in range(maxd + 1):
        regs = [r for r in dd["depth"] if dd["depth"][r] == dep]
        val = sum(dd["region_value"][r] for r in regs)
        print(f"  depth {dep}: {bydepth[dep]:3d} regions   total reward value={val}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/All for One.h3m"
    m = h3m.parse_file(path)
    dd = deps.extract_map(m)
    trace(dd)
    scale = 8; lvl = 0
    real = render_real(m, lvl, scale)
    struct = render_structure(m, dd, lvl, scale)
    gap = 14
    combo = Image.new("RGB", (real.width + struct.width + gap, real.height), (255, 255, 255))
    combo.paste(real, (0, 0)); combo.paste(struct, (real.width + gap, 0))
    os.makedirs(f"{ROOT}/out/render", exist_ok=True)
    out = f"{ROOT}/out/render/showcase_{os.path.basename(path).replace('.h3m','').replace(' ','_')}.png"
    combo.save(out)
    real.save(out.replace("showcase_", "real_")); struct.save(out.replace("showcase_", "struct_"))
    print(f"\nwrote {out}  (left: real map surface | right: structure the algorithm retrieved)")
