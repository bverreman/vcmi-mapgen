"""Shape-driven zone-rebuilding engine (one unified CLI).

Records each terrain zone's object pattern in a shape-relative frame and replays it.
The hard contract: rebuilding on the SAME shape reproduces the EXACT same objects
(bit-exact, integer-only); a DIFFERENT shape gets a sensible warp adaptation.

Pipeline, all in this module:
  extract  -> segment a map into same-terrain zones, label them, dump a template JSON
  inspect  -> zone-tint + label segmentation PNG (per level)
  rebuild  -> template + target terrain -> faithful map (.json + editor .vmap)
              --identity [--verify]   exact reconstruction on the source terrain
              --zone N --deform       rough warp of one zone onto a deformed shape
  run      -> extract -> segmentation -> identity rebuild+verify -> realistic editor
              render, then STOP for manual inspection (the foundation checkpoint).

The identity-rebuild engine itself (templates, bit-exact replay, warp adaptation) lives in
vcmi_mapgen.rebuild.engine; its renderers (segmentation PNG, editor-sprite compare, panel
montage) live in vcmi_mapgen.rebuild.render. This module is the thin CLI on top of both.

Reuses (no existing file modified):
  terrain_segment.segment / compute_static_features   (zones + interior-depth feature)
  kit.objects.load_faithful / exact_identity / purpose_of / mask_cells
  faithful.save / faithful.to_vmap                     (artifacts)
  render_editor.read_vmap / read_real / render_map     (realistic sprite render)
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import statistics
import sys

from vcmi_mapgen.kit import terrain_segment as TS
from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen.kit.segmentation import _segment_level
from vcmi_mapgen.kit.terrain_lookup import TNAME
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import faithful as FA
from vcmi_mapgen.kit.paths import project_root, slug
from vcmi_mapgen.rebuild.engine import (
    _prio, _stretch_gameplay, deco_binned, deco_quilt, deco_split, deform_terrain_level,
    extract_template, label_zone, markov_terrain_level, rebuild_map, rebuild_zone_warp,
    transform_zone, verify_identity, write_features, write_template, zone_features,
)
from vcmi_mapgen.rebuild.render import editor_render, render_segmentation, render_zone_compare

ROOT = project_root()

def _print_tables(tables):
    for L, table in tables:
        print(f"\n  level {L}: {len(table)} zones")
        for zid, lab, area, nobj in table:
            print(f"    zone {zid:>2}  {lab:<22} area={area:<4} objs={nobj}")


# ---------------------------------------------------------------------------
# Object clustering/anchor helpers — not used by this CLI directly, but kept
# here (not in rebuild/engine.py) because vcmi_mapgen.research.zone_skeleton
# reaches for them via ZE._cluster_objects / ZE._group_anchor / ZE._mask_anchor_cells.
# ---------------------------------------------------------------------------

# A placement GROUP forms around a single anchor that must be a VISITABLE destination (interacted
# with by standing on its 'A' visit tile) or a PICKABLE reward — never a guard, lone INFO sign, or
# decoration (the user's rule: "cluster around visitable object and pickable resources / artifacts").
# Anything else (INFO, decoration) may only join a group as a member.
ANCHOR_PURPOSES = {
    "TOWN", "MINE", "DWELLING", "BANK",                    # visitable destinations
    "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "MANA",  # visitable bonus/skill sites
    "REWARD_PICKUP", "RESOURCE_PILE",                      # pickable loot / resource piles
}
# Anchor priority when a cluster holds several eligible objects (tie broken by larger footprint):
# the most "important" destination organizes the set-piece (a town over a mine over a pickup).
_ANCHOR_PRIO = {"TOWN": 9, "BANK": 8, "MINE": 7, "DWELLING": 6, "STAT_PERMANENT": 5,
                "SPELL_SKILL": 4, "BONUS_TEMP": 3, "MANA": 2, "REWARD_PICKUP": 1, "RESOURCE_PILE": 0}
GROUP_EPS = 3            # single-linkage radius (tiles) that ties two gameplay objects into one group:
#                          3 balances corpus multi-object fraction (~0.59, vs map-level 0.64) against
#                          tight set-piece diameter (~2 tiles) — 4 chains whole dense zones together.


def _manh(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def _footprint_area(o):
    """Bounding-box cell count of an object's mask (proxy for object size)."""
    m = o.get("mask") or []
    return len(m) * (len(m[0]) if m else 0)


def _cluster_objects(objs, eps=GROUP_EPS):
    """Single-linkage spatial clusters: two objects are linked when their Manhattan distance is
    <= eps; clusters are the connected components. O(n^2) over a zone's handful of gameplay objects."""
    n = len(objs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _manh(objs[i], objs[j]) <= eps:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    comps = collections.defaultdict(list)
    for i, o in enumerate(objs):
        comps[find(i)].append(o)
    return list(comps.values())


def _group_anchor(cluster):
    """The object that organizes a cluster: the highest-priority ANCHOR_PURPOSES member (visitable
    or pickable), ties broken by larger footprint. None if the cluster has no eligible anchor."""
    cands = [o for o in cluster if o.get("_purpose") in ANCHOR_PURPOSES]
    if not cands:
        return None
    return max(cands, key=lambda o: (_ANCHOR_PRIO.get(o["_purpose"], -1), _footprint_area(o)))


def _mask_anchor_cells(mask, x, y):
    """Yield the (tx, ty) of the mask's 'A' (visitable-anchor) cells — the tile the hero stands
    on / that triggers the object — using the same bottom-right anchoring as ``OR.mask_cells``
    (col 0 is the leftmost tile, `tx = x - (ww-1-c)`; see OR.mask_cells's docstring)."""
    hh = len(mask)
    for r, row in enumerate(mask):
        ww = len(row)
        for c, ch in enumerate(row):
            if ch == "A":
                yield x - (ww - 1 - c), y - (hh - 1 - r)


def cmd_extract(args):
    path, t = write_template(args.name, args.out)
    nz = sum(len(l["zones"]) for l in t["levels"])
    nbar = sum(len(l["barrier_objects"]) for l in t["levels"])
    print(f"extracted {t['name']}: {len(t['levels'])} levels, {nz} zones, "
          f"{nbar} barrier objects -> {path}")


# Editor-style passability overlay colours (the four real mask states; see ontology mask docs).
_MASK_OVERLAY_COLORS = {
    "B": (235, 40, 40, 120),    # blocked
    "X": (245, 140, 25, 150),   # blocked + visitable (a building's action tile, visited adjacent)
    "A": (245, 225, 40, 165),   # passable + visitable (walk-onto pickup / stand-on tile)
    "V": (70, 170, 255, 85),    # passable overlay / overhang
}


def _mask_overlay(full_sprite, grid, tile):
    """Editor-style passability overlay: the object's full 6x8 B/X/A/V `grid` drawn TRANSLUCENT
    over the FULL (uncropped) sprite canvas. H3 sprites are CENTRED on their footprint and sit on
    the ground, so the mask's active bounding box is centred HORIZONTALLY and bottom-aligned
    VERTICALLY within the sprite's tile grid. (The objects.txt grid is NOT simply left-justified:
    e.g. a 1-tile pine whose art is on the right tile, a seer-hut tile centred under a 3-wide hut,
    a town gate at the centre column -- absolute `sx = c` lands those on the wrong, often empty,
    tile.) Validated against art: pine `B` on the trunk, wood pile `A` on the logs, seer hut
    centred, town gate at the sprite centre. '.' grid cells are outside the footprint and not drawn.
    Cropped to the union of sprite content + footprint. (This sprite-canvas frame is distinct from
    the map-placement bottom-RIGHT anchor in render_editor/kit.objects -- do not conflate them.)"""
    from PIL import Image, ImageDraw

    base = full_sprite.convert("RGBA")
    cols_t, rows_t = base.width // tile, base.height // tile
    act = [(r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch != "."]
    if not act:
        return base
    r0 = min(r for r, _ in act); r1 = max(r for r, _ in act)
    c0 = min(c for _, c in act); c1 = max(c for _, c in act)
    cw, chh = c1 - c0 + 1, r1 - r0 + 1
    coff = (cols_t - cw + 1) // 2              # centre footprint horizontally (round outward)
    roff = rows_t - chh                        # bottom-align vertically (object sits on the ground)

    ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cells = []                                 # tile-pixel boxes covered by a non-'.' grid cell
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            ch = grid[r][c]
            col = _MASK_OVERLAY_COLORS.get(ch)
            if col is None:                    # '.' (passable inside bbox is 'V'; truly outside skipped)
                continue
            sx, sy = (c - c0) + coff, (r - r0) + roff   # centred column; rows bottom-aligned
            if not (0 <= sx < cols_t and 0 <= sy < rows_t):
                continue
            x0, y0 = sx * tile, sy * tile
            d.rectangle((x0, y0, x0 + tile - 1, y0 + tile - 1), fill=col,
                        outline=(0, 0, 0, 90))
            if ch != "V":
                cells.append((x0, y0, x0 + tile, y0 + tile))
    base.alpha_composite(ov)

    sb = base.getbbox()                        # union of sprite content + footprint
    boxes = [b for b in [sb] if b] + cells
    if boxes:
        x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
        base = base.crop((x0, y0, x1, y1))
    return base


def cmd_render_ontology(args):
    """Render every leaf of the ontology tree to its full path on disk:
    out/ontology/<CLUSTER>/<PURPOSE>/<type>/<terrain>/<leaf>.png
    and, next to each, `<leaf>.mask.png` -- the same sprite with its passability mask overlaid
    the way the editor draws it (translucent B/X/A/V cells, bottom-left/footprint justified).

    The directory layout mirrors the hardcoded ontology.TAXONOMY exactly -- the absolute object list
    the VCMI/H3 map editor can place (from objects.txt), every CLUSTER -> PURPOSE -> type -> terrain
    -> leaf edge down to the sprite. A leaf's sprite is its `animation` DEF (frame 0); colour-keyed
    quest objects (border gate/guard, keymaster tent) sit under "land" with one leaf per colour.
    """
    import csv
    import shutil
    from vcmi_mapgen import render_editor as RE
    import ontology as ON

    out_root = args.out or os.path.join(ROOT, "out", "ontology")
    tree = ON.build_tree()

    if os.path.isdir(out_root):                 # rebuild cleanly (path shape may change across runs)
        shutil.rmtree(out_root)
    os.makedirs(out_root, exist_ok=True)

    rows = []
    per_cluster = collections.Counter()
    skipped = 0
    for cluster, purpose, typ, terrain, name, anim in ON.iter_leaves(tree):
        groups = RE.get_def(anim)
        if not groups or not groups[0]:
            skipped += 1
            continue
        full = groups[0][0]                     # full canvas (kept for tile-aligned mask overlay)
        bbox = full.getbbox()                   # trim transparent margin for the plain sprite
        sprite = full.crop(bbox) if bbox else full
        # collapse redundant consecutive levels (DECORATION/DECORATION/..., VISIBLE/TOWN/TOWN/...).
        chain = [cluster, purpose, typ]
        parts = [chain[0]] + [x for i, x in enumerate(chain[1:], 1) if x != chain[i - 1]]
        parts.append(terrain)
        # colour/subtype-keyed leaves (name != animation) become a faction/colour FOLDER level, so
        # towns read as VISIBLE/TOWN/<faction>/... rather than one cryptic file per faction.
        if name != anim:
            parts.append(name)
        leaf = anim
        d = os.path.join(out_root, *parts)
        os.makedirs(d, exist_ok=True)
        png = os.path.join(d, f"{leaf}.png")
        sprite.save(png)
        mpng = os.path.join(d, f"{leaf}.mask.png")   # same sprite with the passability mask overlaid
        gridmask = ON.full_mask_of(anim) or ON.mask_of(anim)
        _mask_overlay(full, gridmask, RE.TILE).save(mpng)
        per_cluster[cluster] += 1
        rows.append((cluster, purpose, typ, terrain, name, anim,
                     os.path.relpath(png, out_root), os.path.relpath(mpng, out_root)))

    with open(os.path.join(out_root, "index.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("cluster", "purpose", "type", "terrain", "leaf", "animation", "png", "mask_png"))
        w.writerows(sorted(rows))

    print(f"ontology catalog -> {out_root}/  ({len(rows)} PNGs, {skipped} skipped: no sprite)")
    for c in ON.CLUSTERS:
        print(f"  {c:11s} {per_cluster[c]:5d}")
    print(f"  index.csv ({len(rows)} rows)")


def cmd_inspect(args):
    out = os.path.join(ROOT, "out", "render", f"{slug(args.name)}_segmentation.png")
    path, tables = render_segmentation(args.name, out)
    _print_tables(tables)
    print(f"\nsegmentation -> {path}")


def cmd_features(args):
    out, f = write_features(args.name, args.out)
    for lvl in f["levels"]:
        for z in lvl["zones"]:
            if args.zone is not None and z["zone_id"] != args.zone:
                continue
            print(f"\nL{lvl['level']} zone {z['zone_id']}  {z['label']}  "
                  f"area={z['area']}  guard->loot={z['guard_loot_dist']}")
            for p in sorted(z["purposes"], key=_prio):
                i = z["purposes"][p]
                print(f"  {p:<15} n={i['count']:<4} dens={i['density']:.4f} "
                      f"depth={i['depth_mu']:.2f}±{i['depth_sd']:.2f} "
                      f"spacing={i['spacing']:<4} variants={len(i['identities'])}")
    print(f"\nfeatures -> {out}")


def _recon_report(orig_objs, recon_objs, tag):
    om = collections.defaultdict(list)
    for o in orig_objs:
        om[OR.purpose_of(o)].append((o["x"], o["y"]))
    rm = collections.defaultdict(list)
    for o in recon_objs:
        rm[o["_purpose"]].append((o["x"], o["y"]))
    print(f"  --- {tag}: per-purpose  orig -> recon  (centroid shift) ---")
    for p in sorted(set(om) | set(rm), key=_prio):
        shift = ""
        if om.get(p) and rm.get(p):
            ox = statistics.fmean(x for x, _ in om[p]); oy = statistics.fmean(y for _, y in om[p])
            rx = statistics.fmean(x for x, _ in rm[p]); ry = statistics.fmean(y for _, y in rm[p])
            shift = f"  shift={math.hypot(ox - rx, oy - ry):.1f}t"
        print(f"    {p:<15} {len(om.get(p, [])):>4} -> {len(rm.get(p, [])):<4}{shift}")


def _exact_check(orig, recon):
    def k(o):
        return (o["x"], o["y"], o["type"], o["subtype"], o["animation"], tuple(o["mask"]))
    co = collections.Counter(k(o) for o in orig)
    cr = collections.Counter(k(o) for o in recon)
    return co == cr, sum((co & cr).values()), sum(co.values()), sum(cr.values())


def cmd_reconstruct(args):
    name, zid = args.name, args.zone
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    lvl = fm["terrain"][0]
    zones, zone_label, canon = _segment_level(lvl)
    if zid not in zones:
        sys.exit(f"zone {zid} not on level 0 (have {sorted(zones)})")
    # Gather by FOOTPRINT OVERLAP, not just anchor — so the zone's edge ring of
    # mountains and edge mines (e.g. sulfur mines anchored on neighbouring terrain)
    # come with the zone instead of being dropped.
    zone_tiles = zones[zid]["tiles_set"]
    objs = [o for o in fm["objects"] if o.get("l", 0) == 0
            and any((tx, ty) in zone_tiles
                    for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]))]
    anchor_objs = [o for o in objs if 0 <= o["x"] < W and 0 <= o["y"] < H
                   and zone_label[o["y"]][o["x"]] == zid]
    z = dict(zones[zid]); z["_zid"] = zid
    prof = zone_features(z, anchor_objs, canon[zid])  # canon only defined for anchor tiles
    prof["label"] = label_zone(zones[zid], anchor_objs, W, H)

    src_decor = [o for o in objs if OR.purpose_of(o) == "DECORATION"]
    DECO_MODELS = {"binned": deco_binned, "quilt": deco_quilt, "split": deco_split}
    want = list(DECO_MODELS) if args.deco_model == "all" else [args.deco_model]

    def assemble(tgt_zone, tgt_canon, model):
        """Gameplay (forward-mapped rigid, shared) + decoration via the chosen model."""
        gameplay, hard = _stretch_gameplay(zones[zid], objs, tgt_zone, args.seed)
        decor = DECO_MODELS[model](src_decor, canon[zid], zones[zid]["tiles_set"],
                                   tgt_zone, tgt_canon, hard, args.seed)
        return gameplay + decor

    panels = [{"objs": objs, "tiles": zones[zid]["tiles_set"], "terr": lvl,
               "W": W, "H": H, "title": f"ORIGINAL z{zid} {prof['label']}"}]

    if args.mode == "transform":
        # Pure stretch: the SAME objects on a larger grid (positions scale, no fill).
        same, _ = transform_zone(zones[zid], objs, zones[zid], level=0, seed=args.seed)
        ok, m, t, r = _exact_check(objs, same)
        print(f"[transform] zone {zid} {prof['label']}: area={z['area']}, orig={len(objs)}, "
              f"same-shape exact? {'YES' if ok else 'NO'} ({m}/{t} match)")
        _recon_report(objs, same, "same-shape")
        panels.append({"objs": same, "tiles": zones[zid]["tiles_set"], "terr": lvl,
                       "W": W, "H": H, "title": "same-shape (exact)"})
        if args.deform:
            grid = deform_terrain_level(lvl, zones[zid], W, H, args.fx, args.fy)
            zr, _lr, cr = _segment_level(grid)
            tz = max(zr, key=lambda k: zr[k]["area"])
            dobjs, drep = transform_zone(zones[zid], objs, zr[tz], level=0, seed=args.seed)
            if not drep.get("ok", True):
                print(f"stretch REJECTED ({zr[tz]['area']}t): {drep['reason']} — panel skipped")
            else:
                print(f"stretched onto {zr[tz]['area']}t (was {z['area']}) -> {len(dobjs)} objs")
                panels.append({"objs": dobjs, "tiles": zr[tz]["tiles_set"], "terr": grid,
                               "W": W, "H": H, "title": f"STRETCH transform {zr[tz]['area']}t"})
    else:
        # features: compare decoration-fill models (generative, variable count).
        same = assemble(zones[zid], canon[zid], want[0])
        print(f"[features] zone {zid} {prof['label']}: area={z['area']}, orig={len(objs)}, "
              f"same-shape({want[0]})={len(same)}")
        _recon_report(objs, same, f"same-shape ({want[0]})")
        panels.append({"objs": same, "tiles": zones[zid]["tiles_set"], "terr": lvl,
                       "W": W, "H": H, "title": f"same-shape {want[0]}"})
        if args.deform:
            grid = deform_terrain_level(lvl, zones[zid], W, H, args.fx, args.fy)
            zr, _lr, cr = _segment_level(grid)
            tz = max(zr, key=lambda k: zr[k]["area"])
            tgt_zone, tgt_canon = zr[tz], cr[tz]
            src_decor_n = len(src_decor)
            for mk in want:
                full = assemble(tgt_zone, tgt_canon, mk)
                c = collections.Counter(o["_purpose"] for o in full)
                ndec = c.get("DECORATION", 0)
                ratio = ndec / src_decor_n if src_decor_n else 0.0
                gp = ", ".join(f"{p}={c[p]}" for p in sorted(c, key=_prio)
                               if p != "DECORATION")
                print(f"  [{mk:<7}] {tgt_zone['area']}t (was {z['area']}) -> {len(full)} objs, "
                      f"DECORATION {src_decor_n}->{ndec} (x{ratio:.2f})  {gp}")
                panels.append({"objs": full, "tiles": tgt_zone["tiles_set"], "terr": grid,
                               "W": W, "H": H, "title": f"STRETCH {mk} {tgt_zone['area']}t"})
        if args.markov:
            # Target = the largest same-terrain zone of a FRESH Markov-generated
            # terrain (a real-looking new shape, not a stretch of the source).
            MW = args.markov_size
            terr = markov_terrain_level(MW, MW, args.markov_seed)
            mz, _ml, mc = _segment_level(terr)
            tt = zones[zid]["terrain_type"]
            cands = [k for k, zz in mz.items() if zz["terrain_type"] == tt]
            if not cands:
                print(f"markov: no {TNAME.get(tt, tt)} zone at seed {args.markov_seed} "
                      f"(try another --markov-seed)")
            else:
                tz = max(cands, key=lambda k: mz[k]["area"])
                tgt_zone, tgt_canon = mz[tz], mc[tz]
                print(f"markov {TNAME.get(tt, tt)} target: zone {tz}, "
                      f"area={tgt_zone['area']} (source {z['area']}t)")
                for mk in want:
                    full = assemble(tgt_zone, tgt_canon, mk)
                    c = collections.Counter(o["_purpose"] for o in full)
                    gp = ", ".join(f"{p}={c[p]}" for p in sorted(c, key=_prio)
                                   if p != "DECORATION")
                    print(f"  [{mk:<7}] markov {tgt_zone['area']}t -> {len(full)} objs, "
                          f"DECORATION={c.get('DECORATION', 0)}  {gp}")
                    panels.append({"objs": full, "tiles": tgt_zone["tiles_set"], "terr": terr,
                                   "W": MW, "H": MW,
                                   "title": f"MARKOV {mk} {tgt_zone['area']}t"})

    suffix = "_markov" if args.markov and not args.deform else "_stretch"
    out = os.path.join(ROOT, "out", "render", f"{slug(name)}_z{zid}{suffix}.png")
    render_zone_compare(panels, out)
    print(f"\ncompare -> {out}")


def cmd_rebuild(args):
    stem = args.out or os.path.join(ROOT, "out", f"Rebuilt-{args.name.replace(' ', '_')}")
    template = extract_template(args.name)

    if args.deform:
        if args.zone is None:
            sys.exit("--deform requires --zone N")
        fm = _do_deform(args.name, template, args.zone, stem)
    else:  # identity
        src = OR.load_faithful(args.name)
        fm, stats = rebuild_map(template, src["terrain"], identity=True)
        print(f"identity rebuild: {stats['identity']} zones matched, "
              f"{stats['missing']} missing, {len(fm['objects'])} objects")
        if args.verify:
            _report_verify(args.name, fm)

    FA.save(fm, stem + ".json")
    FA.to_vmap(fm, stem + ".vmap", name=os.path.basename(stem))
    print(f"wrote {stem}.json and {stem}.vmap")


def _report_verify(name, fm):
    ok, total, matched, missing, extra = verify_identity(name, fm)
    if ok:
        print(f"IDENTITY OK: {matched}/{total} objects match, 0 mismatches")
    else:
        print(f"IDENTITY FAIL: {matched}/{total} match, "
              f"{sum(missing.values())} missing, {sum(extra.values())} extra")
        for k in list(missing)[:5]:
            print("   missing:", k)
        for k in list(extra)[:5]:
            print("   extra:  ", k)
    return ok


def _do_deform(name, template, zone_id, stem):
    """Build a deformed single-level map and warp one zone's pattern onto it."""
    src = OR.load_faithful(name)
    W, H = src["width"], src["height"]
    zones0, label0 = TS.segment(src["terrain"][0])
    if zone_id not in zones0:
        sys.exit(f"zone {zone_id} not on level 0 (have {sorted(zones0)})")
    ztmpl = next(z for z in template["levels"][0]["zones"] if z["zone_id"] == zone_id)
    grid = deform_terrain_level(zones0[zone_id], W, H)
    zones_d, label_d, canon_d = _segment_level(grid)
    if not zones_d:
        sys.exit("deformed terrain produced no zone")
    tzid = max(zones_d, key=lambda z: zones_d[z]["area"])  # largest deformed component
    placed, info = rebuild_zone_warp(ztmpl, zones_d[tzid], canon_d[tzid], 0)
    print(f"deform warp zone {zone_id} ({ztmpl['label']}): src={info['src']} "
          f"placed={info['placed']} dropped={info['dropped']}")
    return {"name": f"Deform-{name}", "width": W, "height": H, "twoLevel": False,
            "players": 1, "terrain": [grid], "objects": placed}


def cmd_run(args):
    name = args.name
    print(f"=== zone_engine run: {name} ===")
    tpath, template = write_template(name)
    print(f"[1/4] extracted template -> {tpath}")

    seg = os.path.join(ROOT, "out", "render", f"{slug(name)}_segmentation.png")
    _, tables = render_segmentation(name, seg)
    _print_tables(tables)
    print(f"[2/4] segmentation -> {seg}")

    stem = os.path.join(ROOT, "out", f"Rebuilt-{name.replace(' ', '_')}")
    src = OR.load_faithful(name)
    fm, stats = rebuild_map(template, src["terrain"], identity=True)
    FA.save(fm, stem + ".json")
    FA.to_vmap(fm, stem + ".vmap", name=os.path.basename(stem))
    print(f"[3/4] identity rebuild ({stats['identity']} zones, {stats['missing']} missing) "
          f"-> {stem}.vmap")
    ok = _report_verify(name, fm)

    # Honest visual: render REBUILT against the SOURCE faithful via the SAME path
    # (the .h3m read path over-draws underground sprites onto the surface).
    src_vmap = os.path.join(ROOT, "out", f"_Source-{name.replace(' ', '_')}.vmap")
    FA.to_vmap(src, src_vmap, name=f"Source-{name}")
    edit = os.path.join(ROOT, "out", "render", f"{slug(name)}_identity_editor.png")
    editor_render(stem + ".vmap", edit, compare_vmap=src_vmap)
    print(f"[4/4] realistic editor render (SOURCE | REBUILT, surface) -> {edit}")

    print("\n>>> INSPECTION CHECKPOINT <<<")
    print(f"  identity guarantee: {'PASS (bit-exact)' if ok else 'FAIL — see above'}")
    print(f"  inspect: {seg}")
    print(f"           {edit}  (the two surfaces should be visually identical)")
    print("  underground objects are covered by --verify but not by the surface render.")
    print("  next (after you confirm): rebuild --zone N --deform  (rough different-shape warp)")


def cmd_generate(args):
    if args.layout == "pp":                          # marked-point-process pipeline (spec M6)
        from vcmi_mapgen.pipeline import VcmiMapGenPipeline, PlacementWorkspace
        from vcmi_mapgen.steps import (TerrainGenStep, TileStep, SegmentStep, GateStep,
                                       GameplayStep, VegetationStep, PickupStep, RepairStep)
        from vcmi_mapgen.renderers import PngRenderer, VmapRenderer
        players = getattr(args, "players", 2)
        wmode = getattr(args, "water_mode", None) or ("none" if args.no_water else "normal")
        subterrain = getattr(args, "subterrain", False)

        workspace = PlacementWorkspace()
        pipeline = VcmiMapGenPipeline(ontology=None)
        pipeline.add_step(TerrainGenStep(size=args.size, seed=args.seed, water_mode=wmode,
                                         subterrain=subterrain))
        pipeline.add_step(TileStep())
        pipeline.add_step(SegmentStep())
        if subterrain:
            pipeline.add_step(GateStep(seed=args.seed))
        pipeline.add_step(GameplayStep(seed=args.seed, players=players, workspace=workspace))
        pipeline.add_step(VegetationStep(seed=args.seed, workspace=workspace))
        pipeline.add_step(PickupStep(seed=args.seed, workspace=workspace))
        pipeline.add_step(RepairStep(seed=args.seed, workspace=workspace))
        state = pipeline.run()
        for line in state.log:
            print(f"  {line}")

        levels = [state.cells[lvl] for lvl in sorted(state.cells)]
        objs = state.objs
        ptowns = state.player_towns
        veg_n = sum(1 for o in objs if not o.get("purpose"))
        print(f"pp-map s{args.seed} {args.size}x{args.size}: "
              f"{len(objs) - veg_n} gameplay+pickups, {veg_n} vegetation objects, "
              f"towns={len(state.player_zids)}")

        png_renderer = PngRenderer()
        objs0 = [o for o in objs if o.get("l", 0) == 0]
        base_img = png_renderer.render(state, level=0)
        png = os.path.join(ROOT, "out", "render", "pp", f"ppmap_s{args.seed}.png")
        os.makedirs(os.path.dirname(png), exist_ok=True)
        base_img.save(png)
        if subterrain:
            png1 = png_renderer.save(state, f"ppmap_s{args.seed}_L1.png", level=1)
            print(f"  {png1}")
        vmap_renderer = VmapRenderer()
        vmap = vmap_renderer.render(state, f"ppmap_s{args.seed}.vmap", name=f"pp-map s{args.seed}",
                                    teams_spec=getattr(args, "teams", "ffa"))
        if ptowns:
            print(f"  playable: {len(ptowns)} players, victory=defeat-all")
        from vcmi_mapgen import render_zone_overlay as RZO
        from PIL import Image, ImageDraw
        H0, W0 = len(levels[0]), len(levels[0][0])
        zone_fill, zone_border, draw_labels, _zones, zone_label = \
            RZO._zone_layers(base_img.size, levels[0])
        bg_t, sb_t, sv_t, solo_t = RZO._classify_objects(objs0)
        passable = RZO._compute_passable(levels[0], objs0)
        passages = RZO._passage_tiles(zone_label, passable, H0, W0)
        loot_tiles = RZO._loot_zone_tiles(_zones, zone_label, objs0, H0, W0)
        passable_p = passable - sv_t - loot_tiles
        magenta_layer, _np, _mouths = RZO._pocket_gradient_layer(
            base_img.size, passable_p, objs0, W0, H0)
        guard_tiles = [
            (ax + dx, ay + dy)
            for o in objs0 if o.get("purpose") == "GUARD"
            for ax, ay in OR.mask_interactive_cells(o["mask"], o["x"], o["y"])
            for dx, dy in [(0, 0)] + RZO._NB8
            if 0 <= ax + dx < W0 and 0 <= ay + dy < H0
        ]
        ov = Image.alpha_composite(base_img.convert("RGBA"), zone_fill)
        ov = Image.alpha_composite(ov, zone_border)
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, bg_t,      (130, 130, 130, 160)))
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, sb_t,      (  0, 200,  80, 160)))
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, sv_t,      (  0, 120,  40, 220)))
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, solo_t,    (  0, 155,  70, 220)))
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, passages,  ( 60, 140, 255, 200)))
        ov = Image.alpha_composite(ov, magenta_layer)
        ov = Image.alpha_composite(ov, RZO._fill_layer(base_img.size, guard_tiles, (220, 50, 50, 160)))
        draw_labels(ImageDraw.Draw(ov))
        ov_png = os.path.join(ROOT, "out", "render", "pp", f"ppmap_s{args.seed}_overlays.png")
        ov.convert("RGB").save(ov_png)
        _cache_path = os.path.join(ROOT, "out", "render", "pp", f"ppmap_s{args.seed}_cache.json")
        with open(_cache_path, "w") as _cf:
            json.dump({"level0": levels[0], "objs0": objs0}, _cf)
        print(f"  {png}\n  {ov_png}\n  {vmap}")


def main():
    ap = argparse.ArgumentParser(description="Shape-driven zone-rebuilding engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="map -> template JSON")
    pe.add_argument("name")
    pe.add_argument("--out", default=None)
    pe.set_defaults(func=cmd_extract)

    pro = sub.add_parser("render-ontology",
                         help="render one sprite per documented ontology item to "
                              "out/ontology/<CLUSTER>/<terrain>/<type>.png")
    pro.add_argument("--out", default=None, help="output dir (default out/ontology)")
    pro.set_defaults(func=cmd_render_ontology)

    pi = sub.add_parser("inspect", help="segmentation + labels PNG")
    pi.add_argument("name")
    pi.set_defaults(func=cmd_inspect)

    pf = sub.add_parser("features", help="per-zone feature profile (the 'understanding')")
    pf.add_argument("name")
    pf.add_argument("--zone", type=int, default=None)
    pf.add_argument("--out", default=None)
    pf.set_defaults(func=cmd_features)

    pc = sub.add_parser("reconstruct", help="rebuild a zone's objects onto a target form")
    pc.add_argument("name")
    pc.add_argument("--zone", type=int, required=True)
    pc.add_argument("--mode", choices=["transform", "features"], default="transform",
                    help="transform=shear/resample the tile map (exact on same shape); "
                         "features=generative from density+depth")
    pc.add_argument("--seed", type=int, default=0)
    pc.add_argument("--deform", action="store_true", help="add a STRETCHED (larger-grid) panel")
    pc.add_argument("--fx", type=float, default=1.4, help="grid x stretch factor")
    pc.add_argument("--fy", type=float, default=1.4, help="grid y stretch factor")
    pc.add_argument("--deco-model", choices=["binned", "quilt", "split", "all"],
                    default="all", dest="deco_model",
                    help="decoration fill model(s) for --mode features (default all = "
                         "render binned|quilt|split side by side)")
    pc.add_argument("--markov", action="store_true",
                    help="add a panel placing the model onto a FRESH Markov-generated "
                         "same-terrain patch (a new real-looking shape, not a stretch)")
    pc.add_argument("--markov-size", type=int, default=72, dest="markov_size",
                    help="W=H of the generated Markov terrain (default 72)")
    pc.add_argument("--markov-seed", type=int, default=7, dest="markov_seed",
                    help="seed for the Markov terrain generation (default 7)")
    pc.set_defaults(func=cmd_reconstruct)

    pr = sub.add_parser("rebuild", help="rebuild objects onto target terrain")
    pr.add_argument("name")
    pr.add_argument("--identity", action="store_true", help="rebuild on the source terrain")
    pr.add_argument("--verify", action="store_true", help="assert bit-exact identity")
    pr.add_argument("--zone", type=int, default=None, help="zone id for --deform")
    pr.add_argument("--deform", action="store_true", help="rough warp onto a deformed shape")
    pr.add_argument("--out", default=None, help="output stem (no extension)")
    pr.set_defaults(func=cmd_rebuild)

    prun = sub.add_parser("run", help="foundation pipeline to the inspection checkpoint")
    prun.add_argument("name")
    prun.set_defaults(func=cmd_run)

    pg = sub.add_parser("generate", help="full map synthesized by the marked-point-process pipeline")
    pg.add_argument("--seed", type=int, default=0)
    pg.add_argument("--size", type=int, default=72, help="W=H of the generated map")
    pg.add_argument("--layout", choices=["pp"], default="pp",
                    help="terrain layout generator; pp = marked-point-process "
                    "pipeline (macro zones + L3 gameplay + L2 vegetation + L4 pickups, "
                    "G2-repaired, .vmap export)")
    pg.add_argument("--no-water", action="store_true", dest="no_water",
                    help="reassign water tiles to the nearest land terrain (land-only map)")
    pg.add_argument("--players", type=int, default=2,
                    help="[pp layout] number of players; the N largest zones get start towns")
    pg.add_argument("--teams", default="ffa",
                    help="[pp layout] team matrix: 'ffa', '2v2'-style, or explicit '0,0,1,1'")
    pg.add_argument("--water-mode", choices=["none", "normal", "islands"], default=None,
                    dest="water_mode", help="[pp layout] water style")
    pg.add_argument("--subterrain", action="store_true",
                    help="[pp layout] add a second, underground level connected to the "
                         "surface by Subterranean Gate pairs")
    pg.set_defaults(func=cmd_generate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
