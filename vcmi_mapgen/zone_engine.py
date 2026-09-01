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

Reuses (no existing file modified):
  terrain_segment.segment / compute_static_features   (zones + interior-depth feature)
  kit.objects.load_faithful / exact_identity / purpose_of / mask_cells
  faithful.save / faithful.to_vmap                     (artifacts)
  kit.render_palette.TERRAIN_RGB, zone_structure.ZONE_TINT        (segmentation palette)
  render_editor.read_vmap / read_real / render_map     (realistic sprite render)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import random
import statistics
import sys

import numpy as np

from vcmi_mapgen.kit import terrain_segment as TS
from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen.kit.segmentation import _segment_level
from vcmi_mapgen.kit.terrain_lookup import TNAME, EXCLUDE_DECOR_TYPES
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import faithful as FA
from vcmi_mapgen.kit import vmap_format as VF
from vcmi_mapgen.kit.render_palette import TERRAIN_RGB as _TERRAIN_RGB
from vcmi_mapgen.kit.paths import project_root
from vcmi_mapgen.kit.tiling import _cell, tile_terrain

ROOT = project_root()

ZONE_TINT = [(200, 80, 80), (80, 160, 200), (90, 200, 120), (210, 180, 70),
             (180, 110, 200), (220, 140, 80), (120, 200, 200), (200, 120, 160),
             (150, 170, 90), (110, 130, 220)]

TILE = 9  # px per tile for the schematic segmentation render


def slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------------------------------------------------------------------
# Shape primitives (the SAME helper feeds extract and rebuild => identity holds)
# ---------------------------------------------------------------------------

def zone_bbox_mask(tiles):
    """(minx,miny,maxx,maxy), sorted bbox-relative tile list. Order-independent."""
    xs = [x for x, y in tiles]
    ys = [y for x, y in tiles]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    mask_rel = sorted((x - minx, y - miny) for x, y in tiles)
    return (minx, miny, maxx, maxy), mask_rel


def label_zone(zone, objs_in, W, H):
    """Deterministic human label: terrain + dominant gameplay purpose + map octant."""
    terr = TNAME.get(zone["terrain_type"], f"t{zone['terrain_type']}")
    purps = [OR.purpose_of(o) for o in objs_in]
    purps = [p for p in purps if p not in ("DECORATION", "UNKNOWN")]
    if purps:
        c = collections.Counter(purps)
        top = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    else:
        top = "EMPTY"
    cx, cy = zone["centroid"]
    ns = "N" if cy < H / 3 else ("S" if cy > 2 * H / 3 else "")
    ew = "W" if cx < W / 3 else ("E" if cx > 2 * W / 3 else "")
    return f"{terr}-{top}-{(ns + ew) or 'C'}"


# ---------------------------------------------------------------------------
# Extraction:  map -> template
# ---------------------------------------------------------------------------

def _bucket_objects(objects, level, zone_label, zones, W, H):
    """Split this level's objects into per-zone buckets + a barrier bucket.

    An object whose ANCHOR sits in a zone belongs to that zone (unchanged). An object
    anchored OFF every zone (on water/rock) but whose FOOTPRINT overlaps a zone — e.g.
    a rim mountain anchored on the surrounding rock — is a boundary object that belongs
    to the zone it touches most (deterministic, smallest-zone-id tie-break), so the
    patch keeps its full rim. Each object still lands in exactly one bucket, so the
    bit-exact identity rebuild is preserved (same object, same absolute position)."""
    zone_objs = {zid: [] for zid in zones}
    barrier = []
    for o in objects:
        if o.get("l", 0) != level:
            continue
        x, y = o["x"], o["y"]
        z = zone_label[y][x] if (0 <= x < W and 0 <= y < H) else -1
        if z >= 0 and z in zones:
            zone_objs[z].append(o)
            continue
        cover = collections.Counter()
        for tx, ty, _blk in OR.mask_cells(o["mask"], x, y):
            if 0 <= tx < W and 0 <= ty < H:
                zz = zone_label[ty][tx]
                if zz >= 0 and zz in zones:
                    cover[zz] += 1
        if cover:
            zone_objs[max(sorted(cover), key=lambda zid: cover[zid])].append(o)
        else:
            barrier.append(o)
    return zone_objs, barrier


def extract_template(name: str) -> dict:
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    levels_out = []
    for L, lvl in enumerate(fm["terrain"]):
        zones, zone_label, canon = _segment_level(lvl)
        zone_objs, barrier = _bucket_objects(fm["objects"], L, zone_label, zones, W, H)

        zones_out = []
        for zid in sorted(zones):
            z = zones[zid]
            bbox, mask_rel = zone_bbox_mask(z["tiles"])
            minx, miny = bbox[0], bbox[1]
            cz = canon[zid]
            objl = []
            for o in zone_objs[zid]:
                if (o["x"], o["y"]) in cz:
                    cd, cs = cz[(o["x"], o["y"])]
                else:                       # boundary object anchored off-zone: use the
                    ft = [(tx, ty) for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"])
                          if (tx, ty) in cz]   # footprint tile nearest the anchor
                    if ft:
                        bx, by = min(ft, key=lambda t: (t[0] - o["x"]) ** 2 + (t[1] - o["y"]) ** 2)
                        cd, cs = cz[(bx, by)]
                    else:
                        cd, cs = 0.0, 0.0
                objl.append({
                    "purpose": OR.purpose_of(o),
                    "identity": OR.exact_identity(o),
                    "anchor_off": [o["x"] - minx, o["y"] - miny],
                    "canon": [round(cd, 6), round(cs, 6)],
                })
            objl.sort(key=lambda e: (e["anchor_off"][1], e["anchor_off"][0],
                                     e["identity"]["type"], e["identity"]["subtype"]))
            mask_rel_l = [[dx, dy] for (dx, dy) in mask_rel]
            zones_out.append({
                "zone_id": zid,
                "terrain_type": z["terrain_type"],
                "area": z["area"],
                "bbox": list(bbox),
                "centroid": [round(z["centroid"][0], 3), round(z["centroid"][1], 3)],
                "label": label_zone(z, zone_objs[zid], W, H),
                "shape_hash": hashlib.sha1(repr(mask_rel_l).encode()).hexdigest()[:12],
                "mask_rel": mask_rel_l,
                "objects": objl,
            })

        barrier_out = [{"purpose": OR.purpose_of(o), "identity": OR.exact_identity(o),
                        "x": o["x"], "y": o["y"]}
                       for o in sorted(barrier, key=lambda o: (o["y"], o["x"], o["type"]))]
        levels_out.append({"level": L, "zones": zones_out, "barrier_objects": barrier_out})

    return {
        "name": fm["name"], "width": W, "height": H,
        "twoLevel": fm.get("twoLevel", len(fm["terrain"]) > 1),
        "players": fm.get("players", 1),
        "levels": levels_out,
    }


def write_template(name: str, out: str | None = None):
    t = extract_template(name)
    out = out or os.path.join(ROOT, "out", f"zone_template-{slug(name)}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(t, open(out, "w"))
    return out, t


# ---------------------------------------------------------------------------
# Patch library:  every same-terrain land zone as its own browsable file
# ---------------------------------------------------------------------------

PATCH_FIELDS = ["terrain", "map", "zone_id", "level", "label", "area",
                "n_objects", "n_decor", "shape_hash", "path"]


def rebuild_map(template: dict, target_terrain: list, identity: bool = False):
    """Whole-map rebuild via per-zone shape matching (identity short-circuit).

    Each template zone is matched to the target zone with the SAME (mask_rel, bbox)
    and its objects replayed at bbox_min + anchor_off (pure integer => bit-exact when
    target == source). Barrier-bucket objects are replayed at absolute coords.
    """
    objects = []
    stats = {"identity": 0, "missing": 0}
    for lvl_entry in template["levels"]:
        L = lvl_entry["level"]
        if L >= len(target_terrain):
            continue
        zones_t, _ = TS.segment(target_terrain[L])
        index = {}
        for zid, z in zones_t.items():
            bbox, mask_rel = zone_bbox_mask(z["tiles"])
            index[(tuple(mask_rel), bbox)] = bbox
        for ztmpl in lvl_entry["zones"]:
            key = (tuple((dx, dy) for dx, dy in ztmpl["mask_rel"]), tuple(ztmpl["bbox"]))
            bbox = index.get(key)
            if bbox is None:
                stats["missing"] += 1
                continue
            minx, miny = bbox[0], bbox[1]
            for obj in ztmpl["objects"]:
                objects.append({**obj["identity"],
                                "x": minx + obj["anchor_off"][0],
                                "y": miny + obj["anchor_off"][1], "l": L})
            stats["identity"] += 1
        for bo in lvl_entry["barrier_objects"]:
            objects.append({**bo["identity"], "x": bo["x"], "y": bo["y"], "l": L})

    fm = {"name": template["name"], "width": template["width"], "height": template["height"],
          "twoLevel": template.get("twoLevel", False), "players": template.get("players", 1),
          "terrain": target_terrain, "objects": objects}
    return fm, stats


def rebuild_zone_warp(ztmpl: dict, target_zone: dict, target_canon: dict, level: int):
    """Rough different-shape warp of one template zone onto one target zone.

    Each source object goes to the free target tile nearest in (depth,sweep) canonical
    space whose BLOCKING footprint stays inside the zone (decoration spill tolerated).
    """
    tiles = sorted(target_zone["tiles"], key=lambda t: (t[1], t[0]))  # stable tie-break
    arr = np.array([target_canon[(x, y)] for (x, y) in tiles], dtype=np.float64)
    tiles_set = target_zone["tiles_set"]
    used, placed, dropped = set(), [], 0
    for obj in ztmpl["objects"]:
        cd, cs = obj["canon"]
        order = np.argsort((arr[:, 0] - cd) ** 2 + (arr[:, 1] - cs) ** 2, kind="stable")
        chosen = None
        for idx in order:
            x, y = tiles[int(idx)]
            if (x, y) in used:
                continue
            if all((tx, ty) in tiles_set
                   for tx, ty, blk in OR.mask_cells(obj["identity"]["mask"], x, y) if blk):
                chosen = (x, y)
                break
        if chosen is None:
            dropped += 1
            continue
        used.add(chosen)
        placed.append({**obj["identity"], "x": chosen[0], "y": chosen[1], "l": level})
    return placed, {"placed": len(placed), "dropped": dropped, "src": len(ztmpl["objects"])}


def verify_identity(name: str, fm: dict):
    """Multiset-compare rebuilt objects to the source faithful map (all levels)."""
    src = OR.load_faithful(name)

    def k(o):
        return (o["x"], o["y"], o["l"], o.get("type"), o.get("subtype"),
                o.get("animation"), tuple(o["mask"]))

    cs = collections.Counter(k(o) for o in src["objects"])
    cr = collections.Counter(k(o) for o in fm["objects"])
    matched = sum((cs & cr).values())
    missing, extra = cs - cr, cr - cs
    return (not missing and not extra), sum(cs.values()), matched, missing, extra


# ---------------------------------------------------------------------------
# Feature understanding + generative reconstruction (rules-as-code)
#
# Instead of replaying recorded positions, we (1) summarize each zone into a
# feature profile (per-purpose density + where-in-the-shape it sits + which
# concrete objects it uses), then (2) generate placement from that profile.
# Count = density x area, so deforming the zone yields MORE or FEWER objects to
# achieve the SAME result; positions come from each purpose's depth signature.
# ---------------------------------------------------------------------------

# Placement priority: anchors and large gameplay first, decoration (the walls) last.
_PRIORITY = {"TOWN": 0, "BANK": 1, "DWELLING": 2, "QUEST_GATE": 3, "MINE": 4,
             "TRANSPORT": 5, "WATER_TRANSPORT": 5, "STAT_PERMANENT": 6, "SPELL_SKILL": 6,
             "BONUS_TEMP": 6, "MANA": 6, "INFO": 7, "TERRAIN_MODIFIER": 7,
             "RESOURCE_PILE": 8, "REWARD_PICKUP": 9, "GUARD": 10, "DECORATION": 99}


def _prio(p):
    return _PRIORITY.get(p, 50)


def zone_features(zone, objs, canon_zone):
    """Summarize ONE zone into a feature profile (the 'understanding').

    Per purpose: count, density (per tile), depth signature (mu/sd of interior
    depth = where in the shape it sits), typical within-purpose spacing, and the
    concrete object identities it uses (so a rebuild reuses the same kinds)."""
    area = zone["area"]
    by_p = collections.defaultdict(list)
    for o in objs:
        d, s = canon_zone[(o["x"], o["y"])]
        by_p[OR.purpose_of(o)].append((o, d, s))

    purposes = {}
    for p, items in by_p.items():
        depths = [d for _, d, _ in items]
        pts = [(o["x"], o["y"]) for o, _, _ in items]
        purposes[p] = {
            "count": len(items),
            "density": round(len(items) / area, 5),
            "depth_mu": round(statistics.fmean(depths), 4),
            "depth_sd": round(statistics.pstdev(depths) if len(depths) > 1 else 0.0, 4),
            "spacing": round(_median_nn(pts), 2),
            "identities": _dedup_identities(o for o, _, _ in items),
        }

    # guard<->reward coupling: median distance from each GUARD to nearest reward/loot.
    guards = [(o["x"], o["y"]) for o, _, _ in by_p.get("GUARD", [])]
    loot = [(o["x"], o["y"]) for pp in ("REWARD_PICKUP", "RESOURCE_PILE", "BANK")
            for o, _, _ in by_p.get(pp, [])]
    coupling = None
    if guards and loot:
        coupling = round(statistics.fmean(
            min(abs(gx - lx) + abs(gy - ly) for lx, ly in loot) for gx, gy in guards), 2)

    return {"zone_id": zone.get("_zid"), "terrain": zone["terrain_type"], "area": area,
            "guard_loot_dist": coupling, "purposes": purposes}


def _median_nn(pts):
    if len(pts) < 2:
        return 0.0
    ds = []
    for i, (x, y) in enumerate(pts):
        ds.append(min(abs(x - ox) + abs(y - oy)
                      for j, (ox, oy) in enumerate(pts) if j != i))
    return statistics.median(ds)


def _dedup_identities(objs):
    """Unique identities with a frequency weight (for seeded variety on rebuild)."""
    c = collections.Counter()
    store = {}
    for o in objs:
        ident = OR.exact_identity(o)
        key = (ident["type"], ident["subtype"], ident["animation"], tuple(ident["mask"]))
        c[key] += 1
        store[key] = ident
    return [{"identity": store[k], "weight": w} for k, w in c.most_common()]


def _pick_identity(entry_list, rng):
    ids = [e["identity"] for e in entry_list]
    ws = [max(e["weight"], 1) for e in entry_list]
    return rng.choices(ids, weights=ws, k=1)[0]


OVERLAY_PROB = 0.28   # chance a placed decoration also gets a non-blocking flora overlay (stacking)
_OVERLAY_IDENTS = {}  # terr_id -> [non-blocking small flora identities] (ontology, cached)


def _overlay_ident(terr_id, rng):
    """A non-blocking small flora identity for an overlay stack on ``terr_id`` (ontology), or None."""
    if terr_id not in _OVERLAY_IDENTS:
        _OVERLAY_IDENTS[terr_id] = ON.decor_pool(terr_id, blocking=False, max_cells=4,
                                                 exclude_types=EXCLUDE_DECOR_TYPES)
    pool = _OVERLAY_IDENTS[terr_id]
    return rng.choice(pool) if pool else None


def _stack_decor(placed, ident, t, level, terr_id, rng):
    """Place a decoration base at tile ``t`` and, per :data:`OVERLAY_PROB`, a non-blocking flora
    overlay on top (a 2-high stack) — both as separate DECORATION dicts (the renderer paint-sorts
    them by (l, y, x) so the overlay draws over the base)."""
    placed.append({**ident, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})
    if rng.random() < OVERLAY_PROB:
        ov = _overlay_ident(terr_id, rng)
        if ov and ov.get("animation") != ident.get("animation"):
            placed.append({**ov, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})


# ---------------------------------------------------------------------------
# Decoration as a density FIELD over the shape-intrinsic frame.
#
# The look of a zone's decoration (a thin constant-thickness mountain RIM + a
# sparse INTERIOR) is a function of interior-depth, not of zone size. A single
# global density is biased HIGH by the rim (rim objects ~ perimeter ~ linear in
# size; interior ~ area ~ quadratic) so applying it to a larger interior floods
# it = clutter. Binning density by depth makes it resolution-stable: the rim
# stays a wall, the interior stays sparse, at any target size.
# ---------------------------------------------------------------------------

DEPTH_BINS = 6


def _depth_bin(d, K=DEPTH_BINS):
    return min(int(d * K), K - 1)


def _stochastic_round(x, rng):
    """Integer count whose expectation is x (seeded, expectation-preserving)."""
    n = int(x)
    return n + (1 if rng.random() < (x - n) else 0)


def _obj_canon(o, canon_zone, tiles_set):
    """Shape-intrinsic (depth, sweep) for an object: the RIM-MOST zone tile its
    footprint overlaps (min depth). Rim mountains are anchored OUTSIDE the zone
    (on neighbour/rock tiles) and gathered by footprint overlap, so anchor-canon
    would miss them — overlap-canon classifies them as the rim (depth~0)."""
    best = None
    for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]):
        if (tx, ty) in tiles_set:
            d, s = canon_zone[(tx, ty)]
            if best is None or d < best[0]:
                best = (d, s)
    if best is not None:
        return best
    if (o["x"], o["y"]) in canon_zone:
        return canon_zone[(o["x"], o["y"])]
    return (0.0, 0.0)


def _tiles_by_depth_bin(canon_zone, tiles_set, K=DEPTH_BINS):
    """(tile_hist[k], tiles_in_bin[k]) for a zone's tiles (deterministic order)."""
    hist = [0] * K
    by_bin = [[] for _ in range(K)]
    for t in sorted(tiles_set):
        k = _depth_bin(canon_zone[t][0], K)
        hist[k] += 1
        by_bin[k].append(t)
    return hist, by_bin


def decor_bins(decor_objs, canon_zone, tiles_set, K=DEPTH_BINS):
    """Per-depth-bin density / spacing / identities for DECORATION (the 'look').

    dens[k]    = objects-in-bin / zone-tiles-in-bin  (resolution-stable areal density)
    spacing[k] = within-bin median nearest-neighbour (>=1.0; rim packs at ~1)
    identities[k] = the kinds that sat at that depth (rim->mountains, core->trees)
    """
    hist, _ = _tiles_by_depth_bin(canon_zone, tiles_set, K)
    obj_bins = [[] for _ in range(K)]
    for o in decor_objs:
        d, _s = _obj_canon(o, canon_zone, tiles_set)
        obj_bins[_depth_bin(d, K)].append(o)
    dens, spacing, idents = [], [], []
    for k in range(K):
        pts = [(o["x"], o["y"]) for o in obj_bins[k]]
        dens.append(len(obj_bins[k]) / hist[k] if hist[k] else 0.0)
        spacing.append(_median_nn(pts) if len(pts) >= 2 else 1.0)
        idents.append(_dedup_identities(obj_bins[k]))
    glob = _dedup_identities(decor_objs)
    return {"K": K, "tile_hist": hist, "dens": dens, "spacing": spacing,
            "identities": idents, "global_identities": glob}


def _bin_pool(prof, k):
    """Identity pool for bin k, falling back outward then to the global pool."""
    idents = prof["identities"]
    K = len(idents)
    if idents[k]:
        return idents[k]
    for r in range(1, K):
        for kk in (k - r, k + r):
            if 0 <= kk < K and idents[kk]:
                return idents[kk]
    return prof["global_identities"]


def _place_decor_cells(cells_by_key, key_dens, key_spacing, key_pool, tgt_tiles,
                       hard_block, used, rng, level=0, catfield=None, bbox=None,
                       terr_id=None, stack=False):
    """Shared decoration placer: for each spatial key (a depth bin, or a
    depth x sweep cell) put n = dens * |target tiles in cell| objects, weighted
    uniformly within the cell and min-spaced by the cell's own grain. Never buries
    gameplay (skips stamps whose blocking footprint hits hard_block). Rim first
    (lowest depth bin) so the wall is laid before interior fill competes for tiles.

    If ``catfield`` (+ ``bbox`` origin + ``terr_id``) is given, the sprite at each tile is drawn
    from that tile's coherent CATEGORY (an ontology decode) rather than independently from the
    pool, so the belt reads as same-kind stretches. ``stack=True`` adds a non-blocking overlay."""
    placed = []
    mnx, mny = bbox if bbox else (0, 0)
    for key in sorted(cells_by_key, key=lambda kk: (kk[0] if isinstance(kk, tuple) else kk)):
        tiles = [t for t in cells_by_key[key] if t not in used]
        if not tiles:
            continue
        dens = key_dens.get(key, 0.0)
        if dens <= 0:
            continue
        n = _stochastic_round(dens * len(cells_by_key[key]), rng)
        if n <= 0:
            continue
        min_sep = max(1.0, key_spacing.get(key, 1.0))
        chosen = _weighted_spaced(tiles, [1.0] * len(tiles), n, min_sep, rng,
                                  decoration=False)
        pool = key_pool(key)
        if not pool:
            continue
        for (x, y) in chosen:
            ident = None
            if catfield is not None:
                cat = catfield[y - mny][x - mnx]
                ident = ON.decode_identity(cat, terr_id, rng) if cat else None
            if ident is None:
                ident = _pick_identity(pool, rng)
            cells = [(cx, cy) for cx, cy, blk in OR.mask_cells(ident["mask"], x, y) if blk]
            if any(c in hard_block for c in cells):
                continue              # VCMI: decoration must not bury gameplay
            if stack and terr_id is not None:
                _stack_decor(placed, ident, (x, y), level, terr_id, rng)
            else:
                placed.append({**ident, "x": x, "y": y, "l": level, "_purpose": "DECORATION"})
            used.add((x, y))
    return placed


SWEEP_BINS = 8


def deco_binned(src_decor, src_canon, src_tiles, tgt_zone, tgt_canon, hard_block,
                seed, level=0, K=DEPTH_BINS):
    """Model A — depth-binned density field. Density per interior-depth band is
    resolution-stable, so the rim stays a constant-thickness wall and the interior
    stays sparse at any size. Radially faithful; ignores angular (sweep) structure."""
    rng = random.Random(seed)
    prof = decor_bins(src_decor, src_canon, src_tiles, K)
    tgt = tgt_zone["tiles_set"]
    _hist, by_bin = _tiles_by_depth_bin(tgt_canon, tgt, K)
    cells = {k: by_bin[k] for k in range(K) if by_bin[k]}
    key_dens = {k: prof["dens"][k] for k in range(K)}
    key_spacing = {k: prof["spacing"][k] for k in range(K)}
    return _place_decor_cells(cells, key_dens, key_spacing,
                              lambda k: _bin_pool(prof, k), tgt, hard_block,
                              set(), rng, level)


def deco_quilt(src_decor, src_canon, src_tiles, tgt_zone, tgt_canon, hard_block,
               seed, level=0, K=DEPTH_BINS, S=SWEEP_BINS):
    """Model B — density field over (depth x sweep) cells (quilt-lite). Preserves
    BOTH radial and angular texture, so a one-sided rim or a clump that only sits
    on the north edge stays where it was. Falls back to the depth-bin pool where a
    cell is empty."""
    rng = random.Random(seed)
    binprof = decor_bins(src_decor, src_canon, src_tiles, K)

    def cell_of(d, s):
        return (_depth_bin(d, K), min(int(s * S), S - 1))

    src_hist = collections.Counter(cell_of(*src_canon[t]) for t in src_tiles)
    obj_cells = collections.defaultdict(list)
    for o in src_decor:
        d, s = _obj_canon(o, src_canon, src_tiles)
        obj_cells[cell_of(d, s)].append(o)
    key_dens, key_spacing, key_ident = {}, {}, {}
    for key, cnt in src_hist.items():
        objs = obj_cells.get(key, [])
        key_dens[key] = len(objs) / cnt if cnt else 0.0
        pts = [(o["x"], o["y"]) for o in objs]
        key_spacing[key] = _median_nn(pts) if len(pts) >= 2 else 1.0
        key_ident[key] = _dedup_identities(objs)

    tgt = tgt_zone["tiles_set"]
    cells = collections.defaultdict(list)
    for t in sorted(tgt):
        cells[cell_of(*tgt_canon[t])].append(t)

    def pool(key):
        return key_ident.get(key) or _bin_pool(binprof, key[0])

    return _place_decor_cells(cells, key_dens, key_spacing, pool, tgt, hard_block,
                              set(), rng, level)


def deco_split(src_decor, src_canon, src_tiles, tgt_zone, tgt_canon, hard_block,
               seed, level=0, K=DEPTH_BINS):
    """Model C — wall/field structural split. The packed rim bins (source density
    >= 0.5) are a WALL: re-laid as a continuous constant-thickness band along the
    target rim. The rest are FIELDS: scattered at the source's interior density.
    Most explicit wall continuity."""
    rng = random.Random(seed)
    prof = decor_bins(src_decor, src_canon, src_tiles, K)
    wall_k = 0
    for k in range(K):
        if prof["dens"][k] >= 0.5:
            wall_k = k + 1
        else:
            break
    tgt = tgt_zone["tiles_set"]
    _hist, by_bin = _tiles_by_depth_bin(tgt_canon, tgt, K)
    used, placed = set(), []
    if wall_k > 0:
        wall_pool = [e for k in range(wall_k) for e in prof["identities"][k]] \
            or prof["global_identities"]
        wall_tiles = sorted(t for k in range(wall_k) for t in by_bin[k])
        placed += _place_decor_cells({0: wall_tiles}, {0: 1.0}, {0: 1.0},
                                     lambda key: wall_pool, tgt, hard_block, used,
                                     rng, level)
    cells = {k: by_bin[k] for k in range(wall_k, K) if by_bin[k]}
    key_dens = {k: prof["dens"][k] for k in range(wall_k, K)}
    key_spacing = {k: prof["spacing"][k] for k in range(wall_k, K)}
    placed += _place_decor_cells(cells, key_dens, key_spacing,
                                 lambda k: _bin_pool(prof, k), tgt, hard_block,
                                 used, rng, level)
    return placed


def _stretch_gameplay(src_zone, objs, tgt_zone, seed):
    """Place gameplay (non-decoration) on the target via the existing forward-map
    stretch (rigid one-tile, no overlap). Returns (gameplay, hard_block) so a
    decoration model can avoid burying it. Shared by all models for a fair compare."""
    gp_all, _ = transform_zone(src_zone, objs, tgt_zone, level=0, seed=seed)
    gameplay = [o for o in gp_all if o["_purpose"] != "DECORATION"]
    hard = set()
    tgt = tgt_zone["tiles_set"]
    for o in gameplay:
        for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk and (cx, cy) in tgt:
                hard.add((cx, cy))
    return gameplay, hard


def _nearest_free(pt, tile_set, used):
    """Nearest tile in tile_set not in used, by expanding Manhattan rings."""
    x0, y0 = pt
    for r in range(0, 80):
        for dx in range(-r, r + 1):
            rem = r - abs(dx)
            for dy in ({-rem, rem} if rem else {0}):
                t = (x0 + dx, y0 + dy)
                if t in tile_set and t not in used:
                    return t
    for t in sorted(tile_set):
        if t not in used:
            return t
    return pt


def _components(tiles):
    """4-connected components of a tile set, largest first."""
    seen, comps = set(), []
    for t in tiles:
        if t in seen:
            continue
        comp, dq = [], collections.deque([t])
        seen.add(t)
        while dq:
            x, y = dq.popleft()
            comp.append((x, y))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in tiles and n not in seen:
                    seen.add(n)
                    dq.append(n)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def _stretch_traversable(tgt_set, blocked, hard_placed):
    """Gate: open (walkable) space must stay one connected region reaching every
    rigid object. Returns (ok, reason)."""
    walkable = set(tgt_set) - set(blocked)
    if not walkable:
        return False, "no walkable space left"
    main = set(_components(walkable)[0])
    frac = len(main) / len(walkable)
    if frac < 0.6:
        return False, f"open space fragmented (largest patch {frac:.0%} of walkable)"
    for o in hard_placed:
        adj = set()
        for tx, ty, _ in OR.mask_cells(o["mask"], o["x"], o["y"]):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                adj.add((tx + dx, ty + dy))
        if not (adj & main):
            return False, f"{o.get('_purpose')} at ({o['x']},{o['y']}) walled off"
    return True, "ok"


def transform_zone(src_zone, src_objs, tgt_zone, level=0, seed=0):
    """Stretch a zone like a WIREFRAME by TRANSFORMING ONLY THE OBJECTS THAT EXIST —
    nothing is added. Every object is repositioned by the bbox-affine, so the ring of
    mountains/lava stays a coherent ring (just stretched) and the relative arrangement
    is preserved. Rigid parts (guards/mines/gates/visitable) keep one tile and never
    overlap; flexible decoration may overlap. The empty space stretches with the form.
    The open space is then checked for traversability and the shape is REJECTED if it
    breaks. Identical form => exact replay.

    Returns (placed, report).
    """
    src_set, tgt_set = src_zone["tiles_set"], tgt_zone["tiles_set"]
    if set(tgt_set) == set(src_set):
        placed = [{**OR.exact_identity(o), "x": o["x"], "y": o["y"], "l": level,
                   "_purpose": OR.purpose_of(o)} for o in src_objs]
        return placed, {"ok": True, "mode": "identity", "reason": "ok"}

    (sx0, sy0, sx1, sy1), _ = zone_bbox_mask(src_zone["tiles"])
    (tx0, ty0, tx1, ty1), _ = zone_bbox_mask(tgt_zone["tiles"])
    sw, sh = max(sx1 - sx0, 1), max(sy1 - sy0, 1)
    tw, th = max(tx1 - tx0, 1), max(ty1 - ty0, 1)
    hard_objs = [o for o in src_objs if OR.purpose_of(o) != "DECORATION"]
    deco_objs = [o for o in src_objs if OR.purpose_of(o) == "DECORATION"]

    # "Stretch" = the SAME objects at the same relative placement on a LARGER GRID
    # (positions scale, object footprints do NOT — VCMI objects are fixed-size tiles).
    # The empty space between objects therefore grows.
    def fwd(o):
        return (tx0 + round((o["x"] - sx0) / sw * tw),
                ty0 + round((o["y"] - sy0) / sh * th))

    # rigid gameplay: one tile, never overlapping other gameplay (VCMI)
    placed, used, hard_block = [], set(), set()
    for o in sorted(hard_objs, key=lambda o: (o["y"], o["x"])):
        ident = OR.exact_identity(o)
        tx, ty = fwd(o)
        if (tx, ty) not in tgt_set or (tx, ty) in used:
            tx, ty = _nearest_free((tx, ty), tgt_set, used)
        used.add((tx, ty))
        placed.append({**ident, "x": tx, "y": ty, "l": level, "_purpose": OR.purpose_of(o)})
        for cx, cy, blk in OR.mask_cells(ident["mask"], tx, ty):
            if blk and (cx, cy) in tgt_set:
                hard_block.add((cx, cy))

    # decoration: same relative placement, scaled; VCMI = may overlap decoration but
    # must NOT bury gameplay or sit off valid terrain; snap inside the zone if needed.
    deco_block = set()
    for o in sorted(deco_objs, key=lambda o: (o["y"], o["x"])):
        ident = OR.exact_identity(o)
        tx, ty = fwd(o)
        if (tx, ty) not in tgt_set:
            tx, ty = _nearest_free((tx, ty), tgt_set, set())
        cells = [(cx, cy) for cx, cy, blk in OR.mask_cells(ident["mask"], tx, ty) if blk]
        if any(c in hard_block for c in cells):
            continue                      # VCMI: don't bury a gameplay sprite
        placed.append({**ident, "x": tx, "y": ty, "l": level, "_purpose": "DECORATION"})
        deco_block.update(c for c in cells if c in tgt_set)

    ok, reason = _stretch_traversable(tgt_set, hard_block | deco_block,
                                      [o for o in placed if o["_purpose"] != "DECORATION"])
    report = {"ok": ok, "mode": "stretch", "reason": reason,
              "counts": collections.Counter(o["_purpose"] for o in placed)}
    return placed, report


def _weighted_spaced(tiles, weights, n, min_sep, rng, decoration=False):
    """Pick ~n tiles by weight, keeping a min separation (decoration may pack)."""
    if not tiles or n <= 0:
        return []
    chosen = []
    pool = list(zip(tiles, weights))
    attempts = 0
    sep2 = min_sep * min_sep
    while len(chosen) < n and pool and attempts < n * 40:
        attempts += 1
        ws = [w for _, w in pool]
        tot = sum(ws)
        if tot <= 0:
            (tx, ty), _ = pool[rng.randrange(len(pool))]
        else:
            r = rng.random() * tot
            acc = 0.0
            for i, (_, w) in enumerate(pool):
                acc += w
                if acc >= r:
                    (tx, ty) = pool[i][0]
                    break
        if decoration or all((tx - cx) ** 2 + (ty - cy) ** 2 >= sep2 for cx, cy in chosen):
            chosen.append((tx, ty))
            pool = [(t, w) for (t, w) in pool if t != (tx, ty)]
    return chosen


def extract_features(name: str) -> dict:
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    levels_out = []
    for L, lvl in enumerate(fm["terrain"]):
        zones, zone_label, canon = _segment_level(lvl)
        zone_objs, _ = _bucket_objects(fm["objects"], L, zone_label, zones, W, H)
        zlist = []
        for zid in sorted(zones):
            z = dict(zones[zid]); z["_zid"] = zid
            prof = zone_features(z, zone_objs[zid], canon[zid])
            prof["label"] = label_zone(zones[zid], zone_objs[zid], W, H)
            zlist.append(prof)
        levels_out.append({"level": L, "zones": zlist})
    return {"name": fm["name"], "width": W, "height": H, "levels": levels_out}


def write_features(name, out=None):
    f = extract_features(name)
    out = out or os.path.join(ROOT, "out", f"zone_features-{slug(name)}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(f, open(out, "w"), indent=1)
    return out, f


# ---------------------------------------------------------------------------
# Deform demo terrain (deterministic, no rng)
# ---------------------------------------------------------------------------

_MARKOV_MODEL = {}


def markov_terrain_level(W, H, seed):
    """A fresh surface-terrain grid sampled from the corpus Markov chain (raster
    sample + isotropic Gibbs smoothing for coherent patches). Cells carry view
    variety so they render. The learned model is cached per process."""
    import markov_terrain as MT
    if "m" not in _MARKOV_MODEL:
        _MARKOV_MODEL["m"] = (MT.learn(0), MT.learn4(0))
    M, M4 = _MARKOV_MODEL["m"]
    rnd = random.Random(seed)
    g = MT.generate(M, W, H, rnd)
    MT.gibbs(g, M4, M["marg"], rnd, sweeps=6)
    # `g` is an int terrain-id grid; route it through the corpus-learned tiler so
    # terrain seams (shores especially) get real transition/dither views instead of
    # flat interior frames — otherwise water borders render as hard square edges.
    return tile_terrain(g, W, H)


def deform_terrain_level(src_terr, zone, W, H, fx=1.3, fy=1.3):
    """Stretch the zone into a bigger form (nearest-resize of its filled mask, so it
    stays connected and keeps its shape), RESAMPLING the real source terrain cells
    (view/mirror) so the ground matches the original instead of a flat canvas."""
    from PIL import Image
    (minx, miny, maxx, maxy), _ = zone_bbox_mask(zone["tiles"])
    w, h = maxx - minx + 1, maxy - miny + 1
    m = Image.new("L", (w, h), 0)
    mp = m.load()
    for (x, y) in zone["tiles"]:
        mp[x - minx, y - miny] = 255
    nw, nh = max(1, round(w * fx)), max(1, round(h * fy))
    m2 = m.resize((nw, nh), Image.NEAREST).load()
    grid = [[_cell(TS.ROCK, x, y) for x in range(W)] for y in range(H)]  # rock backdrop
    cx, cy = zone["centroid"]
    ox = min(max(int(cx - nw / 2), 0), max(W - nw, 0))
    oy = min(max(int(cy - nh / 2), 0), max(H - nh, 0))
    t = zone["terrain_type"]
    for yy in range(nh):
        for xx in range(nw):
            X, Y = ox + xx, oy + yy
            if not m2[xx, yy] or not (0 <= X < W and 0 <= Y < H):
                continue
            sx = minx + min(int(xx / nw * w), w - 1)   # inverse-map to a source cell
            sy = miny + min(int(yy / nh * h), h - 1)
            sc = src_terr[sy][sx]
            grid[Y][X] = {"t": t, "view": sc.get("view", 0), "rt": sc.get("rt", 0),
                          "rd": sc.get("rd", 0), "ot": 0, "od": 0, "m": sc.get("m", 0)}
    return grid


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


def _print_tables(tables):
    for L, table in tables:
        print(f"\n  level {L}: {len(table)} zones")
        for zid, lab, area, nobj in table:
            print(f"    zone {zid:>2}  {lab:<22} area={area:<4} objs={nobj}")


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
