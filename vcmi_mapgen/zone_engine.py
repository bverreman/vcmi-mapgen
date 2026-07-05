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
  obj_resolve.load_faithful / exact_identity / purpose_of / mask_cells
  faithful.save / faithful.to_vmap                     (artifacts)
  render.TERRAIN_RGB, zone_structure.ZONE_TINT         (segmentation palette)
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terrain_segment as TS
import obj_resolve as OR
import ontology as ON
import faithful as FA
import render as RD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# terrain code -> human name (for labels); water/rock never form a zone.
TNAME = {0: "dirt", 1: "sand", 2: "grass", 3: "snow", 4: "swamp",
         5: "rough", 6: "subterr", 7: "lava", 8: "water", 9: "rock"}

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


def canonical_coords(zones, depth_arr):
    """Per-zone shape-intrinsic (depth, sweep) for every tile.

    depth = per-zone-renormalized BFS-to-boundary (0 edge .. 1 core); channel-20 is
            already /sqrt(area), so we renormalize to the zone's own [min,max].
    sweep = atan2(y-cy, x-cx) normalized to [0,1) (a cheap angular address).
    """
    out = {}
    for zid, z in zones.items():
        tiles = z["tiles"]
        vals = [float(depth_arr[y, x]) for (x, y) in tiles]
        vmin, vmax = min(vals), max(vals)
        rng = vmax - vmin
        cx, cy = z["centroid"]
        m = {}
        for (x, y) in tiles:
            raw = float(depth_arr[y, x])
            depth = 0.5 if rng < 1e-9 else (raw - vmin) / rng
            sweep = (math.atan2(y - cy, x - cx) + math.pi) / (2 * math.pi)
            m[(x, y)] = (depth, sweep)
        out[zid] = m
    return out


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

def _segment_level(lvl):
    """segment + per-zone canonical coords for one terrain level."""
    zones, zone_label = TS.segment(lvl, subdivide=False)
    feats = TS.compute_static_features(lvl, zones, zone_label)
    canon = canonical_coords(zones, feats[:, :, 20])
    return zones, zone_label, canon


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


def _touch_index(zones, W, H):
    """tile (x,y) -> {zone ids whose tiles or 4-neighbourhood cover it}. A footprint
    tile hitting this index means the object overlaps or borders that zone."""
    idx = collections.defaultdict(set)
    for zid, z in zones.items():
        for (x, y) in z["tiles"]:
            idx[(x, y)].add(zid)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H:
                    idx[(nx, ny)].add(zid)
    return idx


def _zone_canon(o, cz, tiles):
    """(depth,sweep) for an object relative to a zone: its anchor's canon if the anchor
    is in the zone, else the canon of the nearest footprint tile in the zone, else the
    canon of the nearest zone tile (adjacency-only boundary object)."""
    key = (o["x"], o["y"])
    if key in cz:
        return cz[key]
    ft = [(tx, ty) for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"]) if (tx, ty) in cz]
    pool = ft if ft else list(tiles)
    bx, by = min(pool, key=lambda t: (t[0] - o["x"]) ** 2 + (t[1] - o["y"]) ** 2)
    return cz[(bx, by)]


def extract_patches(name: str) -> dict:
    """Per-zone SELF-CONTAINED patches for the library: every object whose footprint
    OVERLAPS or BORDERS a zone is part of that zone's patch — so rim mountains, edge
    mines and boundary decoration that are *anchored in a neighbouring zone* are kept.
    Objects are therefore SHARED across adjacent patches by design. This is distinct
    from extract_template's identity buckets, which keep each object exactly once for
    the bit-exact rebuild. Zone shape metadata + labels match extract_template."""
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    levels_out = []
    for L, lvl in enumerate(fm["terrain"]):
        zones, zone_label, canon = _segment_level(lvl)
        anchor_objs, _ = _bucket_objects(fm["objects"], L, zone_label, zones, W, H)
        idx = _touch_index(zones, W, H)
        touch = collections.defaultdict(list)
        for o in fm["objects"]:
            if o.get("l", 0) != L:
                continue
            hit = set()
            for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if (tx, ty) in idx:
                    hit |= idx[(tx, ty)]
            for zid in hit:
                touch[zid].append(o)

        zones_out = []
        for zid in sorted(zones):
            z = zones[zid]
            bbox, mask_rel = zone_bbox_mask(z["tiles"])
            minx, miny = bbox[0], bbox[1]
            cz = canon[zid]
            objl = []
            for o in touch[zid]:
                cd, cs = _zone_canon(o, cz, z["tiles"])
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
                "label": label_zone(z, anchor_objs[zid], W, H),
                "shape_hash": hashlib.sha1(repr(mask_rel_l).encode()).hexdigest()[:12],
                "mask_rel": mask_rel_l,
                "objects": objl,
            })
        levels_out.append({"level": L, "zones": zones_out})
    return {"name": fm["name"], "width": W, "height": H, "levels": levels_out}


def write_patches(names, out_dir: str | None = None):
    """One self-contained JSON per land zone, grouped by map; collect manifest rows.

    Uses extract_patches: each patch keeps every object overlapping OR bordering its
    zone (full rims), shared across adjacent patches. Each patch carries a provenance
    header so it traces back to its source map; per-tile canon is omitted because it is
    derivable from mask_rel. Returns (rows, paths).
    """
    out_dir = out_dir or os.path.join(ROOT, "out", "patches")
    rows, paths = [], []
    for name in names:
        t = extract_patches(name)
        mslug = slug(name)
        map_dir = os.path.join(out_dir, mslug)
        os.makedirs(map_dir, exist_ok=True)
        for lvl_entry in t["levels"]:
            L = lvl_entry["level"]
            for z in lvl_entry["zones"]:
                patch = {
                    "source_map": t["name"],
                    "source_slug": mslug,
                    "level": L,
                    "zone_id": z["zone_id"],
                    "terrain_type": z["terrain_type"],
                    "terrain_name": TNAME.get(z["terrain_type"], str(z["terrain_type"])),
                    "area": z["area"],
                    "bbox": z["bbox"],
                    "centroid": z["centroid"],
                    "label": z["label"],
                    "shape_hash": z["shape_hash"],
                    "mask_rel": z["mask_rel"],
                    "objects": z["objects"],
                }
                fname = f"z{z['zone_id']}_L{L}__{slug(z['label'])}.json"
                fpath = os.path.join(map_dir, fname)
                json.dump(patch, open(fpath, "w"))
                paths.append(fpath)
                n_decor = sum(1 for o in z["objects"] if o["purpose"] == "DECORATION")
                rows.append({
                    "terrain": patch["terrain_name"],
                    "map": t["name"],
                    "zone_id": z["zone_id"],
                    "level": L,
                    "label": z["label"],
                    "area": z["area"],
                    "n_objects": len(z["objects"]),
                    "n_decor": n_decor,
                    "shape_hash": z["shape_hash"],
                    "path": os.path.relpath(fpath, out_dir),
                })
    return rows, paths


def write_manifest(rows, out_dir: str | None = None):
    """index.csv sorted by (terrain, map, level, zone_id) so terrains group together."""
    import csv
    out_dir = out_dir or os.path.join(ROOT, "out", "patches")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.csv")
    ordered = sorted(rows, key=lambda r: (r["terrain"], r["map"], r["level"], r["zone_id"]))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PATCH_FIELDS)
        w.writeheader()
        w.writerows(ordered)
    return path


# ---------------------------------------------------------------------------
# Rebuild:  template + target terrain -> faithful map
# ---------------------------------------------------------------------------

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
        zones_t, _ = TS.segment(target_terrain[L], subdivide=False)
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


def _gauss(d, mu, sd):
    sd = max(sd, 0.06)
    return math.exp(-0.5 * ((d - mu) / sd) ** 2)


def _pick_identity(entry_list, rng):
    ids = [e["identity"] for e in entry_list]
    ws = [max(e["weight"], 1) for e in entry_list]
    return rng.choices(ids, weights=ws, k=1)[0]


def _filter_terrain(pool, terr_id):
    """Keep only decoration identities native to ``terr_id`` per the ONTOLOGY (or terrain-
    independent 'land'/'water'). Unknown identities are kept; if filtering empties the pool keep
    the original (never break placement). ``pool`` is a list of {identity, weight} entries."""
    if not pool:
        return pool
    name = TNAME.get(terr_id)
    generic = "water" if name == "water" else "land"

    def ok(e):
        ts = ON.terrains_of((e.get("identity") or {}).get("animation") or "")
        return (not ts) or (name in ts) or (generic in ts)
    out = [e for e in pool if ok(e)]
    return out or pool


def _enriched_decor_pool(learned, terr_id, full_fn):
    """The decoration identity pool used by the grammar (region/markov/…) fill.

    The learned ``veg``/``wall`` pools are harvested per (terrain, role) from the corpus,
    so they (a) carry cross-terrain contamination — decoration that merely sat on a
    border of a patch of this terrain — and (b) are weighted by raw corpus frequency,
    which buries the long tail so only a handful of common sprites ever show. Three
    corrections, so a generated zone actually exercises the FULL per-terrain decoration
    ontology rather than the corpus's favourites:

      * terrain purity — drop identities whose animation never appears on ``terr_id``
        (``_filter_terrain``; terrain-neutral rocks survive);
      * full coverage — union in EVERY terrain-native decoration from the authoritative
        per-terrain pool (``full_fn`` = ``_veg_idents`` / ``_interior_idents`` / …), keyed
        by animation so nothing already present is duplicated;
      * variety — compress the corpus weights (``w -> 1 + ln(1+w)``) and floor the
        ontology tail at a real weight, so common sprites still lead but the rest of the
        catalog genuinely appears.
    """
    out, seen = [], set()
    for e in _filter_terrain(learned or [], terr_id):
        anim = (e.get("identity") or {}).get("animation")
        if _is_excluded_anim(anim):
            continue                       # learned corpus idents can include water canals/deltas
        # The corpus contributes only the frequency WEIGHT; the identity itself (mask, type) is
        # re-sourced from the ontology so the placed object is ontology-authoritative.
        ident = ON.identity_of(anim) if ON.has_animation(anim) else e["identity"]
        out.append({"identity": ident, "weight": 1.0 + math.log1p(max(e.get("weight", 1), 1))})
        if anim:
            seen.add(anim)
    for e in full_fn(terr_id) or []:
        anim = (e.get("identity") or {}).get("animation")
        if anim and anim in seen:
            continue
        out.append({"identity": e["identity"], "weight": 1.6})
        if anim:
            seen.add(anim)
    return out


# water features are blocking in the catalog but do NOT read as an obstacle — never use them as a
# zone-border ridge (the belt must be real obstacles: mountains, trees, hills, rocks). These are
# the ONTOLOGY type-level (category) names for the catalog's water features.
# Decoration categories that must NEVER be placed on any terrain: water-feature tiles (river
# deltas, lakes incl. the AB LAKE_2 avllk1r, reefs, kelp) that read as misplaced water cutting
# through land. Excluded unconditionally in the pools, the MRF learning (so the category field
# can't assign them), and the MRF decode. NOTE: the AB class 199 (TREES_2, avlswt*/avltro* --
# swamp palms + rough trees, sprite-verified) was excluded here while it was the opaque
# CLASS_199; it is real vegetation and is now allowed.
EXCLUDE_DECOR_TYPES = {"LAKE", "FROZEN_LAKE", "RIVER_DELTA", "KELP", "REEF", "LAKE_2"}


def _is_excluded_anim(anim):
    """True if an animation's ontology category is an excluded water-canal/water-feature type."""
    if not anim:
        return False
    idx = ON.category_of(anim)
    return idx is not None and ON.veg_categories()[idx] in EXCLUDE_DECOR_TYPES


def _ident_entries(identities):
    """Wrap ontology placement identities as uniform-weight pool entries."""
    return [{"identity": i, "weight": 1} for i in identities]


def _border_idents(terr_id):
    """1x1 (else <=2 cell) blocking DECORATION for a CONTINUOUS rim belt — a single-tile footprint
    sits exactly on its rim tile so the ring is unbroken and never spills onto a neighbour. Sourced
    from the ONTOLOGY's per-terrain decoration; falls back to the smallest blocking decor, then grass."""
    pool = ON.decor_pool(terr_id, blocking=True, exclude_types=EXCLUDE_DECOR_TYPES)
    ones = [i for i in pool if i["mask"] == ["B"]]
    smalls = [i for i in pool if sum(len(r) for r in i["mask"]) <= 2]
    chosen = ones or smalls
    if not chosen and terr_id != 2:
        return _border_idents(2)
    return _ident_entries(chosen)


def _veg_idents(terr_id):
    """Blocking real-OBSTACLE DECORATION (mountains/trees/hills/rocks; any size; water features
    excluded) for a natural border ridge that may overlap — from the ONTOLOGY. Falls back to grass."""
    pool = [i for i in ON.decor_pool(terr_id, blocking=True, exclude_types=EXCLUDE_DECOR_TYPES)]
    if not pool and terr_id != 2:
        return _veg_idents(2)
    return _ident_entries(pool)


def _interior_idents(terr_id):
    """Terrain-matched DECORATION for INTERIOR scatter — flora and SMALL obstacles (footprint <= 4
    cells), water features excluded, non-blocking flora kept — from the ONTOLOGY. Falls back to grass."""
    pool = ON.decor_pool(terr_id, max_cells=4, exclude_types=EXCLUDE_DECOR_TYPES)
    if not pool and terr_id != 2:
        return _interior_idents(2)
    return _ident_entries(pool)


# ---------------------------------------------------------------------------
# Autocorrelated decoration: a spatially-coherent CATEGORY field + stacking.
#
# The "looks too random" problem is that identity was drawn independently per tile, so adjacent
# tiles in a clump got unrelated sprites (salt-and-pepper). Instead we lay a coarse CATEGORY field
# over the zone — each ~CLUMP_CELL-sized block is assigned ONE ontology decoration category
# (mountains / oak trees / rocks / …), so neighbouring tiles share a kind ("a stand of trees", "a
# field of rocks"). The concrete sprite within a category is still varied, and tiles may STACK a
# non-blocking flora overlay on the base (real H3 layers decoration). Everything is driven by the
# per-zone seeded rng so a given map seed reproduces exactly.
# ---------------------------------------------------------------------------

CLUMP_CELL = 5        # edge (tiles) of a single-category patch — the coherence length of a "stand"
OVERLAY_PROB = 0.28   # chance a placed decoration also gets a non-blocking flora overlay (stacking)
_OVERLAY_IDENTS = {}  # terr_id -> [non-blocking small flora identities] (ontology, cached)


def _pool_category_weights(pool):
    """Aggregate a decoration pool [{identity, weight}] into {ontology-category-name: weight}."""
    cats = ON.veg_categories()
    w = collections.defaultdict(float)
    for e in pool or []:
        anim = (e.get("identity") or {}).get("animation")
        idx = ON.category_of(anim) if anim else None
        if idx is not None:
            w[cats[idx]] += max(e.get("weight", 1), 0.0)
    return w


def _category_field(NW, NH, cats, weights, rng, cell=CLUMP_CELL):
    """Coarse categorical field over an NH×NW bbox: each cell-sized block draws ONE category
    (weighted by ``weights``); every fine tile inherits its block's category. Gives coherent
    same-kind patches. Returns field[y][x] -> category name, or None if there are no categories."""
    if not cats:
        return None
    gw = max(1, NW // cell + 1)
    gh = max(1, NH // cell + 1)
    blk = [[rng.choices(cats, weights=weights, k=1)[0] for _ in range(gw)] for _ in range(gh)]
    return [[blk[min(gh - 1, y // cell)][min(gw - 1, x // cell)] for x in range(NW)]
            for y in range(NH)]


EMPTY_CAT = "EMPTY"     # the MRF's "no decoration here" state — a learned clearing
MRF_SWEEPS = 3          # Gibbs relaxation passes over the zone (more = smoother/longer-range)
_MRF_ALPHA = 0.5        # Laplace smoothing so an unseen adjacency is rare, not impossible


def _zone_category_field(ts, terr_id, catmrf, rng, sweeps=MRF_SWEEPS):
    """Sample a spatially-correlated category per tile by Gibbs sampling the learned per-terrain
    category MRF (corpus 4-adjacency co-occurrence). Each tile is resampled from
    ``P(c) ∝ unary(c) · Π_neighbours P(c | neighbour-category)`` so adjacent tiles share a kind
    and clearings (the ``EMPTY`` state) form coherent gaps — the joint corpus texture, not an
    independent per-block draw. Returns ``{tile: category-name}`` (``EMPTY`` included), or ``None``
    when no MRF is available for this terrain. Fully seeded → deterministic for a given rng."""
    if not catmrf:
        return None
    unary = catmrf.get("unary") or {}
    pair = catmrf.get("pair") or {}
    # excluded water-canal categories never appear in the field (safeguard for a stale grammar:
    # a freshly learned MRF already omits them — see learn_grammar).
    cats = [c for c in sorted(unary) if c not in EXCLUDE_DECOR_TYPES]
    if not cats:
        return None
    n = len(cats)
    tot_u = sum(unary.values()) + _MRF_ALPHA * n
    uprob = [(unary.get(c, 0) + _MRF_ALPHA) / tot_u for c in cats]
    # row-normalised transition: P(neighbour=c2 | centre=c1) — the directional adjacency affinity.
    trans = {}
    for c1 in cats:
        row = pair.get(c1, {})
        s = sum(row.get(c2, 0) for c2 in cats) + _MRF_ALPHA * n
        trans[c1] = {c2: (row.get(c2, 0) + _MRF_ALPHA) / s for c2 in cats}
    tiles = sorted(ts)
    field = {t: rng.choices(cats, weights=uprob, k=1)[0] for t in tiles}      # unary init
    for _ in range(max(1, sweeps)):
        for t in tiles:
            neigh = [field[m] for m in _ring1(t) if m in field]
            w = []
            for i, c in enumerate(cats):
                p = uprob[i]
                tc = trans[c]
                for nc in neigh:
                    p *= tc[nc]
                w.append(p)
            field[t] = rng.choices(cats, weights=w, k=1)[0]
    return field


_ONE_TILE_IDENTS = {}


def _one_tile_obstacle(terr_id, rng):
    """A single-tile blocking obstacle (mask ``["B"]``/≤2 cells) for tight gaps — it covers its
    anchor without sealing an adjacent corridor, so dense interior cover never blocks navigation.
    Sourced from the ontology via :func:`_border_idents`."""
    if terr_id not in _ONE_TILE_IDENTS:
        _ONE_TILE_IDENTS[terr_id] = [e["identity"] for e in _border_idents(terr_id)
                                     if e["identity"]["mask"] == ["B"]]
    pool = _ONE_TILE_IDENTS[terr_id]
    return rng.choice(pool) if pool else None


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


def reconstruct_zone(zone, canon_zone, profile, seed=0, level=0):
    """Generate placement for a target zone from a feature profile (the code path).

    Count per purpose = density x target_area (adapts to deform). Positions are
    weighted by each purpose's depth signature, min-spaced; identities reused from
    the profile. Deterministic for a given seed.
    """
    rng = random.Random(seed)
    area = zone["area"]
    tiles = sorted(zone["tiles_set"])  # deterministic order
    tiles_set = zone["tiles_set"]
    used = set()
    placed = []
    report = {}

    for p in sorted(profile["purposes"], key=_prio):
        info = profile["purposes"][p]
        n = round(info["density"] * area)
        if n <= 0:
            continue
        mu, sd = info["depth_mu"], info["depth_sd"]
        # min separation that lets n objects fit the area (preserves density, the
        # "same result" lever; the profile's raw spacing reflects edge-clustering
        # and would over-constrain the count).
        min_sep = max(1.0, 0.6 * math.sqrt(area / max(n, 1)))
        free = [(x, y) for (x, y) in tiles if (x, y) not in used]
        weights = [_gauss(canon_zone[(x, y)][0], mu, sd) for (x, y) in free]
        chosen = _weighted_spaced(free, weights, n, min_sep, rng,
                                  decoration=(p == "DECORATION"))
        for (x, y) in chosen:
            ident = _pick_identity(info["identities"], rng)
            if p != "DECORATION":  # gameplay blocking must stay inside the zone
                if any((tx, ty) not in tiles_set
                       for tx, ty, blk in OR.mask_cells(ident["mask"], x, y) if blk):
                    continue
            placed.append({**ident, "x": x, "y": y, "l": level, "_purpose": p})
            used.add((x, y))
        report[p] = {"target": n, "placed": sum(1 for o in placed if o["_purpose"] == p)}
    return placed, report


# ---------------------------------------------------------------------------
# Three decoration-fill models (compared side-by-side). Each takes the SOURCE
# decoration + source shape frame and a TARGET zone, and returns placed
# decoration for the target. Gameplay is placed separately (shared) so the
# panels differ ONLY in decoration. All deterministic for a given seed.
# ---------------------------------------------------------------------------

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


def _line_tiles(a, b):
    """L-shaped path of tiles from a to b (horizontal then vertical)."""
    (x0, y0), (x1, y1) = a, b
    out = [(x, y0) for x in range(x0, x1 + (1 if x1 >= x0 else -1), 1 if x1 >= x0 else -1)]
    out += [(x1, y) for y in range(y0, y1 + (1 if y1 >= y0 else -1), 1 if y1 >= y0 else -1)]
    return out


def _carve_connect(tgt_set, wall, hard_block, rounds=40):
    """Reconnect isolated open pockets by removing wall tiles along short paths."""
    for _ in range(rounds):
        comps = _components(tgt_set - wall - hard_block)
        if len(comps) <= 1:
            break
        main = set(comps[0])
        mcx = sum(x for x, _ in main) / len(main)
        mcy = sum(y for _, y in main) / len(main)
        a = min(comps[1], key=lambda t: (t[0] - mcx) ** 2 + (t[1] - mcy) ** 2)
        b = min(main, key=lambda t: (t[0] - a[0]) ** 2 + (t[1] - a[1]) ** 2)
        carved = False
        for t in _line_tiles(a, b):
            if t in tgt_set and t not in hard_block and t in wall:
                wall.discard(t)
                carved = True
        if not carved:
            break
    return wall


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

# Per-terrain CLEAN interior tile views (corpus-derived: the views real maps use on
# tiles whose 4 neighbours are the same terrain). Synthetic views 0-7 land on
# transition/border frames for most terrains, which renders as an "off"/patchy
# ground — restricting to these keeps generated terrain reading as flat ground.
CLEAN_VIEWS = {
    0: [21, 22, 23, 24, 25, 26, 27, 28, 29],   # dirt
    1: [0, 1, 2, 3, 4, 5, 6, 7],               # sand
    2: [49, 50, 51, 52, 53, 54, 55, 56],       # grass
    3: [49, 50, 51, 52, 53, 54, 55, 56],       # snow
    4: [49, 50, 51, 52, 53, 54, 55, 56],       # swamp
    5: [49, 50, 51, 52, 53, 54, 55, 56],       # rough
    6: [49, 50, 51, 52, 53, 54, 55, 56],       # subterr
    7: [49, 50, 51, 52, 53, 54, 55, 56],       # lava
    8: [21, 22, 23, 24, 25, 26, 27, 28, 29],   # water
    9: [0, 1, 2, 3, 4, 5, 6, 7],               # rock
}


def _cell(t, x=0, y=0):
    # vary the terrain view-frame per tile (deterministic) across the CLEAN center
    # variants for this terrain, so painted ground reads as flat ground (not a
    # repeated tile, and not transition/border frames).
    vs = CLEAN_VIEWS.get(t, [49, 50, 51, 52, 53, 54, 55, 56])
    return {"t": t, "view": vs[(x * 7 + y * 13) % len(vs)],
            "rt": 0, "rd": 0, "ot": 0, "od": 0, "m": 0}


# --- corpus-learned terrain auto-tiler ------------------------------------------
# Real H3 terrain meets water and other terrains via transition VIEWS (+ the `m`
# mirror flag), not a flat clean frame. We learn, per (centre terrain, its 8 neighbour
# terrains), the (view, m) pairs real maps use, then replay them — reproducing shores,
# beaches and land-land blends exactly as the editor draws them. Back-off: exact 8-sig
# -> 4-sig (N,W,E,S) -> flat clean view.
_TILER = {}

_N8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]


def _neigh8(grid, x, y, W, H, t):
    return tuple(grid[y + dy][x + dx] if 0 <= x + dx < W and 0 <= y + dy < H else t
                 for dx, dy in _N8)


def _learn_terrain_tiler():
    """(exact, four, clean) view/m tables learned from every corpus terrain tile."""
    if "v" in _TILER:
        return _TILER["v"]
    import glob
    exact = collections.defaultdict(collections.Counter)   # (t, sig8)        -> (view,m)
    four = collections.defaultdict(collections.Counter)    # (t, N,W,E,S)     -> (view,m)
    clean = collections.defaultdict(collections.Counter)   # t (all-same nbrs)-> (view,m)
    for f in glob.glob(os.path.join(ROOT, "maps_json", "*.json")):
        m = json.load(open(f))
        for g in m["terrain"]:
            H = len(g)
            W = len(g[0])
            T = [[c["t"] for c in row] for row in g]
            for y in range(H):
                for x in range(W):
                    c = g[y][x]
                    t = c["t"]
                    vm = (c["view"], c["m"])
                    sig = _neigh8(T, x, y, W, H, t)
                    exact[(t, sig)][vm] += 1
                    four[(t, (sig[1], sig[3], sig[4], sig[6]))][vm] += 1
                    if all(v == t for v in sig):
                        clean[t][vm] += 1
    _TILER["v"] = (exact, four, clean)
    return _TILER["v"]


def _tile_cell(t, sig, x, y, tiler):
    exact, four, clean = tiler
    if all(v == t for v in sig):                     # interior: vary for texture
        cc = clean.get(t)
        if cc:
            opts = [vm for vm, _ in cc.most_common(8)]
            view, mm = opts[(x * 7 + y * 13) % len(opts)]
            return {"t": t, "view": view, "rt": 0, "rd": 0, "ot": 0, "od": 0, "m": mm}
        return _cell(t, x, y)
    hit = exact.get((t, sig)) or four.get((t, (sig[1], sig[3], sig[4], sig[6])))
    if not hit:                                      # unseen border config: flat fallback
        return _cell(t, x, y)
    view, mm = hit.most_common(1)[0][0]              # the H3-correct transition frame
    return {"t": t, "view": view, "rt": 0, "rd": 0, "ot": 0, "od": 0, "m": mm}


MIN_TERRAIN_PATCH = 4   # no terrain patch smaller than this — tiny speckles are merged into the
#                         dominant neighbour, so terrain reads as coherent regions (and doesn't
#                         fragment the map into unplayable sliver-zones).


def _despeckle_ids(ids, W, H, min_patch=MIN_TERRAIN_PATCH):
    """Reassign every connected same-terrain patch smaller than ``min_patch`` tiles to the terrain
    it borders most, iterating until no small patch remains (a merge can expose a new one)."""
    ids = [row[:] for row in ids]
    NB4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for _ in range(24):
        comp = [[-1] * W for _ in range(H)]
        comps = []
        cid = 0
        for y in range(H):
            for x in range(W):
                if comp[y][x] >= 0:
                    continue
                t = ids[y][x]
                stack, tiles = [(x, y)], [(x, y)]
                comp[y][x] = cid
                while stack:
                    a, b = stack.pop()
                    for dx, dy in NB4:
                        nx, ny = a + dx, b + dy
                        if 0 <= nx < W and 0 <= ny < H and comp[ny][nx] < 0 and ids[ny][nx] == t:
                            comp[ny][nx] = cid
                            stack.append((nx, ny))
                            tiles.append((nx, ny))
                comps.append((tiles, t))
                cid += 1
        changed = False
        for tiles, t in comps:
            if len(tiles) >= min_patch:
                continue
            nbr = collections.Counter()
            for x, y in tiles:
                for dx, dy in NB4:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and ids[ny][nx] != t:
                        nbr[ids[ny][nx]] += 1
            if nbr:
                newt = nbr.most_common(1)[0][0]
                for x, y in tiles:
                    ids[y][x] = newt
                changed = True
        if not changed:
            break
    return ids


def tile_terrain(id_grid, W, H):
    """Terrain-id grid -> faithful cell grid with corpus-correct transition views. Tiny terrain
    speckles (< MIN_TERRAIN_PATCH tiles) are first merged into their dominant neighbour."""
    id_grid = _despeckle_ids(id_grid, W, H)
    tiler = _learn_terrain_tiler()
    return [[_tile_cell(id_grid[y][x], _neigh8(id_grid, x, y, W, H, id_grid[y][x]), x, y, tiler)
             for x in range(W)] for y in range(H)]


def drop_water(grid):
    """Return a copy of a faithful cell grid with every WATER tile reassigned to the nearest
    land terrain (multi-source BFS through the water), then re-tiled so shores become clean
    ground. ROCK seams are left intact (they are the intended zone separators, not water)."""
    H = len(grid)
    W = len(grid[0]) if H else 0
    ids = [[c["t"] for c in row] for row in grid]
    seen = [[ids[y][x] != TS.WATER and ids[y][x] != TS.ROCK for x in range(W)] for y in range(H)]
    dq = collections.deque((x, y) for y in range(H) for x in range(W) if seen[y][x])
    while dq:                                          # land frontier floods into adjacent water
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx] and ids[ny][nx] == TS.WATER:
                seen[ny][nx] = True
                ids[ny][nx] = ids[y][x]
                dq.append((nx, ny))
    if any(ids[y][x] == TS.WATER for y in range(H) for x in range(W)):   # water walled-in by rock
        land = collections.Counter(t for row in ids for t in row
                                   if t != TS.WATER and t != TS.ROCK)
        fill = land.most_common(1)[0][0] if land else 2   # 2 = grass
        ids = [[fill if t == TS.WATER else t for t in row] for row in ids]
    return tile_terrain(ids, W, H)


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


# ---------------------------------------------------------------------------
# Full-map generation:  Markov terrain  +  patches drawn from the library
#
# Lay a fresh Markov-sampled terrain, segment it into same-terrain zones, and fill
# each zone from the patch pool: pick a same-terrain patch of similar size, stretch
# its gameplay onto the zone (rigid forward-map) and fill decoration via a model
# (quilt by default). Deterministic for a given seed.
# ---------------------------------------------------------------------------

DECO_MODELS = {"binned": deco_binned, "quilt": deco_quilt, "split": deco_split}


def load_patch_pool(out_dir: str | None = None):
    """terrain_name -> [(area, abspath)] read from the library's index.csv."""
    import csv
    out_dir = out_dir or os.path.join(ROOT, "out", "patches")
    idx = os.path.join(out_dir, "index.csv")
    if not os.path.exists(idx):
        sys.exit(f"no patch library at {idx} — run `patches` first")
    pool = collections.defaultdict(list)
    with open(idx, newline="") as f:
        for r in csv.DictReader(f):
            pool[r["terrain"]].append((int(r["area"]), os.path.join(out_dir, r["path"])))
    return pool


def nearest_patch_paths(pool, terrain_type, area, k=8):
    """The k library patch paths of this terrain whose size is closest to `area`
    (nearest first). Empty if the terrain has no patches."""
    cands = pool.get(TNAME.get(terrain_type, ""))
    if not cands:
        return []
    return [p for _a, p in sorted(cands, key=lambda ap: abs(ap[0] - area))[:k]]


def pick_patch(pool, terrain_type, area, rng, k=8):
    """A library patch of this terrain whose size is closest to `area` (random among
    the k closest, for variety). None if the terrain has no patches."""
    paths = nearest_patch_paths(pool, terrain_type, area, k)
    return rng.choice(paths) if paths else None


def _patch_source(patch):
    """Reconstruct a usable SOURCE frame from a stored patch: a (src_zone, src_canon,
    live objects) triple in the patch's own bbox-relative coordinates. The mini-grid
    re-segmentation reproduces the exact (depth,sweep) frame the patch was recorded in
    (depth is shape-only), so objects' footprints land on the right tiles."""
    mask_rel = [(int(dx), int(dy)) for dx, dy in patch["mask_rel"]]
    w = max(dx for dx, _ in mask_rel) + 1
    h = max(dy for _, dy in mask_rel) + 1
    tt = patch["terrain_type"]
    grid = [[_cell(TS.ROCK, x, y) for x in range(w)] for y in range(h)]
    for (dx, dy) in mask_rel:
        grid[dy][dx] = _cell(tt, dx, dy)
    zr, _zl, zc = _segment_level(grid)
    cands = [k for k, z in zr.items() if z["terrain_type"] == tt]
    if not cands:                       # degenerate 1-2 tile shape: synthesize directly
        ts = set(mask_rel)
        cx = sum(x for x, _ in mask_rel) / len(mask_rel)
        cy = sum(y for _, y in mask_rel) / len(mask_rel)
        src_zone = {"tiles": list(mask_rel), "tiles_set": ts, "terrain_type": tt,
                    "area": len(mask_rel), "centroid": (cx, cy)}
        src_canon = {t: (0.5, 0.5) for t in mask_rel}
    else:
        zid = max(cands, key=lambda k: zr[k]["area"])
        src_zone, src_canon = zr[zid], zc[zid]
    objs = [{**o["identity"], "x": o["anchor_off"][0], "y": o["anchor_off"][1],
             "_purpose": o["purpose"], "purpose": o["purpose"]}
            for o in patch["objects"]]
    return src_zone, src_canon, objs


def _place_patch(src_zone, src_canon, src_objs, tgt_zone, tgt_canon, model, seed,
                 level, global_hard):
    """Stretch one patch onto a target zone: gameplay forward-mapped rigid (avoiding
    already-placed gameplay across the whole map), decoration filled by `model`. The
    patch objects carry flat identities, so this does not call the ontology. Updates
    global_hard in place with this zone's gameplay footprints. Returns placed objects."""
    rng = random.Random(seed)
    tgt_set = tgt_zone["tiles_set"]
    (sx0, sy0, sx1, sy1), _ = zone_bbox_mask(src_zone["tiles"])
    (tx0, ty0, tx1, ty1), _ = zone_bbox_mask(tgt_zone["tiles"])
    sw, sh = max(sx1 - sx0, 1), max(sy1 - sy0, 1)
    tw, th = max(tx1 - tx0, 1), max(ty1 - ty0, 1)

    def fwd(x, y):
        return (tx0 + round((x - sx0) / sw * tw), ty0 + round((y - sy0) / sh * th))

    gp = [o for o in src_objs if o["_purpose"] != "DECORATION"]
    decor = [o for o in src_objs if o["_purpose"] == "DECORATION"]

    placed, used, zone_hard = [], set(), set()
    for o in sorted(gp, key=lambda o: (o["y"], o["x"])):
        ident = {k: o[k] for k in ("type", "subtype", "animation", "mask")}
        tx, ty = fwd(o["x"], o["y"])

        def blocks(x, y):
            return [(cx, cy) for cx, cy, blk in OR.mask_cells(ident["mask"], x, y) if blk]

        if ((tx, ty) not in tgt_set or (tx, ty) in used
                or any(c in global_hard for c in blocks(tx, ty))):
            tx, ty = _nearest_free((tx, ty), tgt_set, used)
            if any(c in global_hard for c in blocks(tx, ty)):
                continue                # cannot place without burying another sprite
        used.add((tx, ty))
        placed.append({**ident, "x": tx, "y": ty, "l": level, "_purpose": o["_purpose"]})
        for cx, cy in blocks(tx, ty):
            if (cx, cy) in tgt_set:
                zone_hard.add((cx, cy))

    hard = zone_hard | global_hard
    placed += DECO_MODELS[model](decor, src_canon, src_zone["tiles_set"],
                                 tgt_zone, tgt_canon, hard, seed, level)
    global_hard |= zone_hard
    return placed


# ---------------------------------------------------------------------------
# Terrain LAYOUT generators (the macro structure). Markov is a texture model with
# no zone structure (speckle); these give a few large, coherent regions. Each
# returns (terrain_grid, W, H, info) and feeds the SAME patch-fill below.
# ---------------------------------------------------------------------------

def _markov_marg():
    """Corpus surface terrain marginal (terrain_id -> count), incl. water/rock."""
    import markov_terrain as MT
    if "m" not in _MARKOV_MODEL:
        _MARKOV_MODEL["m"] = (MT.learn(0), MT.learn4(0))
    return _MARKOV_MODEL["m"][0]["marg"]


def _sample_terrain(marg, rng):
    items = sorted(marg.items())
    tot = sum(w for _, w in items)
    r = rng.random() * tot
    acc = 0
    for t, w in items:
        acc += w
        if r <= acc:
            return t
    return items[-1][0]


def _poisson_seeds(W, H, n, rng):
    """n well-spread seed points (best-effort min separation, padded if needed)."""
    seeds = []
    mind = 0.7 * min(W, H) / max(n ** 0.5, 1)
    for _ in range(n * 60):
        if len(seeds) >= n:
            break
        p = (rng.randint(0, W - 1), rng.randint(0, H - 1))
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= mind * mind for q in seeds):
            seeds.append(p)
    while len(seeds) < n:
        seeds.append((rng.randint(0, W - 1), rng.randint(0, H - 1)))
    return seeds


def _value_noise(W, H, cell, rng):
    """Smooth value-noise field in [-1,1] (coarse random grid, bilinear upsample).
    Smooth (not white) noise is what makes warped borders wavy instead of fuzzy."""
    gw, gh = W // cell + 2, H // cell + 2
    g = [[rng.uniform(-1, 1) for _ in range(gw)] for _ in range(gh)]
    out = [[0.0] * W for _ in range(H)]
    for y in range(H):
        gy = y / cell; iy = int(gy); fy = gy - iy
        for x in range(W):
            gx = x / cell; ix = int(gx); fx = gx - ix
            a = g[iy][ix] * (1 - fx) + g[iy][ix + 1] * fx
            b = g[iy + 1][ix] * (1 - fx) + g[iy + 1][ix + 1] * fx
            out[y][x] = a * (1 - fy) + b * fy
    return out


def _carve_river(terr, W, H, rng):
    """A meandering 2-wide water river from one edge across the map (random walk with
    perpendicular drift)."""
    horiz = rng.random() < 0.5
    if horiz:
        fx, fy, dx, dy, steps = 0.0, float(rng.randint(0, H - 1)), 1, 0, W
    else:
        fx, fy, dx, dy, steps = float(rng.randint(0, W - 1)), 0.0, 0, 1, H
    for _ in range(steps * 2):
        ix, iy = int(round(fx)), int(round(fy))
        if not (0 <= ix < W and 0 <= iy < H):
            break
        for ox, oy in ((0, 0), (1, 0), (0, 1)):
            X, Y = ix + ox, iy + oy
            if 0 <= X < W and 0 <= Y < H:
                terr[Y][X] = TS.WATER
        fx += dx + dy * rng.uniform(-0.85, 0.85)
        fy += dy + dx * rng.uniform(-0.85, 0.85)


def layout_region(seed, size, rivers=2):
    """RMG-style synthetic layout: spread N seeds, grow nearest-seed regions whose
    borders are warped by smooth noise (organic, not polygonal), give each a
    corpus-sampled terrain, lay ROCK mountain-range seams between adjacent land zones
    (with periodic gaps = chokepoint passages), and carve a couple of rivers."""
    rng = random.Random(seed)
    W = H = size
    n = max(6, round(W * H / 380))
    seeds = _poisson_seeds(W, H, n, rng)
    marg = _markov_marg()

    def land_or_water():            # surface rock renders as black void; never seed it
        t = _sample_terrain(marg, rng)
        while t == TS.ROCK:
            t = _sample_terrain(marg, rng)
        return t

    terrs = [land_or_water() for _ in seeds]
    nx, ny = _value_noise(W, H, 9, rng), _value_noise(W, H, 9, rng)
    amp = size / 7.0
    label = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            wx, wy = x + amp * nx[y][x], y + amp * ny[y][x]
            label[y][x] = min(range(n), key=lambda k:
                              (wx - seeds[k][0]) ** 2 + (wy - seeds[k][1]) ** 2)
    terr_id = [[terrs[label[y][x]] for x in range(W)] for y in range(H)]
    for _ in range(rivers):
        _carve_river(terr_id, W, H, rng)

    grid = tile_terrain(terr_id, W, H)   # corpus-correct shore/transition views
    return grid, W, H, f"region(n={n})"


def layout_skeleton(seed, size, max_w=110):
    """Borrow a real corpus map's surface terrain as the canvas (coherent, organic),
    then refill its zones from the patch pool. Picks a not-too-large map."""
    rng = random.Random(seed)
    names = OR.all_map_names()
    name = rng.choice(names)
    for _ in range(8):
        fm = OR.load_faithful(name)
        if fm["width"] <= max_w:
            break
        name = rng.choice(names)
    fm = OR.load_faithful(name)
    return fm["terrain"][0], fm["width"], fm["height"], f"skeleton({name})"


def layout_jigsaw(seed, size, out_dir=None):
    """Pack real patch shapes (mask_rel) onto a water canvas like puzzle pieces, each
    keeping a 1-tile gap so segmentation keeps them as separate zones; paint each
    piece's terrain. Leftover stays water."""
    rng = random.Random(seed)
    W = H = size
    pool = load_patch_pool(out_dir)
    paths = [p for lst in pool.values() for (a, p) in lst if 30 <= a <= 500]
    rng.shuffle(paths)
    grid = [[_cell(TS.WATER, x, y) for x in range(W)] for y in range(H)]
    occ = [[False] * W for _ in range(H)]

    def free(ox, oy, mask, w, h):
        if ox < 1 or oy < 1 or ox + w > W - 1 or oy + h > H - 1:
            return False
        for dx, dy in mask:
            x, y = ox + dx, oy + dy
            for ny in range(y - 1, y + 2):      # require a 1-tile empty ring
                for nx in range(x - 1, x + 2):
                    if occ[ny][nx]:
                        return False
        return True

    filled = 0
    for path in paths:
        if filled > W * H * 0.55:
            break
        patch = json.load(open(path))
        mask = [(int(dx), int(dy)) for dx, dy in patch["mask_rel"]]
        w = max(dx for dx, _ in mask) + 1
        h = max(dy for _, dy in mask) + 1
        if w > W - 2 or h > H - 2:
            continue
        tt = patch["terrain_type"]
        for _ in range(60):
            ox, oy = rng.randint(1, W - w - 1), rng.randint(1, H - h - 1)
            if free(ox, oy, mask, w, h):
                for dx, dy in mask:
                    occ[oy + dy][ox + dx] = True
                    grid[oy + dy][ox + dx] = _cell(tt, ox + dx, oy + dy)
                filled += len(mask)
                break
    # Re-tile through the corpus-learned tiler so piece/water seams get real transition
    # views (curved beaches), not the flat interior frames `_cell` paints. This only
    # relabels view/mirror per tile — terrain ids and the 1-tile water gaps that keep
    # pieces as separate zones are untouched.
    ids = [[grid[y][x]["t"] for x in range(W)] for y in range(H)]
    return tile_terrain(ids, W, H), W, H, "jigsaw"


LAYOUTS = {"markov": lambda s, z, od: (markov_terrain_level(z, z, s), z, z, "markov"),
           "region": lambda s, z, od: layout_region(s, z),
           "skeleton": lambda s, z, od: layout_skeleton(s, z),
           "jigsaw": lambda s, z, od: layout_jigsaw(s, z, od)}


# ---------------------------------------------------------------------------
# FEATURE-GRAMMAR ENGINE (replaces patch-copy fill).
#
# Instead of copying one library patch and stretching it, we LEARN, per
# (terrain, role/archetype), a RELATIONAL SETPIECE (an anchor + satellites in
# relative arrangement: guard-on-the-loot's-approach, dwellings-fanned-around-
# the-town) plus an OPENNESS field (how empty real zones keep each part of their
# shape), then CONSTRUCT a fresh zone: place the setpiece by its relations, carve
# and reserve the open lanes + aprons, wall the rim with mountains/trees leaving
# a few border gaps, and fill the remainder with vegetation only up to the learned
# openness budget. This captures the MEANING (relations) and the DESIGNED EMPTY
# SPACE that marginal densities throw away.
# ---------------------------------------------------------------------------

GRAMMAR_PATH = os.path.join(ROOT, "out", "grammar.json")
LOOT_PURPOSES = ("BANK", "REWARD_PICKUP", "RESOURCE_PILE")
GUARDABLE = {"BANK", "REWARD_PICKUP", "RESOURCE_PILE", "MINE", "DWELLING"}
ROLE_ANCHOR = {"town": "TOWN", "mine": "MINE", "treasure": "BANK", "dwelling": "DWELLING"}
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
GROUP_MEAN_SIZE = 4.0    # divides the zone budget into N groups. Higher than the raw corpus mean
#                          (~3.2) on purpose: it keeps gameplay density low enough that the blocking
#                          anchor bodies don't ring the open space into non-carveable pockets.
GROUP_MEMBER_CAP = 3     # max members stamped per group, so a set-piece stays compact (not a dump)
# Anchors with a BLOCKING body (a 'B' footprint) wall the open space; too many ring a zone into
# sealed pockets the reachability carve (decoration-only) can't open. Cap them per zone and fill the
# remaining groups with PICKABLE anchors (resource piles / loot — single non-blocking 'A' tiles).
BLOCK_ANCHORS = {"TOWN", "MINE", "DWELLING", "BANK"}
PICK_ANCHORS = {"REWARD_PICKUP", "RESOURCE_PILE"}
NON_GAMEPLAY = ("DECORATION", "TERRAIN_MODIFIER")
# Purposes NEVER scattered independently: TOWN defines a town zone (anchor only); HERO is
# map-relational; TRANSPORT/QUEST_GATE/WATER_TRANSPORT objects are only meaningful in matched
# SETS (portal entrance+exit, gate+key) or with context (boats need water) — placed by
# _place_relational at the map level, or dropped, so we never emit a lone half of a pair.
SATELLITE_SKIP = {"TOWN", "HERO", "TRANSPORT", "QUEST_GATE", "WATER_TRANSPORT"}
# Whether to emit relational sets (portals, border gates + keymaster tents, seer huts). Disabled
# for now: their pairing/placement is not yet coherent, so we exclude these objects entirely.
PLACE_RELATIONAL = False
# Relational families captured into the grammar catalog, paired by subtype (= color/portal id).
RELATIONAL_TYPES = {"monolithOneWayEntrance", "monolithOneWayExit", "monolithTwoWay",
                    "borderGate", "borderGuard", "keymasterTent", "seerHut",
                    "subterraneanGate", "whirlpool"}
VEG_SCALE = 0.95  # fraction of the decoration budget to lay (rest stays clearing). High: real H3
#                   interiors are mostly obstacle cover (mountains/forests) with carved clearings.
WALL_THICK_MIN = 3       # minimum inter-zone belt thickness — zones are delimited by a real
#                          mountain/forest range, not a 1-tile seam (only the passage gaps open).
MIN_INTERIOR_COVER = 0.92  # floor on interior obstacle coverage (of the free, non-corridor tiles)
#                            so terrain is mostly wooded/rocky (~60-70% of the zone) with the carved
#                            corridors + a few low-noise clearings as the navigable minority.
INTERIOR_SCALE = 0.18    # graph path: fraction of interior tiles eligible for scatter decoration
INTERIOR_THRESH = 0.45   # graph path: only the clump cores (noise above this) get interior decor
NAV_REACH_FRAC = 0.40    # target fraction of OPEN land the start must reach: the navigable basin is
#                          grown to this (corpus mean ~0.35) by carving decoration into the nearest
#                          unreached open pocket, so the map has real navigable space, not fragments.


_GUARDABLE_PRIO = {"BANK": 4, "MINE": 3, "REWARD_PICKUP": 2, "RESOURCE_PILE": 1, "DWELLING": 0}
# Only WORTHY guardables get a dedicated monster — a lone resource pile usually stays unguarded,
# so guards read as protecting real rewards (mines/dwellings/banks/artifacts), not random scatter.
GUARD_WORTHY = {"BANK", "MINE", "DWELLING", "REWARD_PICKUP"}


def _role_of(purposes):
    """Archetype by PRIORITY-of-presence (a town zone is one with a town, regardless of
    how many resources it also has). Caller passes only ANCHORED-in-zone purposes, so
    boundary objects shared with neighbours don't mislabel the zone."""
    if "TOWN" in purposes:
        return "town"
    if "MINE" in purposes:
        return "mine"
    if any(p in purposes for p in LOOT_PURPOSES):
        return "treasure"
    if "DWELLING" in purposes:
        return "dwelling"
    return "passage"


def _ik(o):
    return (o["type"], o["subtype"], o["animation"], tuple(tuple(r) for r in o["mask"]))


def _euclid(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _manh(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def _sweep_bin(s, S=SWEEP_BINS):
    return min(int(s * S), S - 1)


def _pick_anchor(gp, role):
    """The role's primary object (the biggest footprint of the anchor purpose)."""
    want = ROLE_ANCHOR.get(role)
    cands = [o for o in gp if o["_purpose"] == want] if want else []
    if not cands and role == "treasure":
        cands = [o for o in gp if o["_purpose"] in LOOT_PURPOSES]
    if not cands:
        return None
    return max(cands, key=lambda o: len(o["mask"]) * (len(o["mask"][0]) if o["mask"] else 0))


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


def _wall_thickness(decor, canon_zone, tiles_set):
    """How many depth bins from the rim are packed enough (dens>=0.4) to read as a wall."""
    prof = decor_bins(decor, canon_zone, tiles_set)
    t = 0
    for k in range(prof["K"]):
        if prof["dens"][k] >= 0.4:
            t = k + 1
        else:
            break
    return t or 1


def learn_grammar(lib_dir=None):
    """Per (terrain, role): relational setpiece + openness field, averaged over the
    corpus patch library. Empirical (means over real zones), deterministic, inspectable."""
    pool = load_patch_pool(lib_dir)
    paths = sorted(p for lst in pool.values() for (_a, p) in lst)
    K, Sx = DEPTH_BINS, SWEEP_BINS
    samples = collections.defaultdict(list)
    idc = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    ids = {}
    # spatial-correlation MRF over decoration CATEGORIES (per terrain name): the corpus 4-adjacency
    # co-occurrence of ontology categories, with an explicit EMPTY state so clearings are learned
    # jointly with clumping. cat_unary[terr][c] = tile count; cat_pair[terr][c1][c2] = adjacency count.
    cat_unary = collections.defaultdict(collections.Counter)
    cat_pair = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    opens = collections.defaultdict(
        lambda: [[[0.0, 0] for _ in range(Sx)] for _ in range(K)])
    rel_catalog = collections.defaultdict(dict)  # type -> subtype -> identity
    # group/composition archetypes: spatial clusters of gameplay objects, keyed by (terr, anchor
    # purpose). grp_samples records each cluster's member multiset + member radii + guard presence;
    # grp_idc holds the concrete identity catalog per (key, member-purpose) for re-synthesis.
    grp_samples = collections.defaultdict(list)
    grp_idc = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))

    for path in paths:
        patch = json.load(open(path))
        terr = patch.get("terrain_name") or TNAME.get(patch["terrain_type"])
        z, canon, objs = _patch_source(patch)
        ts = z["tiles_set"]
        for o in objs:                           # remember every relational id by subtype
            if o["type"] in RELATIONAL_TYPES:
                rel_catalog[o["type"]].setdefault(o["subtype"], {
                    "type": o["type"], "subtype": o["subtype"],
                    "animation": o["animation"], "mask": o["mask"]})
        gp = [o for o in objs if o["_purpose"] not in NON_GAMEPLAY]
        anchored = {o["_purpose"] for o in gp if (o["x"], o["y"]) in ts}
        role = _role_of(anchored)
        key = (terr, role)
        anchor = _pick_anchor(gp, role)
        guardables = [o for o in gp if o["_purpose"] in GUARDABLE]

        def addid(cat, o):
            k = _ik(o)
            idc[key][cat][k] += 1
            ids[k] = {"type": o["type"], "subtype": o["subtype"],
                      "animation": o["animation"], "mask": o["mask"]}

        sat = collections.defaultdict(lambda: {"count": 0, "depths": []})
        guard_d, plug, outw, gdepth = [], [], [], []
        for o in gp:
            p = o["_purpose"]
            if o is anchor:
                addid("anchor", o)
                continue
            if p == "GUARD":
                tg = guardables or ([anchor] if anchor else [])
                if tg:
                    nt = min(tg, key=lambda t: _manh(o, t))
                    d = _manh(o, nt)
                    guard_d.append(d)
                    plug.append(1.0 if d <= 2 else 0.0)
                    outw.append(1.0 if _obj_canon(o, canon, ts)[0]
                                < _obj_canon(nt, canon, ts)[0] else 0.0)
                gdepth.append(_obj_canon(o, canon, ts)[0])
                addid("guard", o)
                continue
            sat[p]["count"] += 1
            sat[p]["depths"].append(_obj_canon(o, canon, ts)[0])
            addid(p, o)

        # GROUP/COMPOSITION archetypes: cluster this zone's gameplay objects spatially, key each
        # cluster by its anchor purpose (a visitable/pickable), and record the member multiset +
        # member radii + whether the cluster is guarded. Identities go into grp_idc for re-synthesis.
        for cl in _cluster_objects(gp, GROUP_EPS):
            ganc = _group_anchor(cl)
            if ganc is None:
                continue                          # no visitable/pickable -> not a placeable group
            gkey = (terr, ganc["_purpose"])
            gk = _ik(ganc)
            grp_idc[gkey]["_anchor"][gk] += 1
            ids[gk] = {"type": ganc["type"], "subtype": ganc["subtype"],
                       "animation": ganc["animation"], "mask": ganc["mask"]}
            members = collections.Counter()
            radii, has_guard = [], False
            for o in cl:
                if o is ganc:
                    continue
                if o["_purpose"] == "GUARD":
                    has_guard = True
                    continue
                members[o["_purpose"]] += 1
                radii.append(_euclid(o, ganc))
                ok = _ik(o)
                grp_idc[gkey][o["_purpose"]][ok] += 1
                ids[ok] = {"type": o["type"], "subtype": o["subtype"],
                           "animation": o["animation"], "mask": o["mask"]}
            grp_samples[gkey].append(
                {"members": dict(members), "radii": radii, "guard": has_guard})

        for o in objs:
            if o["_purpose"] == "DECORATION":
                d = _obj_canon(o, canon, ts)[0]
                addid("wall" if _depth_bin(d) <= 1 else "veg", o)
        thick = _wall_thickness([o for o in objs if o["_purpose"] == "DECORATION"], canon, ts)

        # rasterize this zone's decoration into a per-tile CATEGORY grid (blocking footprint cells
        # carry their ontology category; everything else is EMPTY) and accumulate the per-terrain
        # 4-adjacency co-occurrence — the joint structure the marginal grammar throws away.
        _cats = ON.veg_categories()
        catg = {}
        for o in objs:
            if o["_purpose"] != "DECORATION":
                continue
            idx = ON.category_of(o.get("animation"))
            if idx is None:
                continue
            cname = _cats[idx]
            if cname in EXCLUDE_DECOR_TYPES:
                continue                       # water canals/deltas never enter the MRF -> stay EMPTY
            for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if blk and (cx, cy) in ts:
                    catg[(cx, cy)] = cname
        for t in ts:
            c1 = catg.get(t, EMPTY_CAT)
            cat_unary[terr][c1] += 1
            for n in _ring1(t):
                if n in ts:
                    cat_pair[terr][c1][catg.get(n, EMPTY_CAT)] += 1

        occ = set()
        for o in objs:
            for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
                occ.add((tx, ty))
        cellc = [[[0, 0] for _ in range(Sx)] for _ in range(K)]
        for t in ts:
            d, s = canon[t]
            cellc[_depth_bin(d)][_sweep_bin(s)][int(t in occ)] += 1  # [empty, occ] by membership
        for k in range(K):
            for s2 in range(Sx):
                emp, oc = cellc[k][s2]
                tot = emp + oc
                if tot:
                    opens[key][k][s2][0] += emp / tot
                    opens[key][k][s2][1] += 1

        samples[key].append({
            "area": z["area"],
            "adepth": _obj_canon(anchor, canon, ts)[0] if anchor else None,
            "apurpose": anchor["_purpose"] if anchor else None,
            "sat": {p: {"count": v["count"], "depths": v["depths"]} for p, v in sat.items()},
            "guard_count": sum(1 for o in gp if o["_purpose"] == "GUARD"),
            "gdepth": gdepth,
            "guard_d": guard_d, "plug": plug, "outw": outw, "thick": thick})

    def idlist(key, cat):
        return [{"identity": ids[k], "weight": w} for k, w in idc[key][cat].most_common()]

    grammar = collections.defaultdict(dict)
    for key, S in samples.items():
        terr, role = key
        w = len(S)
        adepths = [s["adepth"] for s in S if s["adepth"] is not None]
        apur = collections.Counter(s["apurpose"] for s in S if s["apurpose"]).most_common(1)
        anchor = None
        if adepths and idc[key]["anchor"]:
            anchor = {"purpose": apur[0][0] if apur else None,
                      "depth_mu": round(statistics.fmean(adepths), 4),
                      "depth_sd": round(statistics.pstdev(adepths) if len(adepths) > 1 else 0.12, 4),
                      "idents": idlist(key, "anchor")}
        satp = collections.defaultdict(lambda: {"dens": [], "depths": []})
        for s in S:
            a = max(1, s["area"])
            for p, v in s["sat"].items():
                satp[p]["dens"].append(v["count"] / a)
                satp[p]["depths"] += v["depths"]
        satellites = {}
        for p, v in satp.items():
            if not idc[key][p]:
                continue
            dd = v["depths"]
            satellites[p] = {
                "density": round(sum(v["dens"]) / w, 6),         # size-independent; 0 where absent
                "depth_mu": round(statistics.fmean(dd), 4) if dd else 0.5,
                "depth_sd": round(statistics.pstdev(dd) if len(dd) > 1 else 0.2, 4),
                "idents": idlist(key, p)}
        gdens = [s["guard_count"] / max(1, s["area"]) for s in S]
        gd = [d for s in S for d in s["guard_d"]]
        pl = [d for s in S for d in s["plug"]]
        ow = [d for s in S for d in s["outw"]]
        gdp = [d for s in S for d in s["gdepth"]]
        guard = None
        if idc[key]["guard"]:
            guard = {"density": round(sum(gdens) / w, 6),
                     "depth_mu": round(statistics.fmean(gdp), 4) if gdp else 0.3,
                     "depth_sd": round(statistics.pstdev(gdp) if len(gdp) > 1 else 0.2, 4),
                     "dist_mu": round(statistics.fmean(gd), 2) if gd else 1.0,
                     "plug_rate": round(statistics.fmean(pl), 3) if pl else 0.5,
                     "outward_rate": round(statistics.fmean(ow), 3) if ow else 0.5,
                     "idents": idlist(key, "guard")}
        om = [[round(opens[key][k][s2][0] / opens[key][k][s2][1], 3)
               if opens[key][k][s2][1] else 0.85 for s2 in range(Sx)] for k in range(K)]
        thicks = [s["thick"] for s in S]
        grammar[terr][role] = {
            "weight": w, "entrances": 3, "anchor": anchor,
            "satellites": satellites, "guard": guard, "openness": om,
            "wall": {"idents": idlist(key, "wall"),
                     "thickness": max(1, round(statistics.fmean(thicks)) if thicks else 1)},
            "veg": {"idents": idlist(key, "veg")}}
    # group/composition archetypes -> grammar["_groups"][terr][anchor_purpose]. members[p] = the
    # [mean, sd] count of member purpose p per cluster; spread = member-radius stats; guard_rate =
    # fraction of clusters carrying a guard; idents = concrete identity catalog per member purpose
    # ("_anchor" = the anchor identities). Only group STRUCTURE is learned; identities stay sourced.
    def grp_idlist(gkey, p):
        return [{"identity": ids[k], "weight": c} for k, c in grp_idc[gkey][p].most_common()]

    groups_out = collections.defaultdict(dict)
    for gkey, recs in grp_samples.items():
        terr, apur = gkey
        w = len(recs)
        mpurp = {p for r in recs for p in r["members"]}
        members = {}
        for p in sorted(mpurp):
            counts = [r["members"].get(p, 0) for r in recs]
            members[p] = [round(statistics.fmean(counts), 3),
                          round(statistics.pstdev(counts) if len(counts) > 1 else 0.0, 3)]
        radii = [d for r in recs for d in r["radii"]]
        gflags = [1.0 if r["guard"] else 0.0 for r in recs]
        groups_out[terr][apur] = {
            "weight": w,
            "members": members,
            "guard_rate": round(statistics.fmean(gflags), 3) if gflags else 0.0,
            "spread_mu": round(statistics.fmean(radii), 3) if radii else 0.0,
            "spread_sd": round(statistics.pstdev(radii) if len(radii) > 1 else 0.0, 3),
            "idents": {p: grp_idlist(gkey, p) for p in grp_idc[gkey]}}

    out = {t: dict(r) for t, r in grammar.items()}
    out["_relational"] = {t: dict(s) for t, s in rel_catalog.items()}
    out["_catmrf"] = {terr: {"unary": dict(cu),
                             "pair": {c1: dict(cat_pair[terr][c1]) for c1 in cat_pair[terr]}}
                      for terr, cu in cat_unary.items()}
    out["_groups"] = {terr: dict(g) for terr, g in groups_out.items()}
    return out


def _load_or_build_grammar(rebuild=False, lib_dir=None):
    if not rebuild and os.path.exists(GRAMMAR_PATH):
        return json.load(open(GRAMMAR_PATH))
    g = learn_grammar(lib_dir)
    os.makedirs(os.path.dirname(GRAMMAR_PATH), exist_ok=True)
    json.dump(g, open(GRAMMAR_PATH, "w"))
    return g


# --- zone synthesis geometry helpers ---------------------------------------

def _ring1(t):
    x, y = t
    return [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]


def _zone_boundary(ts):
    return [t for t in ts if any(n not in ts for n in _ring1(t))]


def _centroid_tile(tiles):
    cx = sum(x for x, _ in tiles) / len(tiles)
    cy = sum(y for _, y in tiles) / len(tiles)
    return min(tiles, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)


def _rim_band(ts, boundary, thick):
    band, frontier = set(boundary), set(boundary)
    for _ in range(max(0, thick - 1)):
        nxt = {n for t in frontier for n in _ring1(t) if n in ts and n not in band}
        band |= nxt
        frontier = nxt
    return band


def _apron(o, ts, r):
    out = []
    for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                t = (tx + dx, ty + dy)
                if t in ts:
                    out.append(t)
    return out


def _add_hard(hard, o, ts):
    for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
        if blk and (cx, cy) in ts:
            hard.add((cx, cy))


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


def _visit_tiles(o, ts):
    """The tiles that must stay open + reachable for an object to be usable: its 'A' visit-anchor
    cells (a mine entrance, a shrine's stand-on tile). Objects with no 'A' (e.g. a town visited from
    beside it) fall back to the in-zone, non-blocking footprint-adjacent tiles. In-zone tiles only."""
    anchors = [t for t in _mask_anchor_cells(o["mask"], o["x"], o["y"]) if t in ts]
    if anchors:
        return anchors
    blockers = {(cx, cy) for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]) if blk}
    out = []
    for cx, cy, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
        for n in _ring1((cx, cy)):
            if n in ts and n not in blockers:
                out.append(n)
    return out


def _fits(ident, x, y, ts, hard, keep_open=()):
    for cx, cy, blk in OR.mask_cells(ident["mask"], x, y):
        if blk and ((cx, cy) not in ts or (cx, cy) in hard or (cx, cy) in keep_open):
            return False
    return True


def _reserve_approach(o, ts, hard, keep_open):
    """Keep every visit tile of ``o`` open and reserve ONE adjacent approach tile, so a later
    gameplay object's blocking footprint can never seal the only way in (the cause of towns/mines
    getting walled off by their own neighbours — a non-carveable enclosure the carve can't open)."""
    for vt in _visit_tiles(o, ts):
        keep_open.add(vt)
        for n in _ring1(vt):
            if n in ts and n not in hard:
                keep_open.add(n)
                break


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _place_anchor(A, ts, canon, hard, rng, level, keep_open=None):
    keep_open = keep_open if keep_open is not None else set()
    tiles = list(ts)
    mu, sd = A["depth_mu"], A["depth_sd"]
    weights = [_gauss(canon[t][0], mu, sd) for t in tiles]
    for t in _weighted_spaced(tiles, weights, min(16, len(tiles)), 1.0, rng):
        ident = _pick_identity(A["idents"], rng)
        if _fits(ident, t[0], t[1], ts, hard, keep_open):
            o = {**ident, "x": t[0], "y": t[1], "l": level,
                 "_purpose": A.get("purpose") or "TOWN"}
            _add_hard(hard, o, ts)
            _reserve_approach(o, ts, hard, keep_open)
            return o, t
    return None, None


def _place_by_depth(p, pool, n, ts, canon, hard, rng, level, mu, sd, keep_open=None):
    """Place n objects of purpose p where this purpose sits in the shape (depth
    signature), spread with a min separation that lets n fit the area (the corpus
    spread, not a tight anchor radius — that isn't what the data shows). Each placed object
    reserves an open approach (``keep_open``) so they cannot wall each other off."""
    keep_open = keep_open if keep_open is not None else set()
    tiles = list(ts)
    weights = [_gauss(canon[t][0], mu, sd) for t in tiles]
    min_sep = max(1.5, 0.6 * math.sqrt(len(ts) / max(n, 1)))
    out = []
    for t in _weighted_spaced(tiles, weights, n * 2, min_sep, rng):
        if len(out) >= n:
            break
        ident = _pick_identity(pool, rng)
        if _fits(ident, t[0], t[1], ts, hard, keep_open):
            o = {**ident, "x": t[0], "y": t[1], "l": level, "_purpose": p}
            _add_hard(hard, o, ts)
            _reserve_approach(o, ts, hard, keep_open)
            out.append(o)
    return out


def _approach_toward(o, mouths, ts, blocked, base):
    """The walkable tile adjacent to o that is closest to an entrance mouth — i.e. the
    guard stands between the loot and the way in (guard-protects-loot by construction)."""
    appr = set()
    for tx, ty, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
        for n in _ring1((tx, ty)):
            if n in ts and n not in blocked:
                appr.add(n)
    appr.discard((o["x"], o["y"]))
    if not appr:
        return None
    tgt = min(mouths, key=lambda m: _dist((o["x"], o["y"]), m)) if mouths else base
    return min(appr, key=lambda t: _dist(t, tgt))


def synthesize_zone(z, canon, rg, border_tiles, seed, level, global_hard,
                    strict_terrain=False,
                    terr_grid=None, catmrf=None, gameplay=True, groups=None):
    """Construct one zone from a (terrain,role) grammar: relational setpiece + carved
    empty skeleton + rim wall with a few gaps + openness-budgeted vegetation."""
    rng = random.Random(seed)
    ts = z["tiles_set"]
    placed, gameplay_hard, reserved, wall = [], set(), set(), set()
    boundary = set(z.get("boundary_tiles") or _zone_boundary(ts))

    # 1. entrances: the map-level shared passages (already few, and aligned with the
    # neighbour zone's gaps). Water-locked zones fall back to a couple of widest gaps.
    bt = [t for t in border_tiles if t in ts]
    if bt:
        runs = _components(set(bt))
        entrance_tiles = set(bt)
    else:
        runs = sorted(_components(set(boundary)), key=len, reverse=True)[:2]
        entrance_tiles = {t for r in runs for t in r}
    mouths = [_centroid_tile(r) for r in runs] or [_centroid_tile(list(ts))]

    # 2. carve the navigable SKELETON first (entrances -> centre): the reserved network gameplay
    # backs onto. Placing the skeleton BEFORE gameplay lets every multi-tile object sit with its
    # blocking body OFF the corridor (in the to-be-vegetation area) and only its visit tile ON the
    # network — so the object's body MERGES with the vegetation wall instead of forming a fresh wall
    # in open ground (the cause of non-carveable enclosures). 1-wide, widened on diagonal steps.
    def _carve_corridor(a, b):
        line = [t for t in _line_tiles(a, b) if t in ts]
        reserved.update(line)
        for (ax, ay), (bx, by) in zip(line, line[1:]):
            if ax != bx and ay != by:            # diagonal step: widen by one orthogonal tile
                w = (bx, ay) if (bx, ay) in ts else (ax, by)
                if w in ts:
                    reserved.add(w)
    centre = _centroid_tile(list(ts))
    for m in mouths:
        _carve_corridor(m, centre)
    reserved.add(centre)

    keep_open = set()

    def _connect_visit(o):
        """Join o's visit tile to the navigable network (a short spur) and keep it open."""
        vts = _visit_tiles(o, ts)
        if not vts:
            return
        if reserved and not (set(vts) & reserved):
            vt = min(vts, key=lambda v: min((v[0] - r[0]) ** 2 + (v[1] - r[1]) ** 2 for r in reserved))
            near = min(reserved, key=lambda r: (r[0] - vt[0]) ** 2 + (r[1] - vt[1]) ** 2)
            _carve_corridor(vt, near)
        reserved.update(vts)

    def _place_backed(pool, n, mu, sd, purpose):
        """Place up to n objects of ``purpose`` BACKED INTO the to-be-vegetation: the blocking body
        must not sit on the reserved corridor (so it never walls the navigable net), and its visit
        tile is joined to the network. Positioned by the corpus depth signature, min-spaced."""
        if not pool or n <= 0:
            return []
        tiles = list(ts)
        weights = [_gauss(canon[t][0], mu, sd) for t in tiles]
        min_sep = max(1.5, 0.6 * math.sqrt(len(ts) / max(n, 1)))
        out = []
        for t in _weighted_spaced(tiles, weights, n * 3, min_sep, rng):
            if len(out) >= n:
                break
            ident = _pick_identity(pool, rng)
            if not _fits(ident, t[0], t[1], ts, gameplay_hard, keep_open):
                continue
            blk = [(cx, cy) for cx, cy, b in OR.mask_cells(ident["mask"], t[0], t[1]) if b]
            if any(c in reserved for c in blk):
                continue                         # body must not wall the navigable corridor (the rule)
            o = {**ident, "x": t[0], "y": t[1], "l": level, "_purpose": purpose}
            _add_hard(gameplay_hard, o, ts)
            _reserve_approach(o, ts, gameplay_hard, keep_open)
            _connect_visit(o)
            out.append(o)
        return out

    # 3+4. anchor (town) + satellites (mines/dwellings/visitables): each backed into vegetation with
    # its visit tile on the network. Skipped entirely for non-gameplay (decorative island) zones —
    # gameplay on a water-locked island can never be reached, so islands carry only scenery.
    anchor_obj = anchor_tile = None
    base = centre
    guardables = []
    A = rg.get("anchor")
    if gameplay:
        if A and A.get("idents"):
            objs = _place_backed(A["idents"], 1, A.get("depth_mu", 0.5), A.get("depth_sd", 0.2),
                                 A.get("purpose") or "TOWN")
            if objs:
                anchor_obj = objs[0]
                anchor_tile = (anchor_obj["x"], anchor_obj["y"])
                placed.append(anchor_obj)
        base = anchor_tile or centre
        terr_name = TNAME.get(z["terrain_type"])
        glib = groups or {}
        G = rg.get("guard")
        if anchor_obj and (A.get("purpose") in GUARDABLE):
            guardables.append(anchor_obj)

        def _entries(idlist, purpose):
            """Identity entries for `_pick_identity`: the learned group idents if present, else the
            ontology gameplay pool at unit weight. Group STRUCTURE is corpus; IDENTITY is ontology."""
            if idlist:
                return idlist
            pool = ON.gameplay_pool(terr_name, purpose)
            return [{"identity": i, "weight": 1} for i in pool] if pool else None

        def _place_member_near(entries, purpose, ax, ay, rmu, rsd):
            """One member of `purpose` near (ax,ay) at radius ~N(rmu,rsd), backed into to-be-
            vegetation (blocking body OFF the reserved corridor), visit tile joined to the network."""
            if not entries:
                return None
            rad = max(1.0, rng.gauss(rmu, rsd)) if rsd > 0 else rmu
            cands = [t for t in ts if t not in gameplay_hard]
            cands.sort(key=lambda t: (abs((abs(t[0] - ax) + abs(t[1] - ay)) - rad),
                                      (t[0] * 73856093 ^ t[1] * 19349663) & 0xFFFF))
            for t in cands[:80]:
                ident = _pick_identity(entries, rng)
                if not _fits(ident, t[0], t[1], ts, gameplay_hard, keep_open):
                    continue
                blk = [(cx, cy) for cx, cy, b in OR.mask_cells(ident["mask"], t[0], t[1]) if b]
                if any(c in reserved for c in blk):
                    continue
                o = {**ident, "x": t[0], "y": t[1], "l": level, "_purpose": purpose}
                _add_hard(gameplay_hard, o, ts)
                _reserve_approach(o, ts, gameplay_hard, keep_open)
                _connect_visit(o)
                return o
            return None

        def _place_group(arch, ganchor):
            """Stamp one group's members around an already-placed visitable/pickable anchor, then ONE
            guard for the whole group at the corpus guard_rate. Returns the placed members + guard."""
            ax, ay = ganchor["x"], ganchor["y"]
            rmu = min(max(arch.get("spread_mu", 3.0), 2.0), 6.0)   # clamp -> compact set-piece
            rsd = min(arch.get("spread_sd", 1.0), 3.0)
            out, drawn = [], 0
            for p, ms in sorted(arch.get("members", {}).items(), key=lambda kv: -kv[1][0]):
                if drawn >= GROUP_MEMBER_CAP or p in SATELLITE_SKIP or p == "GUARD":
                    continue
                n = min(_stochastic_round(ms[0], rng), GROUP_MEMBER_CAP - drawn)
                for _ in range(n):
                    o = _place_member_near(_entries((arch.get("idents") or {}).get(p), p),
                                           p, ax, ay, rmu, rsd)
                    if not o:
                        break
                    out.append(o)
                    drawn += 1
                    if p in GUARDABLE:
                        guardables.append(o)
            if G and G.get("idents") and rng.random() < arch.get("guard_rate", 0.0):
                gtile = _approach_toward(ganchor, mouths, ts, gameplay_hard, base)
                if gtile and gtile not in gameplay_hard:
                    gid = _pick_identity(G["idents"], rng)
                    out.append({**gid, "x": gtile[0], "y": gtile[1], "l": level, "_purpose": "GUARD"})
                    gameplay_hard.add(gtile)
                    reserved.discard(gtile)
            return out

        gp_budget = max(3, z["area"] // 12)
        if glib:
            # ORDER = intentional clusters: place gameplay as GROUPS (anchor + co-occurring members
            # + one guard), not as an independent per-purpose scatter. Anchors are visitable/pickable.
            n_groups = max(1, round(gp_budget / GROUP_MEAN_SIZE))
            if anchor_obj and glib.get(anchor_obj["_purpose"]):
                placed += _place_group(glib[anchor_obj["_purpose"]], anchor_obj)   # town's economy etc.
                n_groups -= 1
            # additional groups: sample an anchor purpose by corpus weight (never an extra TOWN),
            # guaranteeing the first is a visitable so a zone is never left with nothing to visit, and
            # capping blocking-bodied anchors so they never ring the open space into sealed pockets.
            add = [(ap, a.get("weight", 1)) for ap, a in glib.items() if ap != "TOWN"]
            vis = set(ON.visitable_purposes())
            block_cap = max(1, z["area"] // 120)
            n_block = 1 if (anchor_obj and anchor_obj["_purpose"] in BLOCK_ANCHORS) else 0
            for gi in range(max(0, n_groups)):
                pool_c = add
                if gi == 0:
                    pool_c = [c for c in add if c[0] in vis] or add
                if n_block >= block_cap:              # blocking budget spent -> pickable anchors only
                    pool_c = [c for c in pool_c if c[0] in PICK_ANCHORS] or \
                             [c for c in add if c[0] in PICK_ANCHORS] or pool_c
                if not pool_c:
                    break
                ap = rng.choices([c[0] for c in pool_c], weights=[max(c[1], 1) for c in pool_c], k=1)[0]
                if ap in BLOCK_ANCHORS:
                    n_block += 1
                arch = glib[ap]
                objs = _place_backed(_entries((arch.get("idents") or {}).get("_anchor"), ap),
                                     1, 0.5, 0.2, ap)
                if not objs:
                    continue
                placed.append(objs[0])
                if ap in GUARDABLE:
                    guardables.append(objs[0])
                placed += _place_group(arch, objs[0])
        else:
            # legacy fallback (grammar without a _groups library): independent per-purpose satellites
            sats = rg.get("satellites", {})
            tot_d = sum(sp.get("density", 0.0) for sp in sats.values()) or 1.0
            for p, sp in sorted(sats.items(), key=lambda kv: _prio(kv[0])):
                if p in SATELLITE_SKIP:
                    continue
                n = _stochastic_round(gp_budget * sp.get("density", 0.0) / tot_d, rng)
                objs = _place_backed(sp.get("idents"), n, sp.get("depth_mu", 0.5),
                                     sp.get("depth_sd", 0.2), p)
                placed += objs
                if p in GUARDABLE:
                    guardables += objs

    reserved |= keep_open          # the reserved visit-tile approaches stay open through the fill
    reserved -= gameplay_hard

    # 5. guards: a monster GUARDS something. Put one on the approach tile (between the loot and the
    # way in) of every WORTHY guardable — mines, dwellings, banks, reward pickups — so each monster
    # visibly protects a real reward. Minor resource piles are left unguarded (H3 rarely guards a
    # lone wood pile), which is what stops guards reading as random scatter. Plus one per entrance
    # chokepoint. The guard tile leaves the reserved corridor so the obstacle fill keeps its doorway.
    G = rg.get("guard")
    if gameplay and G and G.get("idents"):
        def _put_guard(tile):
            gid = _pick_identity(G["idents"], rng)
            placed.append({**gid, "x": tile[0], "y": tile[1], "l": level, "_purpose": "GUARD"})
            gameplay_hard.add(tile)
            reserved.discard(tile)

        if not (groups or {}):
            # legacy path only: one guard on every WORTHY guardable's approach. The group path
            # already placed one guard per group at the corpus rate, so this would double-guard.
            worthy = [o for o in guardables if o["_purpose"] in GUARD_WORTHY]
            for obj in sorted(worthy, key=lambda o: -_GUARDABLE_PRIO.get(o["_purpose"], 0)):
                gtile = _approach_toward(obj, mouths, ts, gameplay_hard, base)
                if gtile and gtile not in gameplay_hard:
                    _put_guard(gtile)
        for m in mouths:                       # chokepoint guards at the entrance gaps (both paths)
            if m in ts and m not in gameplay_hard:
                _put_guard(m)

    # spatially-correlated category field for the whole zone, Gibbs-sampled from the learned
    # per-terrain MRF (corpus 4-adjacency co-occurrence). One field feeds the rim wall AND the
    # interior fill so the decoration reads as continuous corpus-like stands of one kind with
    # learned clearings (the EMPTY state). None when this terrain has no MRF -> the old marginal
    # category field + openness floor are used as a fallback.
    catmap = _zone_category_field(ts, z["terrain_type"], catmrf, rng)

    def _catmap_grid(minx, miny, NW, NH):
        """Project the zone catmap dict into the [y-miny][x-minx] array `_place_decor_cells`/the
        ridge loops index (EMPTY -> None so those callers fall back to their pool pick)."""
        return [[(lambda c: None if c in (None, EMPTY_CAT) else c)(catmap.get((minx + xx, miny + yy)))
                 for xx in range(NW)] for yy in range(NH)]

    if not strict_terrain:
        # 6. wall: rim band of mountains/trees, minus entrance gaps and reserved lanes.
        # Enrich the learned pool to the full per-terrain obstacle ontology (terrain-pure,
        # whole catalog, flattened weights) so the rim isn't the same few corpus mountains.
        wall_pool = _enriched_decor_pool((rg.get("wall") or {}).get("idents"),
                                         z["terrain_type"], _veg_idents)
        thick = max(WALL_THICK_MIN, int((rg.get("wall") or {}).get("thickness", 1)))
        ring = _rim_band(ts, boundary, thick)
        wall_tiles = sorted(t for t in ring
                            if t not in entrance_tiles and t not in reserved
                            and t not in gameplay_hard)
        if wall_pool and wall_tiles:
            (wminx, wminy, wmaxx, wmaxy), _ = zone_bbox_mask(z["tiles"])
            if catmap is not None:                 # MRF categories (spatially correlated)
                wcatfield = _catmap_grid(wminx, wminy, wmaxx - wminx + 1, wmaxy - wminy + 1)
            else:                                  # fallback: independent marginal category blocks
                wcat_w = _pool_category_weights(wall_pool)
                wcatfield = (_category_field(wmaxx - wminx + 1, wmaxy - wminy + 1,
                                             list(wcat_w), list(wcat_w.values()), rng)
                             if wcat_w else None)
            wobjs = _place_decor_cells({0: wall_tiles}, {0: 0.9}, {0: 1.0},
                                       lambda k: wall_pool, ts, gameplay_hard | reserved,
                                       set(gameplay_hard) | reserved | entrance_tiles, rng, level,
                                       catfield=wcatfield, bbox=(wminx, wminy),
                                       terr_id=z["terrain_type"])
            placed += wobjs
            wall = {(o["x"], o["y"]) for o in wobjs}
    else:
        # 6. BORDER ridge (graph path): terrain-matched mountains & trees along the INTERNAL zone
        # borders only (NOT the map edge), allowed to overlap for a natural ridge. Entrances and a
        # 1-tile margin around every placed object stay clear so nothing gets sealed; no interior
        # scatter. This is the only vegetation on a graph map — the rim defines the zone.
        gh = len(terr_grid) if terr_grid else 0
        gw = len(terr_grid[0]) if gh else 0
        pool = _veg_idents(z["terrain_type"])
        (rminx, rminy, rmaxx, rmaxy), _ = zone_bbox_mask(z["tiles"])
        if catmap is not None:
            rcatfield = _catmap_grid(rminx, rminy, rmaxx - rminx + 1, rmaxy - rminy + 1)
        else:
            rcat_w = _pool_category_weights(pool)
            rcatfield = (_category_field(rmaxx - rminx + 1, rmaxy - rminy + 1,
                                         list(rcat_w), list(rcat_w.values()), rng)
                         if rcat_w else None)
        keep_open = set(entrance_tiles)
        for t in entrance_tiles:
            keep_open.update(_ring1(t))
        for o in placed:
            for cx, cy, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
                keep_open.add((cx, cy))
                keep_open.update(_ring1((cx, cy)))
        for t in sorted(boundary):
            if t in keep_open or t in gameplay_hard or t not in ts:
                continue
            # internal border only: at least one 4-neighbour is another in-bounds zone (skip map edge)
            if not any(0 <= t[0] + dx < gw and 0 <= t[1] + dy < gh
                       and (t[0] + dx, t[1] + dy) not in ts
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                continue
            if not pool:
                break
            cat = rcatfield[t[1] - rminy][t[0] - rminx] if rcatfield else None
            ident = ON.decode_identity(cat, z["terrain_type"], rng) if cat else None
            if ident is None:
                ident = _pick_identity(pool, rng)
            cells = list(OR.mask_cells(ident["mask"], t[0], t[1]))
            if any((cx, cy) in gameplay_hard for cx, cy, blk in cells if blk):
                continue                       # never bury gameplay (overlap with decoration is fine)
            if any(cx <= 0 or cy <= 0 or cx >= gw - 1 or cy >= gh - 1 for cx, cy, _b in cells):
                continue                       # no vegetation on the map edge (footprint included)
            placed.append({**ident, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})
            wall.add(t)

        # 6b. INTERIOR decoration (graph path): on top of the border ridge, terrain-matched flora /
        # small obstacles scattered through the zone INTERIOR with clearings (a value-noise field
        # gives clumps and open ground). Never on the border (that is the ridge), never on an
        # entrance / reserved lane, never burying gameplay; carve-eligible so the zone stays walkable.
        ipool = _interior_idents(z["terrain_type"])
        interior = [t for t in ts if t not in boundary and t not in gameplay_hard
                    and t not in reserved and t not in entrance_tiles and t not in wall]
        if ipool and interior:
            (minx, miny, maxx, maxy), _ = zone_bbox_mask(z["tiles"])
            NW, NH = maxx - minx + 1, maxy - miny + 1
            noise = _value_noise(NW, NH, max(2, min(NW, NH) // 5), rng)
            if catmap is not None:
                catfield = _catmap_grid(minx, miny, NW, NH)
            else:
                cat_w = _pool_category_weights(ipool)
                catfield = _category_field(NW, NH, list(cat_w), list(cat_w.values()), rng) if cat_w else None
            interior.sort(key=lambda t: noise[t[1] - miny][t[0] - minx], reverse=True)
            blockset = gameplay_hard | reserved
            for t in interior[:max(1, int(INTERIOR_SCALE * len(interior)))]:
                if noise[t[1] - miny][t[0] - minx] < INTERIOR_THRESH:
                    break                          # below the clump threshold = clearing
                cat = catfield[t[1] - miny][t[0] - minx] if catfield else None
                ident = ON.decode_identity(cat, z["terrain_type"], rng) if cat else None
                if ident is None:
                    ident = _pick_identity(ipool, rng)
                cells = list(OR.mask_cells(ident["mask"], t[0], t[1]))
                if any((cx, cy) in blockset for cx, cy, blk in cells if blk):
                    continue                       # never bury gameplay or fill a reserved lane
                if any(cx <= 0 or cy <= 0 or cx >= gw - 1 or cy >= gh - 1 for cx, cy, _b in cells):
                    continue                       # keep the map edge clear (footprint included)
                placed.append({**ident, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})
                for cx, cy, blk in cells:          # carve-eligible if it ever seals the interior
                    if blk and (cx, cy) in ts:
                        wall.add((cx, cy))
                if rng.random() < OVERLAY_PROB:    # 2-high stack: non-blocking flora overlay
                    ov = _overlay_ident(z["terrain_type"], rng)
                    if ov and ov.get("animation") != ident.get("animation") and not any(
                            cx <= 0 or cy <= 0 or cx >= gw - 1 or cy >= gh - 1
                            for cx, cy, _b in OR.mask_cells(ov["mask"], t[0], t[1])):
                        placed.append({**ov, "x": t[0], "y": t[1], "l": level, "_purpose": "DECORATION"})

    # 7. vegetation: CLUMPED with clearings (overlapping clumps where a smooth noise
    # field is high, open ground where it is low) so interiors breathe; the per-cell
    # count is the learned openness budget, concentrated into the clumps not spread flat.
    # interior cover is mostly OBSTACLE vegetation (mountains/trees/rocks) in coherent stands
    # with carved clearings — the obstacles are what make the terrain read as wooded/mountainous
    # rather than empty, and they shape the navigable pockets where gameplay sits.
    veg_pool = _enriched_decor_pool((rg.get("veg") or {}).get("idents"),
                                    z["terrain_type"], _veg_idents)
    if strict_terrain:
        veg_pool = None            # graph path: vegetation belongs on the BORDER belt only — no
        #                            interior scatter (that was the clutter); the rim defines zones.
    openness = rg.get("openness")
    if veg_pool and catmap is not None:
        # PLAN A — the learned category MRF drives BOTH which category fills a tile AND where the
        # zone stays clear (the EMPTY state): the corpus's joint spatial texture, replacing the
        # value-noise clumping + the coverage floor. Each free tile whose MRF category is a real
        # obstacle category gets that category's sprite; EMPTY tiles are the learned clearings.
        # Reserved corridors + gameplay are skipped (stay open) and the global carve is the safety
        # net, so coverage is whatever the corpus says — not a hardcoded target.
        blockset = gameplay_hard | reserved
        used = set(gameplay_hard) | reserved | wall | entrance_tiles
        for t in sorted(ts):
            if t in used:
                continue
            cat = catmap.get(t)
            if not cat or cat == EMPTY_CAT:
                continue                       # a learned clearing — leave it open
            ident = ON.decode_identity(cat, z["terrain_type"], rng)
            if ident is None:                  # category empty on this terrain -> pool fallback
                ident = _pick_identity(veg_pool, rng)
            if any((cx, cy) in blockset for cx, cy, blk in
                   OR.mask_cells(ident["mask"], t[0], t[1]) if blk):
                ident = _one_tile_obstacle(z["terrain_type"], rng)   # don't clip a corridor
                if ident is None:
                    continue
            _stack_decor(placed, ident, t, level, z["terrain_type"], rng)
            used.add(t)
    elif veg_pool and openness:
        (minx, miny, maxx, maxy), _ = zone_bbox_mask(z["tiles"])
        NW, NH = maxx - minx + 1, maxy - miny + 1
        noise = _value_noise(NW, NH, max(2, min(NW, NH) // 5), rng)
        # coherent same-kind patches: which CATEGORY fills each tile (a stand of one species),
        # weighted by the pool's category mix; the concrete sprite is still varied within it.
        cat_w = _pool_category_weights(veg_pool)
        catfield = _category_field(NW, NH, list(cat_w), list(cat_w.values()), rng) if cat_w else None

        def clump(t):
            return noise[t[1] - miny][t[0] - minx]
        cells = collections.defaultdict(list)
        for t in sorted(ts):
            d, s = canon[t]
            cells[(_depth_bin(d), _sweep_bin(s))].append(t)
        blockset = gameplay_hard | reserved
        used = set(gameplay_hard) | reserved | wall | entrance_tiles
        for (k, s2), tl in cells.items():
            op = openness[k][s2] if k < len(openness) and s2 < len(openness[k]) else 0.85
            free = sorted((t for t in tl if t not in used), key=clump, reverse=True)
            if not free:
                continue
            # obstacle COVERAGE of the FREE (non-corridor, non-gameplay) tiles: the learned openness
            # gives the relative shape (where the corpus left clearings), floored high so interiors
            # are mostly wooded/rocky; the densest tiles by noise fill first -> stands with gaps. The
            # reserved skeleton lanes + object aprons stay clear, so the cover IS the navigable carve.
            cover = max(VEG_SCALE * max(0.0, 1.0 - op), MIN_INTERIOR_COVER)
            n = _stochastic_round(cover * len(free), rng)   # cover of the FREE (non-corridor) tiles
            if n <= 0:
                continue
            for t in free[:n]:                 # fill the densest tiles by noise first
                cat = catfield[t[1] - miny][t[0] - minx] if catfield else None
                ident = ON.decode_identity(cat, z["terrain_type"], rng) if cat else None
                if ident is None:              # category empty on this terrain -> pool fallback
                    ident = _pick_identity(veg_pool, rng)
                if any((cx, cy) in blockset for cx, cy, blk in
                       OR.mask_cells(ident["mask"], t[0], t[1]) if blk):
                    # the multi-tile obstacle would clip a corridor/gameplay -> drop a single-tile
                    # obstacle on this free tile instead, so the gap is still covered, not left open.
                    ident = _one_tile_obstacle(z["terrain_type"], rng)
                    if ident is None:
                        continue
                _stack_decor(placed, ident, t, level, z["terrain_type"], rng)
                used.add(t)

    # 8. reachability safety: open space must reach gameplay; carve the wall if not.
    blocked = set(gameplay_hard) | wall
    for o in placed:
        if o["_purpose"] == "DECORATION":
            for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if blk and (cx, cy) in ts:
                    blocked.add((cx, cy))
    hard_objs = [o for o in placed if o["_purpose"] not in ("DECORATION", "GUARD")]
    ok, _ = _stretch_traversable(ts, blocked, hard_objs)
    if not ok and wall:
        kept = set(wall)
        _carve_connect(set(ts), kept, gameplay_hard | reserved)
        carved = wall - kept
        if carved:
            placed = [o for o in placed
                      if not (o["_purpose"] == "DECORATION" and (o["x"], o["y"]) in carved)]

    global_hard |= gameplay_hard
    return placed


def _zone_passages(zones, zone_label, W, H, allowed_pairs=None):
    """For each adjacent land-zone PAIR, choose ONE shared passage and mark the open
    tiles on BOTH sides (a 3-tile window around the interface midpoint). Computing the
    gap at the map level — not per zone — guarantees the two zones' wall gaps ALIGN, so
    regions actually interconnect (decision: entrances at real zone borders).

    ``allowed_pairs`` (from the graph planner): if given, open ONLY those zone-id pairs;
    every other interface stays sealed by the rim-wall vegetation belt (the planned graph's
    non-edges become solid mountain/forest separators with no chokepoint)."""
    iface = collections.defaultdict(list)
    for zid, z in zones.items():
        for (x, y) in (z.get("boundary_tiles") or []):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and zone_label[ny][nx] >= 0 \
                        and zone_label[ny][nx] != zid:
                    iface[(zid, zone_label[ny][nx])].append((x, y))
                    break
    open_tiles = collections.defaultdict(set)
    seen = set()
    for (a, b), atiles in iface.items():
        if (a, b) in seen:
            continue
        if allowed_pairs is not None and (a, b) not in allowed_pairs:
            seen.add((a, b))
            seen.add((b, a))
            continue
        seen.add((a, b))
        seen.add((b, a))
        btiles = iface.get((b, a))
        if not atiles or not btiles:
            continue
        ax = sum(x for x, _ in atiles) / len(atiles)
        ay = sum(y for _, y in atiles) / len(atiles)
        amid = min(atiles, key=lambda t: (t[0] - ax) ** 2 + (t[1] - ay) ** 2)
        bmid = min(btiles, key=lambda t: (t[0] - amid[0]) ** 2 + (t[1] - amid[1]) ** 2)
        open_tiles[a] |= {t for t in atiles if abs(t[0] - amid[0]) + abs(t[1] - amid[1]) <= 1}
        open_tiles[b] |= {t for t in btiles if abs(t[0] - bmid[0]) + abs(t[1] - bmid[1]) <= 1}
    return open_tiles


def _assign_roles(zones, grammar, min_area, rng):
    """One coherent role per land zone, sampled from the terrain's corpus role mix;
    guarantee at least one town zone (drives player count)."""
    roles = {}
    for zid in sorted(zones):
        z = zones[zid]
        if z["area"] < min_area:
            continue
        g = grammar.get(TNAME.get(z["terrain_type"]))
        if not g:
            roles[zid] = "passage"
            continue
        items = [(r, g[r]["weight"]) for r in sorted(g)]
        tot = sum(w for _, w in items)
        x = rng.random() * tot
        acc, chosen = 0, items[-1][0]
        for r, w in items:
            acc += w
            if x <= acc:
                chosen = r
                break
        roles[zid] = chosen
    if "town" not in roles.values():
        cand = [zid for zid in roles
                if (grammar.get(TNAME.get(zones[zid]["terrain_type"])) or {}).get("town")]
        if cand:
            roles[max(cand, key=lambda z: zones[z]["area"])] = "town"
    return roles


def _place_relational(zones, regions, objects, grammar, rng, level, two_level):
    """Place relational objects only as COMPLETE matched sets, keyed by subtype (color /
    portal id): one-way portal entrance+exit and two-way portal pairs in DIFFERENT regions;
    a border gate/guard with its same-colour keymaster tent (the key in another region, so
    it must be found first); a few standalone seer huts. NEVER a lone half of a pair.
    Subterranean gates are skipped unless the map has an underground (they need a partner on
    the other level); whirlpools need paired open water and are deferred."""
    cat = grammar.get("_relational") or {}
    rids = list(regions)
    if len(cat) == 0 or len(rids) < 2:
        return []
    occupied = set()
    for o in objects:
        for cx, cy, _b in OR.mask_cells(o["mask"], o["x"], o["y"]):
            occupied.add((cx, cy))
    free = {zid: [t for t in zones[zid]["tiles"] if t not in occupied] for zid in rids}
    for zid in rids:
        rng.shuffle(free[zid])
    out = []

    def put(zid, ident, purpose):
        if not free.get(zid):
            return False
        t = free[zid].pop()
        out.append({**ident, "x": t[0], "y": t[1], "l": level, "_purpose": purpose})
        for cx, cy, _b in OR.mask_cells(ident["mask"], t[0], t[1]):
            occupied.add((cx, cy))
        return True

    def colors(a, b=None):
        ca = set(cat.get(a, {}))
        return sorted(ca & set(cat.get(b, {}))) if b else sorted(ca)

    ow = colors("monolithOneWayEntrance", "monolithOneWayExit")
    for c in ow[:max(0, len(rids) // 3)]:                  # entrance here, exit elsewhere
        a, b = rng.sample(rids, 2)
        put(a, cat["monolithOneWayEntrance"][c], "TRANSPORT")
        put(b, cat["monolithOneWayExit"][c], "TRANSPORT")
    for c in colors("monolithTwoWay")[:max(0, len(rids) // 3)]:   # two ends, same id
        a, b = rng.sample(rids, 2)
        put(a, cat["monolithTwoWay"][c], "TRANSPORT")
        put(b, cat["monolithTwoWay"][c], "TRANSPORT")
    for gatetype in ("borderGate", "borderGuard"):         # gate + same-colour key
        cols = colors("keymasterTent", gatetype)
        for c in cols[:max(0, len(rids) // 4)]:
            a, b = rng.sample(rids, 2)
            put(a, cat["keymasterTent"][c], "QUEST_GATE")   # the key (reachable first)
            put(b, cat[gatetype][c], "QUEST_GATE")          # the gate it unlocks
    sh = colors("seerHut")
    for i in range(min(len(sh), max(1, len(rids) // 4))):  # standalone quest givers
        put(rng.choice(rids), cat["seerHut"][sh[i]], "QUEST_GATE")
    return out


def _seed_to_zone(x, y, zones, zone_label, W, H, min_area):
    if 0 <= x < W and 0 <= y < H:
        z = zone_label[y][x]
        if z >= 0 and z in zones and zones[z]["area"] >= min_area:
            return z
    return None


def _roles_from_seeds(zones, zone_label, role_seeds, base, W, H, min_area):
    """Override the per-zone roles for zones containing a planned graph node's seed tile
    (mapgraph); zones with no seed keep their ``base`` (grammar-sampled) role."""
    roles = dict(base)
    for (x, y, role) in role_seeds:
        z = _seed_to_zone(x, y, zones, zone_label, W, H, min_area)
        if z is not None:
            roles[z] = role
    return roles


def _allowed_pairs(zones, zone_label, edge_seeds, W, H, min_area):
    """Map planned graph edges (pairs of node seed coords) to the zone-id pairs that should
    have a passage; every other interface stays sealed by its vegetation rim wall."""
    pairs = set()
    for (pa, pb) in edge_seeds:
        za = _seed_to_zone(pa[0], pa[1], zones, zone_label, W, H, min_area)
        zb = _seed_to_zone(pb[0], pb[1], zones, zone_label, W, H, min_area)
        if za is not None and zb is not None and za != zb:
            pairs.add((za, zb))
            pairs.add((zb, za))
    return pairs


def _global_reachability_carve(objects, terrain, W, H, grow=True):
    """GUARANTEE global connectivity after the dense obstacle fill: the open land that touches
    gameplay must form ONE connected component. Find the gameplay-touching open components and,
    while there is more than one, carve the cheapest line of obstacles (a 0-1 BFS that pays 1 to
    cross an obstacle tile, 0 to walk open land) between the largest and the rest — removing the
    decoration objects on the carved path. Water and gameplay footprints are barriers, never carved.
    Returns the surviving objects (decoration removed along carved paths)."""
    from collections import deque
    # 4-connectivity ONLY: must match the movement model in traverse.passable_grid (NB4). An
    # 8-connected carve would "connect" tiles diagonally that the hero cannot actually walk between,
    # reporting a basin the game can't traverse (the cause of near-zero reachable maps).
    NB = [(-1, 0), (1, 0), (0, -1), (0, 1)]

    def land(x, y):
        return 0 <= x < W and 0 <= y < H and terrain[y][x]["t"] < 8

    blocked = [[False] * W for _ in range(H)]
    carveable = [[False] * W for _ in range(H)]      # obstacle tile we may remove
    decor_at = collections.defaultdict(set)          # (x,y) -> decoration object indices covering it
    for i, o in enumerate(objects):
        if o.get("l", 0) != 0:
            continue
        dec = o["_purpose"] == "DECORATION"
        for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk and 0 <= cx < W and 0 <= cy < H:
                blocked[cy][cx] = True
                if dec:
                    decor_at[(cx, cy)].add(i)
                    carveable[cy][cx] = True
    for o in objects:                                # gameplay footprints are never carveable
        if o.get("l", 0) == 0 and o["_purpose"] != "DECORATION":
            for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if blk and 0 <= cx < W and 0 <= cy < H:
                    carveable[cy][cx] = False

    def is_open(x, y):
        return land(x, y) and not blocked[y][x]

    def _carve_decor_on(t):                          # un-bury a tile by removing the decoration on it
        for i in list(decor_at.get(t, ())):
            if i in removed:
                continue
            removed.add(i)
            for cx, cy, blk in OR.mask_cells(objects[i]["mask"], objects[i]["x"], objects[i]["y"]):
                if blk and 0 <= cx < W and 0 <= cy < H:
                    blocked[cy][cx] = False
                    carveable[cy][cx] = False
                    decor_at[(cx, cy)].discard(i)

    # Seed the connectivity search from every open tile adjacent to a gameplay object (broad — this
    # is what keeps the navigable network large), AND from each object's VISIT TILE (the 'A' tile the
    # hero stands on). If an object's visit tiles are ALL sealed by decoration, un-bury the best one
    # so a fully-walled object still becomes reachable. The carve below then unions everything into
    # ONE start-reachable component.
    removed = set()
    gp_adj = set()
    for o in objects:
        if o.get("l", 0) != 0 or o["_purpose"] in ("DECORATION", "GUARD"):
            continue
        cells = list(OR.mask_cells(o["mask"], o["x"], o["y"]))
        vts = [t for t in _mask_anchor_cells(o["mask"], o["x"], o["y"]) if land(*t)]
        if not vts:                                  # no 'A' (e.g. town) -> open land beside the body
            blk = {(cx, cy) for cx, cy, b in cells if b}
            vts = [n for cx, cy, _b in cells for n in _ring1((cx, cy)) if land(*n) and n not in blk]
        if vts and not any(is_open(*t) for t in vts):   # fully sealed -> un-bury the first visit tile
            _carve_decor_on(vts[0])
        gp_adj.update(t for t in vts if is_open(*t))
        for cx, cy, _b in cells:                      # broad: open tiles adjacent to the body
            for dx, dy in NB + [(0, 0)]:
                if is_open(cx + dx, cy + dy):
                    gp_adj.add((cx + dx, cy + dy))

    def gp_components():
        seen, comps = set(), []
        for t in gp_adj:
            if t in seen or not is_open(*t):
                continue
            q, comp = deque([t]), [t]
            seen.add(t)
            while q:
                x, y = q.popleft()
                for dx, dy in NB:
                    n = (x + dx, y + dy)
                    if n not in seen and is_open(*n):
                        seen.add(n)
                        q.append(n)
                        comp.append(n)
            comps.append(comp)
        return comps

    def _carve_to(roots, is_target):
        """0-1 BFS from the open ``roots`` (0 to walk open, 1 to cross a carveable obstacle); carve
        the cheapest line of removable decoration to the nearest tile satisfying ``is_target``.
        Returns True if a path was carved."""
        dist, prev, dq, tgt = {}, {}, deque(), None
        for t in roots:
            dist[t] = 0
            dq.append(t)
        while dq:
            cur = dq.popleft()
            if cur not in roots and is_target(cur):
                tgt = cur
                break
            cx, cy = cur
            for dx, dy in NB:
                nx, ny = cx + dx, cy + dy
                if not land(nx, ny):
                    continue
                if blocked[ny][nx] and not carveable[ny][nx]:
                    continue                         # water / gameplay barrier
                w = 1 if blocked[ny][nx] else 0
                nd = dist[cur] + w
                if (nx, ny) not in dist or nd < dist[(nx, ny)]:
                    dist[(nx, ny)] = nd
                    prev[(nx, ny)] = cur
                    (dq.appendleft if w == 0 else dq.append)((nx, ny))
        if tgt is None:
            return False
        node = tgt
        while node is not None:
            for i in list(decor_at.get(node, ())):
                if i in removed:
                    continue
                removed.add(i)
                for cx, cy, blk in OR.mask_cells(objects[i]["mask"], objects[i]["x"], objects[i]["y"]):
                    if blk and 0 <= cx < W and 0 <= cy < H:
                        blocked[cy][cx] = False
                        carveable[cy][cx] = False
                        decor_at[(cx, cy)].discard(i)
            node = prev.get(node)
        return True

    def _flood_open(seeds):                          # connected open tiles reachable from seeds
        seen = set(s for s in seeds if is_open(*s))
        dq = deque(seen)
        while dq:
            x, y = dq.popleft()
            for dx, dy in NB:
                n = (x + dx, y + dy)
                if n not in seen and is_open(*n):
                    seen.add(n)
                    dq.append(n)
        return seen

    # 1) connect every gameplay-touching open component into ONE (so all gameplay is mutually reachable)
    for _ in range(400):
        comps = gp_components()
        if len(comps) <= 1:
            break
        comps.sort(key=len, reverse=True)
        root, others = set(comps[0]), set().union(*(set(c) for c in comps[1:]))
        if not _carve_to(root, lambda t: t in others):
            break

    # 2) GROW the START TOWN's navigable basin to a corpus-like fraction of the open land: carve the
    # cheapest decoration into the nearest unreached open pocket until the basin reaches NAV_REACH_FRAC
    # of all open tiles. The basin must be the START's region (the town reaching the most open land —
    # what _pick_main_town / traverse use as the start), NOT merely the largest gameplay component, or
    # growth widens a region the hero never reaches. This absorbs the surrounding gameplay pockets too.
    # ``grow`` is off for the pure GRAPH layout (its closed zone-separation belts must stay closed —
    # the spanning tree already guarantees connectivity); on for markov/markov-graph, whose dense fill
    # fragments the open space and needs the basin widened.
    if grow:
        def _obj_open_seed(o):
            vts = [t for t in _mask_anchor_cells(o["mask"], o["x"], o["y"]) if land(*t) and is_open(*t)]
            if vts:
                return vts
            blk = {(cx, cy) for cx, cy, b in OR.mask_cells(o["mask"], o["x"], o["y"]) if b}
            return [n for cx, cy, _b in OR.mask_cells(o["mask"], o["x"], o["y"])
                    for n in _ring1((cx, cy)) if land(*n) and is_open(*n) and n not in blk]
        towns = [o for o in objects if o.get("l", 0) == 0 and o.get("_purpose") == "TOWN"]
        basin = None
        if towns:
            basin = max((_flood_open(_obj_open_seed(o)) for o in towns), key=len, default=None)
        if not basin:
            comps = gp_components()
            basin = set(max(comps, key=len)) if comps else set()
        if basin:
            reach = set(basin)
            open_total = sum(1 for y in range(H) for x in range(W) if is_open(x, y))

            def _grow():
                nonlocal reach
                for _ in range(1200):
                    if len(reach) >= NAV_REACH_FRAC * open_total:
                        return
                    if not _carve_to(reach, lambda t: is_open(*t) and t not in reach):
                        return
                    reach = _flood_open(reach)
            _grow()
            # RESCUE: if the start basin is still tiny it is RINGED BY GAMEPLAY bodies (the town's own
            # economy mines + neighbours), which decoration-only carving cannot escape. As a last
            # resort let non-town gameplay become carveable and reconnect — sacrificing a few objects
            # is far better than a start that reaches almost nothing.
            if len(reach) < 0.20 * open_total:
                for i, o in enumerate(objects):
                    if o.get("l", 0) != 0 or o["_purpose"] in ("DECORATION", "TOWN"):
                        continue
                    for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                        if blk and 0 <= cx < W and 0 <= cy < H and blocked[cy][cx]:
                            decor_at[(cx, cy)].add(i)
                            carveable[cy][cx] = True
                _grow()

    return [o for i, o in enumerate(objects) if i not in removed] if removed else objects


def _pick_main_town(objects, terrain, W, H):
    """The starting town must sit on the LARGEST connected landmass so the map is playable from
    the start (markov terrain scatters zones across many water-separated islands; a start stranded
    on a 30-tile island reaches almost nothing). Returns ``{"x","y"}`` in the main_town convention
    (town anchor minus (2,2)), or None. Picks the town whose reachable passable land is biggest."""
    import traverse as TR
    blocked, _W, _H = TR.passable_grid({"terrain": [terrain], "objects": objects}, 0)
    NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    towns = [o for o in objects if o.get("l", 0) == 0 and TR.TYPE2PURPOSE.get(o.get("type")) == "TOWN"]
    if not towns:
        return None

    def reach_size(o):
        seeds = set()
        for ax, ay, ch in TR._mask_cells(o["x"], o["y"], o["mask"]):
            if ch != "A":
                continue
            for dx, dy in NB4 + [(0, 0)]:
                nx, ny = ax + dx, ay + dy
                if 0 <= nx < W and 0 <= ny < H and not blocked[ny][nx]:
                    seeds.add((nx, ny))
        seen, st = set(seeds), list(seeds)
        while st:
            x, y = st.pop()
            for dx, dy in NB4:
                n = (x + dx, y + dy)
                if 0 <= n[0] < W and 0 <= n[1] < H and not blocked[n[1]][n[0]] and n not in seen:
                    seen.add(n)
                    st.append(n)
        return len(seen)

    best = max(towns, key=reach_size)
    return {"x": best["x"] - 2, "y": best["y"] - 2, "l": best.get("l", 0)}


def generate_map(terrain, W, H, seed=0, min_area=12, name=None, grammar=None, plan=None):
    """Synthesize a full map: assign each land zone a role, then construct it from the
    learned feature grammar. Zones smaller than min_area stay bare terrain.

    ``plan`` carries ``role_seeds`` (per-zone role override by seed tile) and ``edge_seeds``
    (which zone borders get a passage; the rest stay walled)."""
    grammar = grammar or _load_or_build_grammar()
    rng = random.Random(seed)
    zones, zone_label, canon = _segment_level(terrain)
    roles = _assign_roles(zones, grammar, min_area, rng)
    allowed_pairs = None
    if plan and plan.get("role_seeds"):
        roles = _roles_from_seeds(zones, zone_label, plan["role_seeds"], roles, W, H, min_area)
    if plan and plan.get("edge_seeds"):
        allowed_pairs = _allowed_pairs(zones, zone_label, plan["edge_seeds"], W, H, min_area)
    passages = _zone_passages(zones, zone_label, W, H, allowed_pairs)

    # graph-planned maps keep each zone's decoration terrain-pure (border-ridge only); graph-on-markov
    # opts out (strict_terrain=False in its plan) so it keeps the markov MRF interior fill + water.
    strict_terrain = bool(plan) and plan.get("strict_terrain", True)
    objects, report, global_hard = [], [], set()
    for zid in sorted(zones):
        z = zones[zid]
        if z["area"] < min_area:
            continue
        terr = TNAME.get(z["terrain_type"])
        role = roles.get(zid, "passage")
        gt = grammar.get(terr) or {}
        rg = gt.get(role) or gt.get("passage")
        if not rg:
            report.append((zid, terr, z["area"], role, 0))
            continue
        borders = sorted(passages.get(zid, set()))
        catmrf = (grammar.get("_catmrf") or {}).get(terr)
        groups = (grammar.get("_groups") or {}).get(terr)
        play_zones = plan.get("play_zones") if plan else None
        gameplay = play_zones is None or zid in set(play_zones)
        placed = synthesize_zone(z, canon[zid], rg, borders,
                                 seed ^ (zid * 2654435761 & 0xFFFFFFFF), 0, global_hard,
                                 strict_terrain=strict_terrain, terr_grid=terrain, catmrf=catmrf,
                                 gameplay=gameplay, groups=groups)
        objects += placed
        report.append((zid, terr, z["area"], role, len(placed)))
    if PLACE_RELATIONAL:
        # Relational sets (transport portals, border gates + their keymaster tents, seer huts):
        # only meaningful as correctly matched, reachability-ordered pairs. Disabled for now —
        # the current pairing/placement is not coherent, so we exclude them entirely rather than
        # emit improper gates/tents/portals.
        objects += _place_relational(zones, sorted(roles), objects, grammar, rng, 0, False)
    # GLOBAL reachability: the dense per-zone obstacle fill can leave zones sealed from each other;
    # carve the minimal obstacle lines so every gameplay object sits in one connected open network.
    objects = _global_reachability_carve(objects, terrain, W, H, grow=not strict_terrain)
    import traverse as TR
    n_towns = sum(1 for o in objects if TR.TYPE2PURPOSE.get(o.get("type")) == "TOWN")
    fm = {"name": name or f"Generated-s{seed}", "width": W, "height": H,
          "twoLevel": False, "players": max(1, n_towns), "terrain": [terrain],
          "objects": objects}
    main_town = _pick_main_town(objects, terrain, W, H)   # start on the largest landmass
    if main_town is not None:
        fm["main_town"] = main_town
    return fm, report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_segmentation(name: str, out_path: str):
    from PIL import Image, ImageDraw
    fm = OR.load_faithful(name)
    W, H = fm["width"], fm["height"]
    imgs, tables = [], []
    for L, lvl in enumerate(fm["terrain"]):
        zones, zone_label = TS.segment(lvl, subdivide=False)
        zone_objs, _ = _bucket_objects(fm["objects"], L, zone_label, zones, W, H)
        img = Image.new("RGB", (W * TILE, H * TILE), (10, 10, 10))
        px = img.load()
        for y in range(H):
            for x in range(W):
                base = RD.TERRAIN_RGB.get(lvl[y][x]["t"], (0, 0, 0))
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
    import render_editor as RE
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


def render_fm(fm, out_path, title=""):
    """Render a generated faithful map (whose objects still carry `_purpose`) directly,
    using the same banded paint order as the patch panels so flat terrain overlays and
    stacked objects don't hide gameplay. Surface level only. Avoids the vmap round-trip
    (read_vmap drops purpose), so this is the correct render path for generated maps."""
    import render_editor as RE
    from PIL import Image, ImageDraw
    T = RE.TILE
    terr = fm["terrain"][0]
    H, W = len(terr), len(terr[0])
    canvas = Image.new("RGB", (W * T, H * T), (0, 0, 0))
    for y in range(H):
        for x in range(W):
            canvas.paste(RE.terr_tile_img(FA.tile_string(terr[y][x])).convert("RGB"), (x * T, y * T))
    objs = [o for o in fm["objects"] if o.get("l", 0) == 0]
    miss = 0
    for o in sorted(objs, key=lambda o: (_paint_layer(o), o["y"], o["x"])):
        anim = o.get("animation", "")
        groups = RE.get_def(anim) if anim else None
        if not groups or not groups[0]:
            miss += 1
            continue
        sp = groups[0][0]
        canvas.paste(sp.convert("RGB"),
                     ((o["x"] + 1) * T - sp.size[0], (o["y"] + 1) * T - sp.size[1]), sp.split()[3])
    if miss:
        print(f"  {miss} objects with missing sprites")
    if title:
        ImageDraw.Draw(canvas).text((4, 4), title, fill=(255, 255, 255))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return out_path


def _render_panel(pan, title=None):
    """Render ONE zone panel (cropped to the zone) with REAL H3 sprites at editor
    resolution (32px), only the zone's own tiles, transparent elsewhere. RGBA."""
    import render_editor as RE
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
                img.paste(RE.terr_tile_img(FA.tile_string(terr[y][x])),
                          ((x - x0) * T, (y - y0) * T))
    for (ox, oy, sp) in draw:
        img.paste(sp, ((ox - x0 + 1) * T - sp.size[0], (oy - y0 + 1) * T - sp.size[1]), sp)
    t = title if title is not None else pan.get("title")
    if t:
        ImageDraw.Draw(img).text((4, 4), t, fill=(255, 255, 255, 255))
    return img


def _label_image(img, title):
    from PIL import ImageDraw
    ImageDraw.Draw(img).text((4, 4), title, fill=(255, 255, 255, 255))
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


def _patch_panel(patch, pad=4):
    """A _render_panel dict for a stored patch in its own bbox-relative frame:
    terrain = the patch's terrain on its mask, objects at anchor_off. `pad` tiles of
    empty margin so bottom-right-anchored sprites (extend up & left) aren't clipped.
    Boundary objects anchored OFF the zone have anchor_off outside the mask bbox, so the
    panel origin is shifted to cover both the mask tiles and every object anchor."""
    mask_rel = [(int(dx), int(dy)) for dx, dy in patch["mask_rel"]]
    tt = patch["terrain_type"]
    axs = [o["anchor_off"][0] for o in patch["objects"]]
    ays = [o["anchor_off"][1] for o in patch["objects"]]
    minx = min([0] + axs) - pad
    miny = min([0] + ays) - pad
    w = max([dx for dx, _ in mask_rel] + axs) - minx + 1 + pad
    h = max([dy for _, dy in mask_rel] + ays) - miny + 1 + pad
    tiles = {(dx - minx, dy - miny) for dx, dy in mask_rel}
    terr = [[_cell(tt, x, y) for x in range(w)] for y in range(h)]
    objs = [{**o["identity"], "x": o["anchor_off"][0] - minx, "y": o["anchor_off"][1] - miny,
             "_purpose": o["purpose"]} for o in patch["objects"]]
    return {"terr": terr, "tiles": tiles, "objs": objs, "W": w, "H": h}


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


def render_patch_panel(patch, title=None):
    """RGBA render of one patch in isolation (real H3 sprites, terrain only on-mask)."""
    return _render_panel(_patch_panel(patch), title=title)


def _flatten(img, bg=PATCH_BG):
    from PIL import Image
    out = Image.new("RGB", img.size, bg)
    out.paste(img, (0, 0), img)
    return out


def _grid_montage(cells, out_path, cols=10, thumb=200, pad=6, caph=13, bg=PATCH_BG):
    """Contact sheet: each cell = (RGBA panel, caption) downscaled into a thumb box."""
    from PIL import Image, ImageDraw
    cw, ch = thumb + pad * 2, thumb + caph + pad * 2
    rows = max(1, -(-len(cells) // cols))
    canvas = Image.new("RGB", (cols * cw, rows * ch), bg)
    draw = ImageDraw.Draw(canvas)
    for i, (img, cap) in enumerate(cells):
        r, c = divmod(i, cols)
        scale = min(thumb / max(img.width, 1), thumb / max(img.height, 1), 1.0)
        nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        im = img.resize((nw, nh), Image.NEAREST)
        ox = c * cw + pad + (thumb - nw) // 2
        oy = r * ch + pad + (thumb - nh) // 2
        canvas.paste(im, (ox, oy), im)
        draw.text((c * cw + pad, r * ch + pad + thumb + 1), cap[:36], fill=(205, 205, 210))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return out_path


def write_patch_renders(out_dir=None, lib_dir=None, names=None, terrains=None,
                        individual=True, sheets=True, cols=10, thumb=200, per_page=120):
    """Render every patch in the library to a PNG (mirrors the library tree) and/or
    per-terrain contact sheets. Returns (n_rendered, sheet_paths)."""
    import csv
    lib_dir = lib_dir or os.path.join(ROOT, "out", "patches")
    out_dir = out_dir or lib_dir          # renders live next to index.csv, no new folder
    idx = os.path.join(lib_dir, "index.csv")
    if not os.path.exists(idx):
        sys.exit(f"no patch library at {idx} — run `patches` first")
    name_set = set(names) if names else None
    terr_set = set(terrains) if terrains else None
    rows = []
    with open(idx, newline="") as f:
        for r in csv.DictReader(f):
            if name_set and r["map"] not in name_set:
                continue
            if terr_set and r["terrain"] not in terr_set:
                continue
            rows.append(r)
    rows.sort(key=lambda r: (r["terrain"], r["map"], int(r["level"]), int(r["zone_id"])))

    by_terrain = collections.defaultdict(list)
    n = 0
    for r in rows:
        patch = json.load(open(os.path.join(lib_dir, r["path"])))
        cap = f"{r['map']} z{r['zone_id']}L{r['level']} ({r['n_objects']}o)"
        img = render_patch_panel(patch, title=None)
        if individual:
            png = os.path.join(out_dir, os.path.splitext(r["path"])[0] + ".png")
            os.makedirs(os.path.dirname(png), exist_ok=True)
            _flatten(img).save(png)
        if sheets:
            by_terrain[r["terrain"]].append((img, cap))
        n += 1
        if n % 200 == 0:
            print(f"  ...{n}/{len(rows)} patches rendered")

    sheet_paths = []
    if sheets:
        for terr, cells in sorted(by_terrain.items()):
            pages = -(-len(cells) // per_page)
            for p in range(pages):
                chunk = cells[p * per_page:(p + 1) * per_page]
                sfx = f"_{p + 1:02d}" if pages > 1 else ""
                sp = os.path.join(out_dir, f"_sheet_{terr}{sfx}.png")
                _grid_montage(chunk, sp, cols=cols, thumb=thumb)
                sheet_paths.append(sp)
    return n, sheet_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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


def cmd_patches(args):
    names = args.name if args.name else OR.all_map_names()
    out_dir = args.out or os.path.join(ROOT, "out", "patches")
    rows, paths = write_patches(names, out_dir)
    idx = write_manifest(rows, out_dir)
    terrains = sorted({r["terrain"] for r in rows})
    print(f"wrote {len(paths)} patches from {len(names)} maps across "
          f"{len(terrains)} terrains ({', '.join(terrains)}) -> {out_dir}/")
    print(f"manifest -> {idx}")
    print("(each patch keeps every object overlapping OR bordering its zone; "
          "rim objects are shared across adjacent patches)")


def cmd_render_patches(args):
    n, sheets = write_patch_renders(
        out_dir=args.out, lib_dir=args.patches, names=args.name or None,
        terrains=args.terrain or None, individual=not args.no_individual,
        sheets=not args.no_sheets, cols=args.cols, thumb=args.thumb,
        per_page=args.per_page)
    out_dir = args.out or args.patches or os.path.join(ROOT, "out", "patches")
    print(f"rendered {n} patches -> {out_dir}/"
          + ("" if args.no_individual else " (one PNG next to each patch JSON)"))
    if sheets:
        print(f"{len(sheets)} per-terrain contact sheet(s) -> {out_dir}/_sheet_<terrain>.png")
        for sp in sheets:
            print(f"  {os.path.relpath(sp, out_dir)}")


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
    the map-placement bottom-RIGHT anchor in render_editor/obj_resolve -- do not conflate them.)"""
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
    import render_editor as RE
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
    zones0, label0 = TS.segment(src["terrain"][0], subdivide=False)
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


def build_layout(kind, seed, size):
    """Return (terr, W, H, info, plan). The texture layouts (region/skeleton/jigsaw/markov)
    return plan=None."""
    terr, W, H, info = LAYOUTS[kind](seed, size, None)
    return terr, W, H, info, None


def _generate_one(kind, args, grammar):
    terr, W, H, info, plan = build_layout(kind, args.seed, args.size)
    if getattr(args, "no_water", False):
        terr = drop_water(terr)
        info += " no-water"
    name = f"Gen-{kind}-s{args.seed}"
    fm, report = generate_map(terr, W, H, seed=args.seed, min_area=args.min_zone,
                              name=name, grammar=grammar, plan=plan)
    filled = [r for r in report if r[4]]
    print(f"\n[{kind}] {info}  {W}x{H}: {len(report)} zones >= {args.min_zone}t, "
          f"{len(filled)} filled, {len(fm['objects'])} objects")
    for zid, terrn, area, role, n in report:
        print(f"  zone {zid:>2}  {str(terrn):<8} area={area:<5} objs={n:<4} role={role}")
    stem = os.path.join(ROOT, "out", slug(name))
    FA.save(fm, stem + ".json")
    FA.to_vmap(fm, stem + ".vmap", name=os.path.basename(stem))
    out = os.path.join(ROOT, "out", "render", f"{slug(name)}.png")
    render_fm(fm, out, title=f"{kind}: {info}")
    print(f"  saved {stem}.vmap  +  render -> {out}")
    return out


def cmd_generate(args):
    if args.layout == "pp":                          # marked-point-process pipeline (spec M6)
        import pp_map
        import render_editor as RED
        players = getattr(args, "players", 2)
        wmode = getattr(args, "water_mode", None) or ("none" if args.no_water else "normal")
        cells, surf, objs, info, ptowns = pp_map.build(seed=args.seed, size=args.size,
                                                       players=players, water_mode=wmode)
        print(info)
        png = os.path.join(ROOT, "out", "render", "pp", f"ppmap_s{args.seed}.png")
        os.makedirs(os.path.dirname(png), exist_ok=True)
        RED.render_map(surf, objs, title="").save(png)
        vmap = pp_map.export_vmap(cells, objs,
                                  os.path.join(ROOT, "out", "vmap", f"ppmap_s{args.seed}.vmap"),
                                  name=f"pp-map s{args.seed}")
        if ptowns:
            try:
                teams = pp_map.parse_teams(getattr(args, "teams", "ffa"), len(ptowns))
            except ValueError as e:
                print(f"  WARNING: {e} — falling back to ffa")
                teams = list(range(len(ptowns)))
            pp_map.apply_playability(vmap, ptowns, teams)
            print(f"  playable: {len(ptowns)} players, teams={teams}, victory=defeat-all")
        print(f"  {png}\n  {vmap}")
        return
    kinds = ["region", "skeleton", "jigsaw"] if args.layout == "all" else [args.layout]
    print(f"=== generate: seed={args.seed} size={args.size} "
          f"min-zone={args.min_zone} layouts={kinds} ===")
    grammar = _load_or_build_grammar(rebuild=args.rebuild_grammar)
    outs = [_generate_one(k, args, grammar) for k in kinds]
    print("\nrenders to compare:")
    for o in outs:
        print(f"  {o}")


def cmd_grammar(args):
    g = learn_grammar(args.patches)
    out = args.out or GRAMMAR_PATH
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(g, open(out, "w"))
    terrains = args.terrain or [t for t in sorted(g) if not t.startswith("_")]
    print(f"=== learned feature grammar from the patch library -> {out} ===")
    for terr in terrains:
        roles = g.get(terr)
        if not roles or terr.startswith("_"):
            continue
        tot = sum(r["weight"] for r in roles.values())
        parts = "  ".join(f"{role}:{roles[role]['weight']}"
                          for role in sorted(roles, key=lambda r: -roles[r]["weight"]))
        print(f"  {terr:<8} ({tot:>4} zones)  {parts}")
    rel = g.get("_relational", {})
    if rel:
        print("  relational sets available (type: #subtypes): "
              + ", ".join(f"{t}:{len(s)}" for t, s in sorted(rel.items())))


def main():
    ap = argparse.ArgumentParser(description="Shape-driven zone-rebuilding engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="map -> template JSON")
    pe.add_argument("name")
    pe.add_argument("--out", default=None)
    pe.set_defaults(func=cmd_extract)

    pp = sub.add_parser("patches", help="every land zone -> a browsable patch library + index.csv")
    pp.add_argument("name", nargs="*", help="map name(s); omit for the whole corpus")
    pp.add_argument("--out", default=None, help="output dir (default out/patches)")
    pp.set_defaults(func=cmd_patches)

    prp = sub.add_parser("render-patches",
                         help="render every library patch in isolation (PNG per patch + "
                              "per-terrain contact sheets) for eyeballing patch content")
    prp.add_argument("name", nargs="*", help="map name(s) to limit to; omit for all")
    prp.add_argument("--terrain", action="append", default=[],
                     help="limit to terrain name(s); repeatable")
    prp.add_argument("--patches", default=None, help="library dir (default out/patches)")
    prp.add_argument("--out", default=None, help="render dir (default out/patches_render)")
    prp.add_argument("--no-individual", action="store_true", help="skip per-patch PNGs")
    prp.add_argument("--no-sheets", action="store_true", help="skip contact sheets")
    prp.add_argument("--cols", type=int, default=10, help="contact-sheet columns")
    prp.add_argument("--thumb", type=int, default=200, help="contact-sheet thumb px")
    prp.add_argument("--per-page", type=int, default=120, dest="per_page",
                     help="patches per contact-sheet page")
    prp.set_defaults(func=cmd_render_patches)

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

    pgr = sub.add_parser("grammar",
                         help="learn the feature grammar (relational setpieces + openness "
                              "fields) from the patch library -> out/grammar.json")
    pgr.add_argument("--terrain", action="append", default=[],
                     help="limit the printed summary to terrain name(s); repeatable")
    pgr.add_argument("--patches", default=None, help="patch library dir (default out/patches)")
    pgr.add_argument("--out", default=None, help="grammar JSON path (default out/grammar.json)")
    pgr.set_defaults(func=cmd_grammar)

    pg = sub.add_parser("generate", help="full map synthesized from the learned feature grammar")
    pg.add_argument("--seed", type=int, default=0)
    pg.add_argument("--size", type=int, default=72, help="W=H of the generated map")
    pg.add_argument("--layout",
                    choices=["region", "skeleton", "jigsaw", "markov", "pp", "all"],
                    default="region", help="terrain layout generator; pp = marked-point-process "
                    "pipeline (macro zones + L3 gameplay + L2 vegetation + L4 pickups, "
                    "G2-repaired, .vmap export); all = the 3 texture alternatives")
    pg.add_argument("--min-zone", type=int, default=12, dest="min_zone",
                    help="leave zones smaller than this as bare terrain (default 12)")
    pg.add_argument("--rebuild-grammar", action="store_true", dest="rebuild_grammar",
                    help="relearn out/grammar.json from the patch library before generating")
    pg.add_argument("--no-water", action="store_true", dest="no_water",
                    help="reassign water tiles to the nearest land terrain (land-only map)")
    pg.add_argument("--players", type=int, default=2,
                    help="[pp layout] number of players; the N largest zones get start towns")
    pg.add_argument("--teams", default="ffa",
                    help="[pp layout] team matrix: 'ffa', '2v2'-style, or explicit '0,0,1,1'")
    pg.add_argument("--water-mode", choices=["none", "normal", "islands"], default=None,
                    dest="water_mode", help="[pp layout] water style")
    pg.set_defaults(func=cmd_generate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
