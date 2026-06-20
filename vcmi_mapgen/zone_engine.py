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


_DECOR_TERR = None
_NEUTRAL_MIN = 6   # an identity on >= this many terrains is terrain-neutral (rocks, generic)


def _decor_terrains():
    """animation -> frozenset(terrain ids) it appears under in the corpus catalog (objlib). Lets a
    zone keep its decoration terrain-consistent — no snow trees on grass."""
    global _DECOR_TERR
    if _DECOR_TERR is None:
        m = collections.defaultdict(set)
        for _purpose, byterr in OR._OBJLIB.items():
            for tid, items in byterr.items():
                for it in items:
                    if it.get("animation"):
                        m[it["animation"]].add(int(tid))
        _DECOR_TERR = {a: frozenset(s) for a, s in m.items()}
    return _DECOR_TERR


def _filter_terrain(pool, terr_id):
    """Keep only decoration identities appropriate for ``terr_id`` (appear on it in the corpus, or
    are terrain-neutral). Unknown identities are kept; if filtering empties the pool, keep the
    original (never break placement). ``pool`` is a list of {identity, weight} entries."""
    if not pool:
        return pool
    dt = _decor_terrains()

    def ok(e):
        ts = dt.get((e.get("identity") or {}).get("animation"))
        return (not ts) or (terr_id in ts) or (len(ts) >= _NEUTRAL_MIN)
    out = [e for e in pool if ok(e)]
    return out or pool


_BORDER_IDENTS = None


def _border_idents(terr_id):
    """1x1 blocking DECORATION identities for a terrain — the material for a CONTINUOUS border
    belt: a single-tile footprint sits exactly on its rim tile, so the ring is unbroken and never
    spills onto a neighbour's terrain. Falls back to the smallest blocking decoration, then grass."""
    global _BORDER_IDENTS
    if _BORDER_IDENTS is None:
        _BORDER_IDENTS = {}
        for tid, items in OR._OBJLIB.get("DECORATION", {}).items():
            ones, smalls = [], []
            for it in items:
                mask = it.get("mask") or []
                if not any("B" in r for r in mask):
                    continue
                ent = {"identity": {k: it[k] for k in ("type", "subtype", "animation", "mask")},
                       "weight": 1}
                if mask == ["B"]:
                    ones.append(ent)
                if sum(len(r) for r in mask) <= 2:
                    smalls.append(ent)
            _BORDER_IDENTS[int(tid)] = ones or smalls
    return _BORDER_IDENTS.get(terr_id) or _BORDER_IDENTS.get(2) or []


_VEG_IDENTS = None
# water features are blocking in the catalog but do NOT read as an obstacle — never use them as a
# zone-border ridge (the belt must be real obstacles: mountains, trees, hills, rocks).
_NON_OBSTACLE_TYPES = {"lake", "lakeDUPLICATE", "frozenLake", "riverDelta", "kelp", "reef"}


def _veg_idents(terr_id):
    """Blocking DECORATION that reads as a real OBSTACLE (mountains AND trees, hills, rocks; any
    footprint size) for a terrain — the material for a natural border ridge that may overlap. Water
    features are excluded. Falls back to grass."""
    global _VEG_IDENTS
    if _VEG_IDENTS is None:
        _VEG_IDENTS = {}
        for tid, items in OR._OBJLIB.get("DECORATION", {}).items():
            pool = [{"identity": {k: it[k] for k in ("type", "subtype", "animation", "mask")},
                     "weight": 1}
                    for it in items if any("B" in r for r in (it.get("mask") or []))
                    and it.get("type") not in _NON_OBSTACLE_TYPES]
            _VEG_IDENTS[int(tid)] = pool
    return _VEG_IDENTS.get(terr_id) or _VEG_IDENTS.get(2) or []


_INTERIOR_IDENTS = None


def _interior_idents(terr_id):
    """Terrain-matched DECORATION for INTERIOR scatter — flora and SMALL obstacles (footprint <= 4
    cells) that texture a zone's interior without sealing it. Water features excluded; non-blocking
    overlay flora is kept so interiors read as lived-in, not empty. Falls back to grass."""
    global _INTERIOR_IDENTS
    if _INTERIOR_IDENTS is None:
        _INTERIOR_IDENTS = {}
        for tid, items in OR._OBJLIB.get("DECORATION", {}).items():
            pool = []
            for it in items:
                if it.get("type") in _NON_OBSTACLE_TYPES:
                    continue
                mask = it.get("mask") or []
                if sum(len(r) for r in mask) > 4:      # keep interior pieces small & walkable
                    continue
                pool.append({"identity": {k: it[k] for k in ("type", "subtype", "animation", "mask")},
                             "weight": 1})
            _INTERIOR_IDENTS[int(tid)] = pool
    return _INTERIOR_IDENTS.get(terr_id) or _INTERIOR_IDENTS.get(2) or []


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
                       hard_block, used, rng, level=0):
    """Shared decoration placer: for each spatial key (a depth bin, or a
    depth x sweep cell) put n = dens * |target tiles in cell| objects, weighted
    uniformly within the cell and min-spaced by the cell's own grain. Never buries
    gameplay (skips stamps whose blocking footprint hits hard_block). Rim first
    (lowest depth bin) so the wall is laid before interior fill competes for tiles."""
    placed = []
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
            ident = _pick_identity(pool, rng)
            cells = [(cx, cy) for cx, cy, blk in OR.mask_cells(ident["mask"], x, y) if blk]
            if any(c in hard_block for c in cells):
                continue              # VCMI: decoration must not bury gameplay
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


def tile_terrain(id_grid, W, H):
    """Terrain-id grid -> faithful cell grid with corpus-correct transition views."""
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
    return [[_cell(g[y][x], x, y) for x in range(W)] for y in range(H)]


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
    return grid, W, H, "jigsaw"


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
NON_GAMEPLAY = ("DECORATION", "TERRAIN_MODIFIER")
# Purposes NEVER scattered independently: TOWN defines a town zone (anchor only); HERO is
# map-relational; TRANSPORT/QUEST_GATE/WATER_TRANSPORT objects are only meaningful in matched
# SETS (portal entrance+exit, gate+key) or with context (boats need water) — placed by
# _place_relational at the map level, or dropped, so we never emit a lone half of a pair.
SATELLITE_SKIP = {"TOWN", "HERO", "TRANSPORT", "QUEST_GATE", "WATER_TRANSPORT"}
# Relational families captured into the grammar catalog, paired by subtype (= color/portal id).
RELATIONAL_TYPES = {"monolithOneWayEntrance", "monolithOneWayExit", "monolithTwoWay",
                    "borderGate", "borderGuard", "keymasterTent", "seerHut",
                    "subterraneanGate", "whirlpool"}
VEG_SCALE = 0.7  # fraction of the corpus decoration budget to lay (rest stays clearing)
INTERIOR_SCALE = 0.18    # graph path: fraction of interior tiles eligible for scatter decoration
INTERIOR_THRESH = 0.45   # graph path: only the clump cores (noise above this) get interior decor


_GUARDABLE_PRIO = {"BANK": 4, "MINE": 3, "REWARD_PICKUP": 2, "RESOURCE_PILE": 1, "DWELLING": 0}


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
    opens = collections.defaultdict(
        lambda: [[[0.0, 0] for _ in range(Sx)] for _ in range(K)])
    rel_catalog = collections.defaultdict(dict)  # type -> subtype -> identity

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

        for o in objs:
            if o["_purpose"] == "DECORATION":
                d = _obj_canon(o, canon, ts)[0]
                addid("wall" if _depth_bin(d) <= 1 else "veg", o)
        thick = _wall_thickness([o for o in objs if o["_purpose"] == "DECORATION"], canon, ts)

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
    out = {t: dict(r) for t, r in grammar.items()}
    out["_relational"] = {t: dict(s) for t, s in rel_catalog.items()}
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


def _fits(ident, x, y, ts, hard):
    for cx, cy, blk in OR.mask_cells(ident["mask"], x, y):
        if blk and ((cx, cy) not in ts or (cx, cy) in hard):
            return False
    return True


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _place_anchor(A, ts, canon, hard, rng, level):
    tiles = list(ts)
    mu, sd = A["depth_mu"], A["depth_sd"]
    weights = [_gauss(canon[t][0], mu, sd) for t in tiles]
    for t in _weighted_spaced(tiles, weights, min(16, len(tiles)), 1.0, rng):
        ident = _pick_identity(A["idents"], rng)
        if _fits(ident, t[0], t[1], ts, hard):
            o = {**ident, "x": t[0], "y": t[1], "l": level,
                 "_purpose": A.get("purpose") or "TOWN"}
            _add_hard(hard, o, ts)
            return o, t
    return None, None


def _place_by_depth(p, pool, n, ts, canon, hard, rng, level, mu, sd):
    """Place n objects of purpose p where this purpose sits in the shape (depth
    signature), spread with a min separation that lets n fit the area (the corpus
    spread, not a tight anchor radius — that isn't what the data shows)."""
    tiles = list(ts)
    weights = [_gauss(canon[t][0], mu, sd) for t in tiles]
    min_sep = max(1.5, 0.6 * math.sqrt(len(ts) / max(n, 1)))
    out = []
    for t in _weighted_spaced(tiles, weights, n * 2, min_sep, rng):
        if len(out) >= n:
            break
        ident = _pick_identity(pool, rng)
        if _fits(ident, t[0], t[1], ts, hard):
            o = {**ident, "x": t[0], "y": t[1], "l": level, "_purpose": p}
            _add_hard(hard, o, ts)
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
                    veg="grammar", gan_ctx=None, patch_pool=None, strict_terrain=False,
                    terr_grid=None):
    """Construct one zone from a (terrain,role) grammar: relational setpiece + carved
    empty skeleton + rim wall with a few gaps + openness-budgeted vegetation.

    ``veg="gan"`` with a loaded ``gan_ctx=(G, D, layers)`` replaces the rule-based vegetation fill
    (step 7) with the learned GAN field decoded by the genetic algorithm; ``veg="patch"`` with a
    loaded ``patch_pool`` instead quilts real corpus patch fragments via a genetic algorithm
    (:mod:`veg_patch`); the default ``"grammar"`` keeps the original rule-based fill unchanged."""
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

    # 2. anchor (deep, open, central)
    anchor_obj = anchor_tile = None
    A = rg.get("anchor")
    if A and A.get("idents"):
        anchor_obj, anchor_tile = _place_anchor(A, ts, canon, gameplay_hard, rng, level)
        if anchor_obj:
            placed.append(anchor_obj)
    base = anchor_tile or _centroid_tile(list(ts))

    # 3. satellites: a per-zone gameplay BUDGET (keeps zones breathing regardless of
    # corpus outliers) split across purposes by their learned density; each purpose is
    # positioned by its own depth signature (where it sits in the shape).
    area = z["area"]
    sats = rg.get("satellites", {})
    gp_budget = max(3, area // 12)
    tot_d = sum(sp.get("density", 0.0) for sp in sats.values()) or 1.0
    guardables = []
    if anchor_obj and (A.get("purpose") in GUARDABLE):
        guardables.append(anchor_obj)
    for p, sp in sorted(sats.items(), key=lambda kv: _prio(kv[0])):
        if p in SATELLITE_SKIP:
            continue
        pool = sp.get("idents")
        n = _stochastic_round(gp_budget * sp.get("density", 0.0) / tot_d, rng)
        if not pool or n <= 0:
            continue
        objs = _place_by_depth(p, pool, n, ts, canon, gameplay_hard, rng, level,
                               sp.get("depth_mu", 0.5), sp.get("depth_sd", 0.2))
        placed += objs
        if p in GUARDABLE:
            guardables += objs

    # 4. carve the open SKELETON (designed empty space): entrance->base lanes + aprons
    for m in mouths:
        for t in _line_tiles(m, base):
            if t in ts:
                reserved.add(t)
                reserved.update(n for n in _ring1(t) if n in ts)
    for o in placed:
        reserved.update(_apron(o, ts, 1))
    reserved -= gameplay_hard

    # 5. guards: protect guardables (the MEANING) — a guard on each one's approach toward
    # the nearest entrance, most-valuable first; leftovers plug the entrance mouths.
    G = rg.get("guard")
    if G and G.get("idents"):
        n_g = _stochastic_round(G.get("density", 0.0) * area, rng)
        n_g = min(n_g, len(guardables) + len(mouths)) if (guardables or mouths) else 0

        def _put_guard(tile):
            gid = _pick_identity(G["idents"], rng)
            placed.append({**gid, "x": tile[0], "y": tile[1], "l": level, "_purpose": "GUARD"})
            gameplay_hard.add(tile)

        for obj in sorted(guardables, key=lambda o: -_GUARDABLE_PRIO.get(o["_purpose"], 0)):
            if n_g <= 0:
                break
            gtile = _approach_toward(obj, mouths, ts, gameplay_hard, base)
            if gtile and gtile not in gameplay_hard:
                _put_guard(gtile)
                n_g -= 1
        for m in mouths:                       # chokepoint guards at the gaps
            if n_g <= 0:
                break
            if m in ts and m not in gameplay_hard:
                _put_guard(m)
                n_g -= 1

    if not strict_terrain:
        # 6. wall: rim band of mountains/trees, minus entrance gaps and reserved lanes
        wall_pool = (rg.get("wall") or {}).get("idents")
        thick = int((rg.get("wall") or {}).get("thickness", 1))
        ring = _rim_band(ts, boundary, thick)
        wall_tiles = sorted(t for t in ring
                            if t not in entrance_tiles and t not in reserved
                            and t not in gameplay_hard)
        if wall_pool and wall_tiles:
            wobjs = _place_decor_cells({0: wall_tiles}, {0: 0.9}, {0: 1.0},
                                       lambda k: wall_pool, ts, gameplay_hard | reserved,
                                       set(gameplay_hard) | reserved | entrance_tiles, rng, level)
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
            interior.sort(key=lambda t: noise[t[1] - miny][t[0] - minx], reverse=True)
            blockset = gameplay_hard | reserved
            for t in interior[:max(1, int(INTERIOR_SCALE * len(interior)))]:
                if noise[t[1] - miny][t[0] - minx] < INTERIOR_THRESH:
                    break                          # below the clump threshold = clearing
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

    # 7. vegetation: CLUMPED with clearings (overlapping clumps where a smooth noise
    # field is high, open ground where it is low) so interiors breathe; the per-cell
    # count is the learned openness budget, concentrated into the clumps not spread flat.
    if veg == "gan" and gan_ctx is not None:
        # learned vegetation: GAN field decoded by the GA (footprint-valid, never buries
        # the gameplay placed above). Replaces the rule-based clump fill below. The
        # condition is cropped from the full-map ``layers`` so the generator sees the
        # neighbour terrain + map edge; ``border_tiles`` (the inter-zone passages) are
        # protected so a mountain wall on the seam keeps its one-tile gap.
        import veg_gan as VG
        G, D, layers = gan_ctx
        placed += VG.decorate_zone(z, layers, border_tiles, gameplay_hard, level, G, D, seed)
        veg_pool = openness = None
    elif veg == "patch" and patch_pool is not None:
        # patch-quilt vegetation: real corpus patch fragments assembled by a GA to match the
        # corpus distribution (footprint-valid, never buries gameplay; the inter-zone passages
        # in ``border_tiles`` stay open). Replaces the rule-based clump fill below.
        import veg_patch as VP
        placed += VP.decorate_zone(z, canon, patch_pool, border_tiles, gameplay_hard,
                                   seed=seed, level=level)
        veg_pool = openness = None
    else:
        veg_pool = (rg.get("veg") or {}).get("idents")
        if strict_terrain:
            veg_pool = None        # graph path: vegetation belongs on the BORDER belt only — no
            #                        interior scatter (that was the clutter); the rim defines zones.
        openness = rg.get("openness")
    if veg_pool and openness:
        (minx, miny, maxx, maxy), _ = zone_bbox_mask(z["tiles"])
        NW, NH = maxx - minx + 1, maxy - miny + 1
        noise = _value_noise(NW, NH, max(2, min(NW, NH) // 5), rng)

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
            n = _stochastic_round(VEG_SCALE * max(0.0, 1.0 - op) * len(tl), rng)
            if n <= 0:
                continue
            free = sorted((t for t in tl if t not in used), key=clump, reverse=True)
            for t in free[:n]:                 # fill the clump cores first (overlapping)
                ident = _pick_identity(veg_pool, rng)
                if any((cx, cy) in blockset for cx, cy, blk in
                       OR.mask_cells(ident["mask"], t[0], t[1]) if blk):
                    continue                   # never bury gameplay or fill a reserved lane
                placed.append({**ident, "x": t[0], "y": t[1], "l": level,
                               "_purpose": "DECORATION"})
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


def generate_map(terrain, W, H, seed=0, min_area=12, name=None, grammar=None,
                 veg="grammar", weights=None, plan=None):
    """Synthesize a full map: assign each land zone a role, then construct it from the
    learned feature grammar. Zones smaller than min_area stay bare terrain.

    ``veg="gan"`` decorates each zone with the learned GAN+GA vegetation instead of the
    rule-based fill; if the weights are missing it warns and falls back to ``"grammar"``.
    ``plan`` (from the graph generator) carries ``role_seeds`` (per-zone role override by
    seed tile) and ``edge_seeds`` (which zone borders get a passage; the rest stay walled)."""
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

    gan_ctx = None
    patch_pool = None
    if veg == "gan":
        import veg_gan as VG
        import veg_data as VD
        wpath = weights or VG.DEFAULT_WEIGHTS
        if os.path.exists(wpath):
            G, D, _cfg = VG.load_models(wpath)
            # full-map condition layers once → cross-zone terrain + map-edge context.
            layers = VD.compute_cond_layers(terrain, canon, zones, zone_label)
            gan_ctx = (G, D, layers)
        else:
            print(f"  --veg gan: weights {wpath} not found; falling back to grammar fill")
            veg = "grammar"
    elif veg == "patch":
        patch_pool = load_patch_pool()      # GA quilts real corpus patches (no neural net)

    strict_terrain = bool(plan)   # graph-planned maps keep each zone's decoration terrain-pure
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
        placed = synthesize_zone(z, canon[zid], rg, borders,
                                 seed ^ (zid * 2654435761 & 0xFFFFFFFF), 0, global_hard,
                                 veg=veg, gan_ctx=gan_ctx, patch_pool=patch_pool,
                                 strict_terrain=strict_terrain, terr_grid=terrain)
        objects += placed
        report.append((zid, terr, z["area"], role, len(placed)))
    objects += _place_relational(zones, sorted(roles), objects, grammar, rng, 0, False)
    import traverse as TR
    n_towns = sum(1 for o in objects if TR.TYPE2PURPOSE.get(o.get("type")) == "TOWN")
    fm = {"name": name or f"Generated-s{seed}", "width": W, "height": H,
          "twoLevel": False, "players": max(1, n_towns), "terrain": [terrain],
          "objects": objects}
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
    """Return (terr, W, H, info, plan). The graph generator plans roles + edges; the texture
    layouts (region/skeleton/jigsaw/markov) return plan=None."""
    if kind == "graph":
        import mapgraph as MG
        return MG.realize(seed, size)
    terr, W, H, info = LAYOUTS[kind](seed, size, None)
    return terr, W, H, info, None


def _generate_one(kind, args, grammar):
    terr, W, H, info, plan = build_layout(kind, args.seed, args.size)
    if getattr(args, "no_water", False):
        terr = drop_water(terr)
        info += " no-water"
    name = f"Gen-{kind}-s{args.seed}"
    fm, report = generate_map(terr, W, H, seed=args.seed, min_area=args.min_zone,
                              name=name, grammar=grammar,
                              veg=getattr(args, "veg", "grammar"),
                              weights=getattr(args, "weights", None),
                              plan=plan)
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
    kinds = ["region", "skeleton", "jigsaw"] if args.layout == "all" else [args.layout]
    print(f"=== generate: seed={args.seed} size={args.size} "
          f"min-zone={args.min_zone} layouts={kinds} ===")
    grammar = _load_or_build_grammar(rebuild=args.rebuild_grammar)
    outs = [_generate_one(k, args, grammar) for k in kinds]
    print("\nrenders to compare:")
    for o in outs:
        print(f"  {o}")


def cmd_veg_train(args):
    import veg_gan as VG
    out = args.out or VG.DEFAULT_WEIGHTS
    print(f"=== veg-train: seed={args.seed} epochs={args.epochs} windows={args.windows} "
          f"width={args.width} ga_every={args.ga_every} resume={args.resume} -> {out} ===")
    VG.train(seed=args.seed, epochs=args.epochs, n_windows=args.windows, width=args.width,
             ga_every=args.ga_every, out=out, resume=args.resume)


def _veg_sample_patch(args):
    """VEGETATION ONLY (patch-quilt GA): generate terrain, place just the GA-quilted real-patch
    vegetation per zone (no gameplay, no rule-based wall), and render. Isolates the patch
    vegetation for evaluation. No weights/torch — uses the corpus patch library."""
    import veg_patch as VP
    pool = load_patch_pool()
    terr, W, H, info = LAYOUTS[args.layout](args.seed, args.size, None)
    if getattr(args, "no_water", False):
        terr = drop_water(terr)
        info += " no-water"
    zones, zone_label, canon = _segment_level(terr)
    passages = _zone_passages(zones, zone_label, W, H)
    objects = []
    for zid in sorted(zones):
        z = zones[zid]
        if z["area"] < args.min_zone or not (0 <= z["terrain_type"] < 8):
            continue
        objects += VP.decorate_zone(z, canon[zid], pool, passages.get(zid), set(),
                                    seed=args.seed ^ (zid * 2654435761 & 0xFFFFFFFF))
    fm = {"name": f"VegOnly-patch-{args.layout}-s{args.seed}", "width": W, "height": H,
          "twoLevel": False, "players": 0, "terrain": [terr], "objects": objects}
    land = sum(1 for zid in zones for _ in zones[zid]["tiles"]
               if 0 <= zones[zid]["terrain_type"] < 8)
    print(f"[veg-sample VEGETATION-ONLY patch] {info} {W}x{H}: {len(objects)} vegetation objects "
          f"over {land} land tiles (density {len(objects)/max(land,1):.2f})")
    out = os.path.join(ROOT, "out", "render", f"vegonly_patch_{args.layout}_s{args.seed}.png")
    render_fm(fm, out, title=f"patch-quilt vegetation only: {info}")
    print(f"  render -> {out}")


def cmd_veg_sample(args):
    """VEGETATION ONLY: generate terrain, place just the learned GAN+GA vegetation per zone
    (no gameplay objects, no rule-based rim wall), and render so vegetation vs free tiles is
    clearly visible. This isolates the GAN's vegetation for evaluation."""
    if getattr(args, "veg", "gan") == "patch":
        return _veg_sample_patch(args)
    import veg_gan as VG
    wpath = args.weights or VG.DEFAULT_WEIGHTS
    if not os.path.exists(wpath):
        print(f"weights {wpath} not found — run veg-train first")
        return
    import veg_data as VD
    terr, W, H, info = LAYOUTS[args.layout](args.seed, args.size, None)
    if getattr(args, "no_water", False):
        terr = drop_water(terr)
        info += " no-water"
    G, D, _cfg = VG.load_models(wpath)
    zones, zone_label, canon = _segment_level(terr)
    # full-map condition layers + inter-zone passages, computed once (cross-zone context).
    layers = VD.compute_cond_layers(terr, canon, zones, zone_label)
    passages = _zone_passages(zones, zone_label, W, H)
    objects = []
    for zid in sorted(zones):
        z = zones[zid]
        if z["area"] < args.min_zone or not (0 <= z["terrain_type"] < 8):
            continue
        objects += VG.decorate_zone(z, layers, passages.get(zid), set(), 0, G, D,
                                    seed=args.seed ^ (zid * 2654435761 & 0xFFFFFFFF))
    fm = {"name": f"VegOnly-{args.layout}-s{args.seed}", "width": W, "height": H,
          "twoLevel": False, "players": 0, "terrain": [terr], "objects": objects}
    land = sum(1 for zid in zones for _ in zones[zid]["tiles"]
               if 0 <= zones[zid]["terrain_type"] < 8)
    print(f"[veg-sample VEGETATION-ONLY] {info} {W}x{H}: {len(objects)} vegetation objects "
          f"over {land} land tiles (density {len(objects)/max(land,1):.2f})")
    out = os.path.join(ROOT, "out", "render", f"vegonly_{args.layout}_s{args.seed}.png")
    render_fm(fm, out, title=f"GAN vegetation only: {info}")
    print(f"  render -> {out}")


def _parse_seeds(spec):
    """'0-4' or '0,1,2' or '0-2,5' -> [0,1,2,3,4] / [0,1,2] / [0,1,2,5]."""
    out = []
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def gen_fm(layout, veg, seed, size, min_zone, grammar, weights=None):
    """Generate ONE full map (no render/save) for the leaderboard. Returns the faithful dict."""
    terr, W, H, _info, plan = build_layout(layout, seed, size)
    fm, _report = generate_map(terr, W, H, seed=seed, min_area=min_zone,
                               name=f"Gen-{layout}-{veg}-s{seed}", grammar=grammar,
                               veg=veg, weights=weights, plan=plan)
    return fm


def _runnable_veg(veg, weights):
    """Filter veg modes whose assets are missing (gan weights / patch library)."""
    if veg == "gan":
        import veg_gan as VG
        return os.path.exists(weights or VG.DEFAULT_WEIGHTS)
    if veg == "patch":
        return os.path.exists(os.path.join(ROOT, "out", "patches", "index.csv"))
    return True


def cmd_compare(args):
    """Race generators on the map-level yardstick: for each (layout × veg) over the seed set,
    generate a full map and score it with mapeval, then print a leaderboard (mean ± std)."""
    import mapeval as ME
    seeds = _parse_seeds(args.seeds)
    grammar = _load_or_build_grammar()
    specs = [(lay, veg) for lay in args.layouts for veg in args.veg
             if _runnable_veg(veg, args.weights)]
    print(f"=== compare: seeds={seeds} size={args.size} "
          f"specs={[f'{l}:{v}' for l, v in specs]} ===")
    rows = []
    for lay, veg in specs:
        recs = []
        for s in seeds:
            try:
                fm = gen_fm(lay, veg, s, args.size, args.min_zone, grammar, args.weights)
                recs.append(ME.score_map(fm))
            except Exception as e:
                print(f"  {lay}:{veg} s{s} FAILED: {e}")
        if not recs:
            continue

        def col(k):
            v = np.array([r[k] for r in recs], float)
            return v.mean(), v.std()
        rows.append((f"{lay}:{veg}", col("total"), col("reach"), col("dist"),
                     col("balance"), sum(r["ok"] for r in recs), len(recs)))
    rows.sort(key=lambda r: -r[1][0])
    print(f"\n{'generator':<18}{'total':>14}{'reach':>14}{'dist':>14}{'balance':>14}  ok")

    def fmt(m, s):
        return f"{m:.3f}±{s:.3f}"
    for name, tot, reach, dist, bal, nok, n in rows:
        print(f"{name:<18}{fmt(*tot):>14}{fmt(*reach):>14}{fmt(*dist):>14}"
              f"{fmt(*bal):>14}  {nok}/{n}")
    # corpus reference band so the leaderboard is interpretable
    names, mean, std = ME._corpus_stats()
    print(f"\n(corpus reference: {len(ME.OR.all_map_names())} maps; "
          f"a generator's 'dist' nearer 1.0 is closer to the corpus distribution)")


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
                    choices=["region", "skeleton", "jigsaw", "markov", "graph", "all"],
                    default="region", help="terrain layout generator; graph = zone-graph "
                    "planner (connectivity-first); all = the 3 texture alternatives")
    pg.add_argument("--min-zone", type=int, default=12, dest="min_zone",
                    help="leave zones smaller than this as bare terrain (default 12)")
    pg.add_argument("--rebuild-grammar", action="store_true", dest="rebuild_grammar",
                    help="relearn out/grammar.json from the patch library before generating")
    pg.add_argument("--no-water", action="store_true", dest="no_water",
                    help="reassign water tiles to the nearest land terrain (land-only map)")
    pg.add_argument("--veg", choices=["grammar", "gan", "patch"], default="grammar",
                    help="vegetation fill: grammar=rule-based (default), gan=learned GAN+GA, "
                         "patch=GA quilt of real corpus patches (needs the `patches` library)")
    pg.add_argument("--weights", default=None,
                    help="veg GAN weights for --veg gan (default out/veg_gan.pt)")
    pg.set_defaults(func=cmd_generate)

    pcmp = sub.add_parser("compare",
                          help="race generators on the map-level yardstick (mapeval) and print "
                               "a leaderboard of total/reach/dist/balance vs the corpus")
    pcmp.add_argument("--seeds", default="0-4", help="seed set, e.g. 0-4 or 0,1,2 (default 0-4)")
    pcmp.add_argument("--size", type=int, default=72, help="W=H of each generated map")
    pcmp.add_argument("--min-zone", type=int, default=12, dest="min_zone")
    pcmp.add_argument("--layouts", nargs="+",
                      default=["region", "skeleton", "jigsaw", "markov", "graph"],
                      help="layouts to race (graph = zone-graph planner; wfc once added)")
    pcmp.add_argument("--veg", nargs="+", default=["grammar"],
                      help="veg fills to race: grammar (default), gan, patch")
    pcmp.add_argument("--weights", default=None, help="veg GAN weights for --veg gan")
    pcmp.set_defaults(func=cmd_compare)

    pvt = sub.add_parser("veg-train", help="train the vegetation GAN (PyTorch) -> out/veg_gan.pt")
    pvt.add_argument("--seed", type=int, default=0)
    pvt.add_argument("--epochs", type=int, default=5)
    pvt.add_argument("--windows", type=int, default=None, help="cap training windows (default all)")
    pvt.add_argument("--width", type=int, default=64, help="base conv width")
    pvt.add_argument("--ga-every", type=int, default=0, dest="ga_every",
                     help="coevolution: feed GA hard-negatives to D every N steps (0=off)")
    pvt.add_argument("--resume", action="store_true",
                     help="continue from out/veg_gan.pt.ckpt (G/D/optimizers/EMA/step)")
    pvt.add_argument("--out", default=None, help="weights path (default out/veg_gan.pt)")
    pvt.set_defaults(func=cmd_veg_train)

    pvs = sub.add_parser("veg-sample",
                         help="sample learned vegetation over generated terrain and render "
                              "a real-vs-GAN comparison")
    pvs.add_argument("--seed", type=int, default=0)
    pvs.add_argument("--size", type=int, default=72, help="W=H of the generated terrain")
    pvs.add_argument("--layout", choices=["region", "skeleton", "jigsaw", "markov"],
                     default="region")
    pvs.add_argument("--min-zone", type=int, default=12, dest="min_zone")
    pvs.add_argument("--veg", choices=["gan", "patch"], default="gan",
                     help="gan=learned GAN+GA (default), patch=GA quilt of real corpus patches")
    pvs.add_argument("--no-water", action="store_true", dest="no_water",
                     help="reassign water tiles to the nearest land terrain (land-only map)")
    pvs.add_argument("--weights", default=None, help="weights path (default out/veg_gan.pt)")
    pvs.set_defaults(func=cmd_veg_sample)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
