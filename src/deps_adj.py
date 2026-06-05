"""Object-ADJACENCY GRAPH extraction -- the 'shape' a histogram misses.

A zone is not a bag of objects with counts; it has a local spatial GRAPH: which
object types sit next to which, AT WHAT RELATIVE OFFSET. A mine almost always has
a guard on its approach and resources fanned around it; trees line up into
borders; treasure clusters behind one gate. Those edges -- with their geometry --
are the zone's shape.

For each object we record its neighbours within radius R on the same level:
(anchor_purpose -> neighbour_purpose, dx, dy). Aggregated over the corpus this
gives, per anchor type: its degree (how many neighbours), the neighbour-type
distribution (share), and a sample of the real OFFSET cloud (dx,dy) per pair. The
generator grows zones by sampling these offsets, reproducing the local geometry.
"""
import sys, os, json, glob, collections, math, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3m, vcmi_ids, deps_realize as R

R_ADJ = 5                       # neighbourhood radius (tiles)
MAX_OFF = 150                   # offset samples kept per (anchor, neighbour) pair
P = R.TYPE2PURPOSE


def neighbours(objs):
    """objs: list of (x,y,purpose). Yield (a_pur, b_pur, dx, dy) for pairs within R_ADJ."""
    cell = R_ADJ
    grid = collections.defaultdict(list)
    for i, (x, y, p) in enumerate(objs):
        grid[(x // cell, y // cell)].append(i)
    for i, (x, y, pa) in enumerate(objs):
        gx, gy = x // cell, y // cell
        for dgx in (-1, 0, 1):
            for dgy in (-1, 0, 1):
                for j in grid.get((gx + dgx, gy + dgy), ()):
                    if j == i:
                        continue
                    xb, yb, pb = objs[j]
                    dx, dy = xb - x, yb - y
                    if dx * dx + dy * dy <= R_ADJ * R_ADJ:
                        yield pa, pb, dx, dy


def build(paths):
    deg = collections.defaultdict(list)                 # anchor -> avg neighbour count (per map/level)
    nbr = collections.defaultdict(collections.Counter)  # anchor -> Counter(neighbour purpose)
    off = collections.defaultdict(list)                  # (anchor,neighbour) -> [(dx,dy),...]
    for pth in paths:
        try:
            m = h3m.parse_file(pth)
        except Exception:
            continue
        for lvl in (0, 1):
            objs = []
            for o in m.objects:
                if o.l != lvl:
                    continue
                r = vcmi_ids.resolve(o.obj_class, o.obj_subclass)
                if r:
                    objs.append((o.x, o.y, P.get(r[0], "?")))
            if len(objs) < 2:
                continue
            cnt = collections.Counter()
            tot = collections.Counter(p for _, _, p in objs)
            for pa, pb, dx, dy in neighbours(objs):
                nbr[pa][pb] += 1
                off[(pa, pb)].append((dx, dy))
                cnt[pa] += 1
            for p in tot:
                deg[p].append(cnt[p] / tot[p])

    prof = {}
    for a in nbr:
        total = sum(nbr[a].values()) or 1
        nlist = []
        for b, c in nbr[a].most_common(8):
            offs = off[(a, b)]
            samp = random.sample(offs, min(MAX_OFF, len(offs)))
            dists = sorted(math.hypot(dx, dy) for dx, dy in offs)
            md = round(dists[len(dists) // 2], 1) if dists else 0
            nlist.append({"pur": b, "share": round(c / total, 3), "dist": md,
                          "offsets": [[dx, dy] for dx, dy in samp]})
        prof[a] = {"avg_degree": round(sum(deg[a]) / max(1, len(deg[a])), 2), "neighbours": nlist}

    out = f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/out/adjacency.json"
    json.dump({"radius": R_ADJ, "profiles": prof}, open(out, "w"))
    return prof, out


if __name__ == "__main__":
    paths = sorted(glob.glob('/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m', recursive=True))
    prof, out = build(paths)
    print(f"adjacency graph over {len(paths)} maps (radius {R_ADJ}) -> {out}\n")
    for a in ["MINE", "TOWN", "GUARD", "REWARD_PICKUP", "DWELLING"]:
        if a not in prof:
            continue
        print(f"{a:14} avg_degree={prof[a]['avg_degree']:.2f}  neighbours (share @ median dist, #offset samples):")
        for e in prof[a]["neighbours"]:
            print(f"     {e['pur']:16} {e['share']:5.2f}  @ {e['dist']} tiles  ({len(e['offsets'])} offsets)")
        print()
