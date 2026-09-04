"""The identity-rebuild engine: per-zone templates in a shape-relative frame, bit-exact
same-shape replay (pure integer math — the identity guarantee), and warp adaptation of a
zone's objects onto a differently-shaped target (rigid gameplay, decoration models,
feature-driven reconstruction)."""
import collections
import hashlib
import json
import math
import os
import random
import statistics

import numpy as np

from vcmi_mapgen.kit import terrain_segment as TS
from vcmi_mapgen.kit import objects as OR
from vcmi_mapgen.kit.segmentation import _segment_level
from vcmi_mapgen.kit.terrain_lookup import TNAME, EXCLUDE_DECOR_TYPES
from vcmi_mapgen import ontology as ON
from vcmi_mapgen.kit.paths import project_root, slug
from vcmi_mapgen.kit.tiling import _cell, tile_terrain

ROOT = project_root()


def slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


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
