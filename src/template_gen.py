"""Deterministic template-driven map generator (entry point).

Pipeline:
    terrain (a real map's terrain, or generated)
      -> segment each level into zones by terrain type      (terrain_segment)
      -> assign each zone a TYPE (castle/quest/dwelling/passage/thematic)
      -> per zone: place objects, carve narrow paths, fill walls   (zone_fill)
      -> coordinate subterranean-gate pairs across levels
      -> build fm -> repair (expect ~0) -> traverse -> to_vmap

Seed only varies interchangeable object SUBTYPES; positions/counts/structure are
deterministic.

Usage:
    uv run python src/template_gen.py --terrain "All for One" --seed 0 --emit-vmap
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import energy_place as EP
import faithful
import markov_terrain as MT
import obj_resolve as OR
import recon
import terrain_segment as TS
import traverse as TR
import zone_fill as ZF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATER, ROCK = 8, 9
MIN_TOWN_AREA = 50


def _load_templates() -> dict:
    return json.load(open(os.path.join(ROOT, "out", "zone_templates.json")))


def assign_types(zones: dict, level_grid: list, n_castle: int, small_area: int,
                 main_comp: set) -> dict:
    """Generation-time zone typing (no object peeking).

    castle  : n_castle largest, well-spread zones in the main passable component.
    passage : tiny zones (area <= small_area).
    quest   : ~1 in 8 of the remainder (deterministic stride) — adds quest gates.
    dwelling: ~1 in 50 of the remainder.
    thematic: the rest.
    """
    types: dict = {zid: None for zid in zones}

    # castle candidates: big enough, in the main component
    def in_main(z):
        cx, cy = zones[z]["centroid"]
        return (int(round(cx)), int(round(cy))) in main_comp or \
               any(t in main_comp for t in zones[z]["tiles"][:1])

    cands = sorted([z for z in zones
                    if zones[z]["area"] >= MIN_TOWN_AREA and in_main(z)],
                   key=lambda z: -zones[z]["area"])
    castles: list = []
    min_sep2 = (max(small_area, 1) ** 0.5 * 3) ** 2
    for z in cands:
        if len(castles) >= n_castle:
            break
        cx, cy = zones[z]["centroid"]
        if all((cx - zones[c]["centroid"][0]) ** 2 + (cy - zones[c]["centroid"][1]) ** 2
               >= min_sep2 for c in castles):
            castles.append(z)
    # if spacing was too strict, top up by area
    for z in cands:
        if len(castles) >= n_castle:
            break
        if z not in castles:
            castles.append(z)
    for z in castles:
        types[z] = "castle"

    rest = sorted([z for z in zones if types[z] is None], key=lambda z: -zones[z]["area"])
    i = 0
    for z in rest:
        if zones[z]["area"] <= small_area:
            types[z] = "passage"
            continue
        if i % 8 == 3:
            types[z] = "quest"
        elif i % 50 == 7:
            types[z] = "dwelling"
        else:
            types[z] = "thematic"
        i += 1
    return types


def _gate_identity() -> dict | None:
    """The subterranean-gate object identity from objlib (relational; not re-rolled)."""
    for terr_entries in OR._OBJLIB.get("TRANSPORT", {}).values():
        for e in terr_entries:
            if e["type"] == "subterraneanGate":
                return {k: e[k] for k in ("type", "subtype", "animation", "mask")}
    return None


def _fits_both(mask, x, y, pass0, pass1, W, H):
    for tx, ty, _b in OR.mask_cells(mask, x, y):
        if not (0 <= tx < W and 0 <= ty < H):
            return False
        if (tx, ty) not in pass0 or (tx, ty) not in pass1:
            return False
    return True


def plan_gates(terr, n_gates: int):
    """Pick up to n_gates (x,y) passable on BOTH levels, spread out. Returns list."""
    if len(terr) < 2:
        return []
    g = _gate_identity()
    if not g:
        return []
    g0 = ZF.terrain_ints(terr[0])
    g1 = ZF.terrain_ints(terr[1])
    H, W = len(g0), len(g0[0])
    p0 = ZF.passable_set(g0)
    p1 = ZF.passable_set(g1)
    m0 = ZF.components(p0)
    m1 = ZF.components(p1)
    main0 = m0[0] if m0 else set()
    main1 = m1[0] if m1 else set()
    shared = sorted(t for t in (main0 & main1)
                    if _fits_both(g["mask"], t[0], t[1], p0, p1, W, H))
    if not shared:
        return []
    cx, cy = W / 2, H / 2
    start = min(shared, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    picks = ZF.farthest_points(shared, n_gates, start)
    return [(x, y, g) for (x, y) in picks]


def gen_terrain(W: int, H: int, seed: int) -> list:
    """Markov + Gibbs surface terrain (learned from the corpus) as one faithful
    level of writer-ready {t,view,m} cells."""
    import random
    rnd = random.Random(seed)
    model = MT.learn(0)
    grid = MT.generate(model, W, H, rnd)
    m4 = MT.learn4(0)
    MT.gibbs(grid, m4, model["marg"], rnd, sweeps=6)
    return [[[recon._terr_cell({"t": t}) for t in row] for row in grid]]


def generate(name: str, seed: int, n_towns: int = 0, terrain: list | None = None):
    templates = _load_templates()
    small_area = templates.get("small_zone_area", 100)
    if terrain is not None:
        terr = terrain
        real_objs = []
    else:
        m = OR.load_faithful(name)
        terr = m["terrain"]
        real_objs = m["objects"]
    H, W = len(terr[0]), len(terr[0][0])

    if not n_towns:
        n_towns = sum(1 for o in real_objs
                      if o.get("l", 0) == 0 and OR.purpose_of(o) == "TOWN")
        if not n_towns:   # generated terrain: scale town count with passable area
            pa = len(ZF.passable_set(ZF.terrain_ints(terr[0])))
            n_towns = max(2, min(8, round(pa / 800)))

    rng = None if seed == 0 else random.Random(seed)

    # coordinate gates first so both levels carve to the same (x,y)
    n_gates = 2 if len(terr) > 1 else 0
    gates = plan_gates(terr, n_gates)
    extra_by_level: dict = collections.defaultdict(list)
    for (gx, gy, gid) in gates:
        extra_by_level[0].append((gx, gy, "TRANSPORT", gid))
        extra_by_level[1].append((gx, gy, "TRANSPORT", gid))

    all_objs: list = []
    main_town = None
    for l, lvl in enumerate(terr):
        tgrid = ZF.terrain_ints(lvl)
        passable = ZF.passable_set(tgrid)
        comps = ZF.components(passable)
        main_comp = comps[0] if comps else set()
        zones, zone_label = TS.segment(lvl, subdivide=True)
        n_castle = n_towns if l == 0 else 0
        ztypes = assign_types(zones, tgrid, n_castle, small_area, main_comp)
        res = ZF.fill_level(lvl, l, zones, zone_label, ztypes, templates, rng,
                            extra=extra_by_level.get(l))
        all_objs.extend(res["placements"])
        if l == 0 and main_town is None and res["town_anchors"]:
            # start town = town anchor nearest map centre; main_town = anchor-(2,2)
            ax, ay, _ci = min(res["town_anchors"],
                              key=lambda t: (t[0] - W / 2) ** 2 + (t[1] - H / 2) ** 2)
            main_town = {"l": 0, "x": ax - 2, "y": ay - 2}

    fm = {"terrain": terr, "objects": all_objs, "main_town": main_town,
          "name": f"TemplateGen {name} s{seed}"}
    return fm, templates


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic template-driven generator.")
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-towns", type=int, default=0)
    ap.add_argument("--generate", nargs=2, type=int, metavar=("W", "H"), default=None,
                    help="generate WxH terrain (markov) instead of using a real map")
    ap.add_argument("--emit-vmap", action="store_true")
    args = ap.parse_args()

    terrain = None
    label = args.terrain
    if args.generate:
        W, H = args.generate
        terrain = gen_terrain(W, H, args.seed or 1)
        label = f"Generated_{W}x{H}"
    fm, _templates = generate(label, args.seed, args.n_towns, terrain=terrain)
    purposes = collections.Counter(o.get("purpose", "?") for o in fm["objects"])
    gp = {k: v for k, v in purposes.items() if k != "DECORATION"}
    print(f"terrain='{label}' seed={args.seed} levels={len(fm['terrain'])}")
    print(f"objects={len(fm['objects'])}  decoration={purposes.get('DECORATION',0)}")
    print("gameplay:", dict(sorted(gp.items(), key=lambda kv: -kv[1])))

    n_rem, ok = EP.repair_reachability(fm)
    rr = TR.traverse(fm)
    print(f"reachable: {rr['ok']}  (repair removed {n_rem}; "
          f"reached {rr['reached_tiles']}/{rr['passable_tiles']} L0 tiles; "
          f"cavern_reached={rr['cavern_reached_tiles']})")
    if rr["unreachable_towns"]:
        print("  UNREACHABLE TOWNS:", rr["unreachable_towns"])
    if rr["unreachable_mines"]:
        print("  UNREACHABLE MINES:", len(rr["unreachable_mines"]))

    if args.emit_vmap:
        safe = label.replace(" ", "_")
        out = os.path.join(ROOT, "out", f"TemplateGen-{safe}-s{args.seed}.vmap")
        faithful.to_vmap(fm, out, name=fm["name"])
        print("vmap:", out)


if __name__ == "__main__":
    main()
