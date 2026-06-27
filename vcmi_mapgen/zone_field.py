"""Open/blocked field as a SHAPED conditional process (per terrain), learned from the corpus.

P(tile open) is shaped by, in raster order:
  - the open-RUN already laid to the left and above  (reproduces the corpus run-length shape: the
    'maximum in either direction' -- open runs rarely exceed ~2-3 tiles, which is why real zones are
    a thin web, not wide clearings),
  - distance to the zone EDGE (forest belts ring a zone -> P(blocked) high near the rim),
and the overall open fraction is the BUDGET (marginal). Centroid forced open as the seed.

    uv run python -m vcmi_mapgen.zone_field --map "All for One" --zone 11
"""
import argparse
import collections
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR        # noqa: E402
import zone_engine as ZE        # noqa: E402
import zone_skeleton as SK      # noqa: E402

R = 8          # cap on run-length feature
EBINS = 6      # edge-distance bins (0..4, 5+)
NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def edge_dist(ts):
    """Chebyshev distance from each zone tile to the nearest NON-zone tile (the rim)."""
    import collections as C
    d = {}
    q = C.deque()
    for (x, y) in ts:
        if any((x + dx, y + dy) not in ts for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            d[(x, y)] = 0
            q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (x + dx, y + dy)
                if n in ts and n not in d:
                    d[n] = d[(x, y)] + 1
                    q.append(n)
    return d


def _feat(x, y, openmap, edist):
    """(left_open_run, up_open_run, edge_bin) from already-decided tiles."""
    lr = 0
    while openmap.get((x - 1 - lr, y)) == 1:
        lr += 1
        if lr >= R:
            break
    ur = 0
    while openmap.get((x, y - 1 - ur)) == 1:
        ur += 1
        if ur >= R:
            break
    eb = min(edist.get((x, y), EBINS - 1), EBINS - 1)
    return (lr, ur, eb)


def learn(terrain, nmaps=159):
    """P(open | left_run, up_run, edge_bin) and the mean open fraction, over corpus zones of one
    terrain. Raster order so left/up are causal."""
    op = collections.Counter()
    tot = collections.Counter()
    fracs = []
    maps = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(ZE.ROOT, "maps_json", "*.json")))[:nmaps]
    for mp in maps:
        try:
            fm = OR.load_faithful(mp)
        except Exception:
            continue
        lvl = fm["terrain"][0]
        zones, zl, canon = ZE._segment_level(lvl)
        for zid in zones:
            z = zones[zid]
            if ZE.TNAME.get(z["terrain_type"]) != terrain or z["area"] < 60:
                continue
            ts = set(z["tiles_set"])
            O = SK.open_set(fm, ts)
            edist = edge_dist(ts)
            openmap = {t: (1 if t in O else 0) for t in ts}
            fracs.append(len(O) / len(ts))
            xs = [x for x, _ in ts]; ys = [y for _, y in ts]
            for y in range(min(ys), max(ys) + 1):          # raster
                for x in range(min(xs), max(xs) + 1):
                    if (x, y) not in ts:
                        continue
                    f = _feat(x, y, openmap, edist)
                    tot[f] += 1
                    op[f] += openmap[(x, y)]
    import statistics as st
    return {"op": op, "tot": tot, "budget": st.fmean(fracs) if fracs else 0.4,
            "nzones": len(fracs)}


def p_open(f, model, bias=0.0):
    o, t = model["op"].get(f, 0), model["tot"].get(f, 0)
    p = (o + 1) / (t + 2)                                  # Laplace
    return min(1.0, max(0.0, p + bias))


def generate(fm, zid, zones, model, seed=1, bias=0.0):
    import random
    z = zones[zid]
    ts = set(z["tiles_set"])
    edist = edge_dist(ts)
    cx, cy = z["centroid"]
    cx, cy = int(round(cx)), int(round(cy))
    # nearest in-zone tile to centroid = forced-open seed
    seedt = min(ts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    rng = random.Random(seed ^ (zid * 2654435761 & 0xFFFFFFFF))
    openmap = {}
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    for y in range(min(ys), max(ys) + 1):
        for x in range(min(xs), max(xs) + 1):
            if (x, y) not in ts:
                continue
            if (x, y) == seedt:
                openmap[(x, y)] = 1
                continue
            f = _feat(x, y, openmap, edist)
            openmap[(x, y)] = 1 if rng.random() < p_open(f, model, bias) else 0
    return ts, {t for t, v in openmap.items() if v == 1}, seedt


def run_lengths(ts, O):
    """Open-run-length histogram (horizontal + vertical) over a field."""
    h = collections.Counter()
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    for y in range(min(ys), max(ys) + 1):
        run = 0
        for x in range(min(xs), max(xs) + 2):
            if (x, y) in ts and (x, y) in O:
                run += 1
            else:
                if run:
                    h[run] += 1
                run = 0
    for x in range(min(xs), max(xs) + 1):
        run = 0
        for y in range(min(ys), max(ys) + 2):
            if (x, y) in ts and (x, y) in O:
                run += 1
            else:
                if run:
                    h[run] += 1
                run = 0
    return h


def render_rw(ts, O, out, scale=6, seedt=None):
    from PIL import Image
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    img = Image.new("RGB", ((x1 - x0 + 1) * scale, (y1 - y0 + 1) * scale), (20, 20, 22))
    px = img.load()
    for (x, y) in ts:
        col = (245, 245, 245) if (x, y) in O else (150, 40, 40)
        for yy in range((y - y0) * scale, (y - y0) * scale + scale):
            for xx in range((x - x0) * scale, (x - x0) * scale + scale):
                px[xx, yy] = col
    if seedt:
        sx, sy = (seedt[0] - x0) * scale, (seedt[1] - y0) * scale
        for yy in range(sy, sy + scale):
            for xx in range(sx, sx + scale):
                px[xx, yy] = (40, 120, 240)
    img.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=11)
    ap.add_argument("--bias", type=float, default=0.0, help="add to P(open) to hit the budget")
    args = ap.parse_args()

    fm = OR.load_faithful(args.map)
    lvl = fm["terrain"][0]
    zones, zl, canon = ZE._segment_level(lvl)
    z = zones[args.zone]
    terrain = ZE.TNAME.get(z["terrain_type"])
    ts_real = set(z["tiles_set"])
    O_real = SK.open_set(fm, ts_real)

    model = learn(terrain)
    print(f"learned {terrain}: zones={model['nzones']} budget(open frac)={model['budget']:.2f}")
    ts, O_gen, seedt = generate(fm, args.zone, zones, model, bias=args.bias)

    rdir = os.path.join(ZE.ROOT, "out", "render", "field")
    os.makedirs(rdir, exist_ok=True)
    base = f"{args.map.replace(' ', '_')}_z{args.zone}"
    render_rw(ts_real, O_real, os.path.join(rdir, base + "_real.png"))
    render_rw(ts, O_gen, os.path.join(rdir, base + "_gen.png"), seedt=seedt)
    # combine
    from PIL import Image
    a = Image.open(os.path.join(rdir, base + "_real.png"))
    b = Image.open(os.path.join(rdir, base + "_gen.png"))
    combo = Image.new("RGB", (a.width + b.width + 12, max(a.height, b.height)), (0, 0, 0))
    combo.paste(a, (0, 0)); combo.paste(b, (a.width + 12, 0))
    combo.save(os.path.join(rdir, base + ".png"))

    print(f"open frac: real={len(O_real)/len(ts_real):.2f}  gen={len(O_gen)/len(ts):.2f}")
    hr, hg = run_lengths(ts_real, O_real), run_lengths(ts, O_gen)
    sr, sg = sum(hr.values()), sum(hg.values())
    print("open-run-length  k:  real%   gen%")
    for k in range(1, 8):
        print(f"   {k}: {100*hr.get(k,0)/max(sr,1):5.1f}  {100*hg.get(k,0)/max(sg,1):5.1f}")
    print(f"-> {rdir}/{base}.png  (real | gen, white=open red=blocked, blue=seed)")


if __name__ == "__main__":
    main()
