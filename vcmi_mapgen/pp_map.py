"""End-to-end L0+L2+L3+L4 map render: terrain + gameplay + vegetation + pickups.

Pipeline:  macro_topo.generate (capacity-constrained zones, textured borders)
        -> zone_engine.tile_terrain (corpus-learned TRANSITION views: shores, terrain edges)
        -> segment into zones
        -> per land zone:
             pp_gameplay.place_zone  (towns/mines/dwellings/shrines; corpus densities,
                                      anchors SAMPLED from the fitted L3 intensity)
             protected walkable web  (backbone + gates + every gameplay approach tile,
                                      routed AROUND gameplay footprints)
             pp_sample.sample_zone   (corpus-fitted Gibbs marked point process; overlap
                                      legal; gameplay cells + web are hard zeros)
             pp_pickup.place_pickups (resources/artifacts over the OPEN field: unguarded
                                      scatter on routes, guarded caches in pockets with a
                                      monster on the mouth)
        -> render_editor.render_map with REAL 32px H3 sprites.

    uv run python -m vcmi_mapgen.pp_map --seed 3 --size 72
"""
import argparse
import collections
import os
from vcmi_mapgen import faithful as FA
from vcmi_mapgen.steps.terrain_gen import macro_topo as MTOPO
from vcmi_mapgen import obj_resolve as OR
from vcmi_mapgen import ontology as ON
from vcmi_mapgen import pp_gameplay as PG
from vcmi_mapgen import pp_pickup as PK
from vcmi_mapgen.steps.vegetation import sample as PP
from vcmi_mapgen import terrain_segment as TSG
from vcmi_mapgen import vcmi_paths as VPATH
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen import zone_field as ZF
from vcmi_mapgen.vcmi_paths import project_root
from vcmi_mapgen.steps.gameplay.mines import select_player_zones
from vcmi_mapgen.steps.gameplay.water import _ensure_water_seaports
from vcmi_mapgen.steps.repair.border_seal import seal_zone_borders
from vcmi_mapgen.steps.repair.geometry import g2_repair, fill_open_islands, rescue_unreachable_zones
from vcmi_mapgen.steps.repair.step import _repair_one_level

ROOT = project_root()
MIN_AREA = 25          # vegetate even smallish zones (the stats floor stays 60 in pp_stats)


def export_vmap(levels, objs, out_path, name="pp-map"):
    """Write the generated map as an editor-valid .vmap via the proven faithful writer.
    `levels` is a list of 1 (surface only) or 2 (surface + underground, `--subterrain`)
    terrain-cell grids. The first town becomes player 0's start (faithful.to_vmap wires
    mainTown slots)."""
    towns = [o for o in objs if o.get("purpose") == "TOWN"]
    fm = {"name": name, "terrain": levels,
          "objects": [o for o in objs if o.get("type")],
          "main_town": ({"l": towns[0].get("l", 0), "x": towns[0]["x"] - 2,
                        "y": towns[0]["y"] - 2} if towns else None)}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    return FA.to_vmap(fm, out_path, name=name)


def parse_teams(spec, n):
    """Team matrix from a CLI spec: 'ffa' (everyone alone), '2v2'/'1v3'-style groups, or an
    explicit comma list of team ids ('0,0,1,1'). Returns one team id per player."""
    if not spec or spec == "ffa":
        return list(range(n))
    if "v" in spec:
        sizes = [int(s) for s in spec.split("v")]
        if sum(sizes) != n:
            raise ValueError(f"teams {spec!r} sums to {sum(sizes)}, but players={n}")
        return [ti for ti, s in enumerate(sizes) for _ in range(s)]
    out = [int(s) for s in spec.split(",")]
    if len(out) != n:
        raise ValueError(f"teams {spec!r} lists {len(out)} ids, but players={n}")
    return out


def apply_playability(vmap_path, player_towns, teams):
    """Deterministic playability overlay on an exported .vmap:

      1. exactly len(player_towns) playable slots, slot i wired to its designated town
         (any faction allowed — the towns are usually randomTown) — AND the town OBJECT
         itself gets `options.owner = <player>` (the header's mainTown alone does NOT
         assign ownership; without the owner the town stays neutral),
      2. the team matrix (`teams[i]` = team id of player i; VCMI allies equal ids),
      3. victory = DEFEAT ALL (the canonical standardWin triggered event; standardDefeat =
         7 days without town), any special victory conditions stripped.
    """
    import json
    import zipfile
    from collections import defaultdict
    z = zipfile.ZipFile(vmap_path)
    files = {n: z.read(n) for n in z.namelist()}
    z.close()
    h = json.loads(files["header.json"].decode())
    vobjs = json.loads(files["objects.json"].decode())
    pids = sorted(p for p, pl in h["players"].items() if isinstance(pl, dict))
    for i, pid in enumerate(pids):
        pl = h["players"][pid]
        if i < len(player_towns):
            t = player_towns[i]
            pl["mainTown"] = {"generateHero": True, "l": t.get("l", 0),
                              "x": t["x"] - 2, "y": t["y"] - 2}
            pl["canPlay"] = "PlayerOrAI"
            pl["team"] = int(teams[i])
            if t.get("type") == "town":
                # concrete start town (spare-neutral top-up): the lobby must not offer
                # factions the map cannot honour — restrict to the authored one, exactly
                # like VCMI's own RMG maps do
                pl["allowedFactions"] = {"anyOf": [f"core:{t['subtype']}"]}
                pl.pop("randomFaction", None)
            else:
                # randomTown start: any faction; VCMI resolves the OWNED random town to
                # the lobby pick (CGTownInstance::randomizeFaction). PlayerInfo::defaultCastle()
                # only returns RANDOM when isFactionRandom is set — an absent/permissive
                # allowedFactions alone still defaults the lobby dropdown to the first
                # faction (Castle) sorted by id. Field name from MapFormatJson.cpp's
                # serializePlayerInfo: handler.serializeBool("randomFaction", ...).
                pl.pop("allowedFactions", None)
                pl["randomFaction"] = True
            for vo in vobjs:                         # ownership lives on the town object
                if (vo["x"] == t["x"] and vo["y"] == t["y"]
                        and vo.get("l", 0) == t.get("l", 0)
                        and vo.get("type") in ("town", "randomTown")):
                    vo.setdefault("options", {})["owner"] = pid
                    break
        else:
            pl["mainTown"] = None
            pl["canPlay"] = "false"
            pl.pop("team", None)
    # VCMI's lobby/map-select screen reads alliances from this top-level grouping —
    # not from each player's individual "team" int above — so it must be set for
    # the UI to show teams at all. Real VCMI RMG maps omit the key entirely for FFA.
    groups = defaultdict(list)
    for i, pid in enumerate(pids[:len(player_towns)]):
        groups[int(teams[i])].append(pid)
    allied = [members for members in groups.values() if len(members) > 1]
    if allied:
        h["teams"] = allied
    else:
        h.pop("teams", None)
    files["objects.json"] = json.dumps(vobjs, indent=1).encode()
    MSG = {"exactStrings": None, "localStrings": None, "message": [2], "numbers": None}
    h["triggeredEvents"] = {
        "standardVictory": {
            "condition": ["standardWin", {"type": "", "value": -1}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "victory"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.659"])},
        "standardDefeat": {
            "condition": ["daysWithoutTown", {"type": "", "value": 7}],
            "effect": {"messageToSend": {"exactStrings": None, "localStrings": None,
                                         "message": None, "numbers": None,
                                         "stringsTextID": None}, "type": "defeat"},
            "message": dict(MSG, stringsTextID=["core.genrltxt.7"])}}
    h["victoryIconIndex"] = 11                       # "defeat all enemies"
    h["victoryMessage"] = dict(MSG, stringsTextID=["core.vcdesc.0"])
    h["defeatIconIndex"] = 3
    h["defeatMessage"] = dict(MSG, stringsTextID=["core.lcdesc.0"])
    files["header.json"] = json.dumps(h, indent=1).encode()
    with zipfile.ZipFile(vmap_path, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, data in files.items():
            zo.writestr(n, data)
    return vmap_path


def _rim8(zones):
    """The 8-connected inter-zone rim: every tile with an 8-neighbour in another zone
    (both sides of every border). Diagonal contact counts — corner-cutting is a legal
    hero move in H3, so a diagonal-only touch leaks exactly like a shared edge."""
    owner = {}
    for zid, z in zones.items():
        for t in z["tiles_set"]:
            owner[t] = zid
    return {t for t, zid in owner.items()
            if any(owner.get((t[0] + dx, t[1] + dy), zid) != zid for dx, dy in ZF.NB8)}


def _run_level(level, W, H, grid, zones, player_zids, ledger, gstats, seed, has_subterrain,
               gate_occ=frozenset(), gate_blk=frozenset(), gate_appr=(), tunnel_protect=frozenset()):
    """L3 gameplay + L2 vegetation + L4a scatter for ONE terrain level's already-segmented
    `zones`. `player_zids` is the set of this level's zids that must fit a start town.
    Water-body population (sea chests/boats/etc.) only runs for the surface (level 0) —
    underground water/lava pockets stay unpopulated barrier tiles (classic-sparse scope).
    `tunnel_protect` (only non-empty for the underground level) is the corpus tunnel-mask
    plus gate-connector-corridor cell set that terrain generation already guarantees stays
    walkable (`build()`'s despeckle/texturing protect set) — folded into each zone's own
    `prot` web so vegetation can never re-block a corridor that terrain generation just
    fought to keep open (zone-local `protected_web` has no visibility of cells the corridor
    passes through, since it only ever routes within one zone's own tiles).
    `gate_occ`/`gate_blk`/`gate_appr` are this LEVEL's already-finalized Subterranean Gate
    footprint/blocked/approach cells (`build()` calls `place_gates` right after segmentation,
    before any zone's gameplay/vegetation pass runs) — each zone seeds `place_zone` with
    whichever of these cells fall inside it (`preoccupied`/`preblocked`/`preapproaches`), so a
    gate is treated exactly like a pre-placed mine/town: density fills right up to its GAP
    buffer instead of a whole disc being held bare.
    Returns (objs, targets, zone_records, town_of_zone, has_water, nz); every object still
    carries `l=0` (place_zone's/pp_pickup's own default) — `build()` retags the whole
    underground level's objects to `l=1` in one post-processing pass."""
    targets = []                                     # G2: tiles that must stay reachable
    nz = 0
    zone_records = []                                 # for the per-level pocket-cache pass
    seal_avoid = set()                               # tiles the seal pass must leave veg-free
    hard_avoid = set()                               # tiles that can't even host a guard
    models = {}

    # L3 gameplay (per-zone place_zone + protected web), water-body population, and the
    # seaport guarantee — split out into steps/gameplay/step.py so both this legacy path and
    # GameplayStep call the identical logic (pipeline-refactor-v2-folders.md, Phase 2).
    from vcmi_mapgen.steps.gameplay.step import _run_level_gameplay
    (objs, zone_cache, entrance_plan, has_water, town_of_zone, ridge,
     _seaport_blk, _seaport_appr, water_tiles) = _run_level_gameplay(
        level, W, H, grid, zones, player_zids, ledger, gstats, seed, has_subterrain,
        gate_occ=gate_occ, gate_blk=gate_blk, gate_appr=gate_appr,
        tunnel_protect=tunnel_protect,
    )

    # ── Pass 2: L2 vegetation + L4a scatter for all zones ─────────────────────
    for zid, z in sorted(zones.items()):
        if zid not in zone_cache:
            continue
        c = zone_cache[zid]
        terrain  = c["terrain"]
        ts       = c["ts"]
        ts_full  = c["ts_full"]
        gobjs    = c["gobjs"]
        occupied = c["occupied"]
        gblocked = c["gblocked"]
        approaches = c["approaches"]
        z_entr   = c["z_entr"]
        prot     = c["prot"]
        rim8     = c["rim8"]
        ent_bands = c["ent_bands"]

        # L2 vegetation: gameplay cells + approaches admit no vegetation at all; the
        # annulus around MINE footprints ATTRACTS vegetation (sawmills nestle in forest)
        if terrain not in models:
            models[terrain] = PP.build_model(terrain)
        model = models[terrain]
        if not model["cats"]:
            continue
        # Seaport footprint in this zone must be excluded from vegetation
        zone_seaport_cells = (_seaport_blk | _seaport_appr) & ts_full
        forbid = frozenset(occupied) | frozenset(approaches) | zone_seaport_cells
        mine_cells = {(mcx, mcy) for o in gobjs if o.get("purpose") == "MINE"
                      for mcx, mcy, mblk in OR.mask_cells(o["mask"], o["x"], o["y"]) if mblk}
        attract = frozenset(
            t for t in ts if t not in forbid
            and 2 <= min(max(abs(t[0] - mx), abs(t[1] - my)) for mx, my in mine_cells) <= 3
        ) if mine_cells else frozenset()             # annulus 2..3: greenery frames the
        # mine without sprite canopies overhanging its visual
        # zone-isolation border belt: the whole 8-connected rim minus the planned entrance
        # bands (those sit in `prot` as hard zeros) gets the +BORDER_W vegetation bias —
        # both zones densify their own side, so the border reads as a ~2-thick ridge.
        border = frozenset(rim8 - ent_bands - forbid)
        zobjs, blocked, _ = PP.sample_zone(ts, zones, zid, model, seed=seed,
                                           prot=prot, forbid=forbid, attract=attract,
                                           border=border)
        objs.extend(zobjs)

        # L4a scatter over the finished open field (unguarded resources/artifacts along
        # routes); approach tiles stay clear — mines may already carry their guard there.
        # Guarded pocket caches are NOT placed per zone — see the global pass below, which
        # must run once the whole map's zones are done (a pocket's neck is only genuine when
        # judged against TRUE map-wide passability, not one zone's reach alone).
        open_set = ts - blocked - gblocked - set(occupied) - set(approaches) - zone_seaport_cells
        # TRUE physical passability, for pocket GEOMETRY only: unlike `open_set` (placement
        # eligibility — excludes approach tiles and non-blocking occupied cells so new
        # objects can't stack on them), this only drops tiles that are actually impassable.
        # Approach tiles and non-blocking occupied footprint cells ARE walkable in-game.
        passable = ts - blocked - gblocked
        # scatter loot never sits on the rim: a pickup there is a walkable, unsealable hole
        sobjs, sused, reach = PK.place_scatter(ts, zones, zid, terrain,
                                              open_set - (rim8 - ent_bands), prot, seed=seed,
                                              bounds=(W, H), entrances=z_entr)
        objs.extend(sobjs)
        targets.extend(approaches)
        targets.extend((o["x"], o["y"]) for o in sobjs)
        # every planned crossing must survive repair: its rep is a named G2 target, so
        # `g2_repair` verifies the entrance stayed connected once the level is finalized
        targets.extend(r for r, _b, _o in z_entr)
        # Seaport approach tile must stay reachable (hero boards ship from there)
        targets.extend(t for t in (_seaport_appr & ts_full))
        seal_avoid |= prot | forbid | sused | set(approaches)
        hard_avoid |= set(occupied) | sused | set(approaches)
        nz += 1
        zone_records.append({"zid": zid, "terrain": terrain, "ts": ts_full,
                             "open_set": open_set, "passable": passable,
                             "reach": reach, "used": sused})
        pk = collections.Counter(o["purpose"] for o in sobjs)
        print(f"  L{level} zone {zid:>3} {terrain:<8} {z['area']:>5} tiles: {len(gobjs):>2} "
              f"gameplay, {len(zobjs):>4} veg (blocked {len(blocked) / len(ts):.2f}/"
              f"{model['target']:.2f}), scatter res={pk.get('RESOURCE_PILE', 0)} "
              f"art={pk.get('REWARD_PICKUP', 0)}")

    loot_objs, n_loot, _loot_zids = PK.place_loot_zones(
        zone_records, entrance_plan, objs, seed=seed, bounds=(W, H),
        water_tiles=water_tiles)
    objs.extend(loot_objs)
    # Only add EXTERIOR loot zone objects to targets (keymaster, exterior monolith).
    # Interior objects (gate, interior monolith) are reachable via teleportation/gate,
    # not via physical traversal — adding them causes g2_repair to carve a hole in the
    # loot zone seal to make them physically reachable.
    _loot_interior_tiles = set()
    for zr in zone_records:
        if zr["zid"] in _loot_zids:
            _loot_interior_tiles |= zr["ts"]
    targets.extend((o["x"], o["y"]) for o in loot_objs
                   if o.get("purpose") and (o["x"], o["y"]) not in _loot_interior_tiles)
    # Remove any stale scatter targets from loot zone interiors — scatter loot was placed
    # inside those zones before place_loot_zones cleared it; without this, g2_repair sees
    # those now-open positions as unreachable targets and carves a path through the seal.
    targets[:] = [t for t in targets if t not in _loot_interior_tiles]
    # Mark sealed loot zones so place_pocket_caches excludes them from its detection
    # universe — their interiors would otherwise appear as pockets and receive a
    # spurious interior guard (the access mechanic already provides the gate keeper).
    for zr in zone_records:
        if zr["zid"] in _loot_zids:
            zr["loot_zone"] = True
    if n_loot:
        zid_str = ", ".join(str(z) for z in sorted(_loot_zids))
        print(f"  L{level} loot zones: {n_loot} access pair(s) placed "
              f"(1 gate+key, {n_loot-1} sealed+monolith) zones=[{zid_str}]" if n_loot > 1
              else f"  L{level} loot zones: 1 gate+key pair placed zones=[{zid_str}]")

    # Residual border-leak seal: close every cross-zone crossing the statistics left open.
    # Runs after place_loot_zones so loot-zone boundaries are already veg-sealed.
    # skip_tiles = all loot-zone tile sets: pairs touching a loot-zone tile are dropped
    # entirely so no guard lands on the gate/monolith approach or on the exterior side.
    _loot_ts = set()
    for zr in zone_records:
        if zr.get("loot_zone"):
            _loot_ts |= zr["ts"]
    sobjs_seal, sealed, guard_tiles, n_open = seal_zone_borders(
        W, H, grid, zones, entrance_plan, objs, seal_avoid | set(tunnel_protect),
        hard_avoid, seed, level, skip_tiles=_loot_ts)
    objs.extend(sobjs_seal)
    if sealed or guard_tiles or n_open:
        print(f"  L{level} border seal: {len(sealed)} cells closed, "
              f"{len(guard_tiles)} back-path guards"
              + (f", {n_open} crossings left free (unguardable)" if n_open else ""))
    for zr in zone_records:                          # keep pocket detection honest
        zr["passable"] -= sealed
        zr["open_set"] -= sealed | guard_tiles

    return objs, targets, zone_records, town_of_zone, has_water, nz, frozenset(ridge), frozenset(guard_tiles)


def _warn_sliver_zones(zones, level, protect=frozenset()):
    """Dev-time guard on ZE._despeckle_ids' shape rule (a zone must be >4 tiles or a compact
    2x2 square — anything narrower can't hold gameplay). The despeckle fixpoint loop caps at
    24 iterations, so a pathological grid could in principle leak a sliver through; surface
    it loudly instead of silently generating an unplayable zone. `protect` cells (tunnel
    corridors) are exempt — despeckle deliberately never merges them."""
    for zid, z in sorted(zones.items()):
        if ZE.TNAME.get(z["terrain_type"]) in (None, "water", "rock"):
            continue
        if z["tiles_set"] & protect:
            continue
        if not ZE._keep_patch(list(z["tiles_set"])):
            print(f"  WARNING: L{level} zone {zid} is a sliver ({z['area']} tiles) — "
                  f"despeckle should have absorbed it")


def _land_tiles(zones):
    """Union of tiles across every zone `_run_level` would actually process (same filter:
    not water/rock/unlabeled, at least MIN_AREA) — used to compute `place_gates`'s candidate
    set BEFORE `_run_level` runs, so a gate can claim a tile ahead of any zone's own density."""
    out = set()
    for z in zones.values():
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < MIN_AREA:
            continue
        out |= set(z["tiles_set"])
    return out


def _repair_and_finish_level(level, size, grid, objs, targets, zone_records, seed,
                             boat_ok=True, ridge=frozenset(), seerhut_artifacts=None,
                             border_guards=frozenset()):
    """Thin wrapper over steps.repair.step._repair_one_level, kept so this legacy build()
    path and RepairStep share the identical logic (pipeline-refactor-v2-folders.md, Phase 5).
    G2 map-level gate + island repair + guarded pocket caches + dup-guard cleanup for ONE
    already-fully-populated level (gates included). MUST run before pocket detection
    (user-mandated: "the pocket detection should run after the map is fully crafted" — a
    pocket is a zone-independent property of the FINAL, fully-repaired passability field, not
    of the raw per-zone snapshot taken while vegetation/gameplay were still being placed).
    `boat_ok` is forwarded to `fill_open_islands` — False for the underground level, which has
    no boat mechanic to excuse a stranded-target component (see that function's docstring).
    Returns (objs, ncarved, nreconn, nfilled, n_pockets, ndrop)."""
    return _repair_one_level(level, size, grid, objs, targets, zone_records, seed,
                             boat_ok=boat_ok, ridge=ridge,
                             seerhut_artifacts=seerhut_artifacts,
                             border_guards=border_guards)

def _gate_anchor_points(W, H, seed, n_sites=8, margin=8, pad=4):
    """N (x, y) anchors spread across the interior (grid jitter + RNG offset), each with
    enough clearance (`margin`) that its own `pad`-radius block stays fully in-bounds."""
    import random

    rng = random.Random(seed ^ 0xA7E5)
    cols = max(1, round(n_sites ** 0.5))
    rows = -(-n_sites // cols)
    lo, hi_x, hi_y = margin, W - margin, H - margin
    anchors = []
    for i in range(n_sites):
        gx = lo + (hi_x - lo) * ((i % cols) + 0.5) / cols
        gy = lo + (hi_y - lo) * ((i // cols) + 0.5) / rows
        ax = min(W - 2 - pad, max(margin, int(gx) + rng.randint(-3, 3)))
        ay = min(H - 3 - pad, max(margin, int(gy) + rng.randint(-3, 3)))
        anchors.append((ax, ay))
    return anchors


def _gate_site_cells(ax, ay, pad=4):
    """The organic (circular, not square) disc of cells `_carve_gate_sites` carves/reserves
    at one anchor — a rounded cavern-like patch reads as a natural cave chamber, where a
    hard-edged square looked like an obvious generation artifact next to the tunnel-network
    terrain (`macro_topo._tunnel_mask`)."""
    r2 = (pad + 0.5) ** 2
    return {(ax + dx, ay + dy) for dy in range(-pad, pad + 1) for dx in range(-pad, pad + 1)
            if dx * dx + dy * dy <= r2}


def _carve_gate_sites(grid0, grid1, W, H, anchors, seed, pad=4):
    """Force a handful of small joint openings (mutates grid0/grid1 in place) so
    Subterranean Gate placement always has candidates. Level0/level1 terrain come from
    independent RNG streams (seed vs seed^0x51E9) with no spatial correlation, and real
    underground corpus terrain is rock-dominated (~85-90% barrier), so a patch that is
    walkable on BOTH levels at the identical (x, y) -- the gate mask footprint plus its
    approach row -- is not guaranteed to occur by chance. Each grid's carved cells are set
    to THAT grid's own dominant non-barrier terrain code, so this only guarantees a site
    exists; it does not touch the corpus-mined terrain-mix statistics. The carved disc is
    padded well past the gate's own 4x4 footprint (mask bbox + approach row) so that, even
    when the patch is fully rock/water-locked, its own connected-component area still clears
    `_run_level`'s MIN_AREA zone floor and survives into `zone_records` for `place_gates` —
    `build()` calls `place_gates` right after segmentation, before either level's zone
    gameplay/density pass runs, so the gate claims its own small footprint here first and
    ordinary `place_zone`/vegetation/scatter density simply decorates around it afterwards
    (see `place_zone`'s `preoccupied` parameter), the same as any other pre-placed object.

    On level 1 an anchor dropped into open rock, far from `_tunnel_mask`'s already-carved
    cave network, would otherwise sit as its own sealed island — exactly the disconnected
    "octagon patch" the corpus reads as a bug (real subterrain is tunnels leading to a larger
    cavern, not scattered unconnected rooms). So each disc also gets a drunken-walk corridor
    stub (`macro_topo._carve_corridor`) back to the nearest pre-existing underground land
    tile, and every cell that stub paints is returned so the caller can fold it into the same
    `protect` set used for the corpus's own tunnels — otherwise the thin stub would be eroded
    right back to rock by `_texture_boundaries`/`_despeckle_ids` downstream. Returns the
    level-1 protect cell set (empty when `grid1` is None, i.e. no subterrain level)."""
    import random
    land0 = collections.Counter(grid0[y][x] for y in range(H) for x in range(W)
                                 if grid0[y][x] != TSG.WATER)
    fill0 = land0.most_common(1)[0][0] if land0 else 2
    protect1 = set()
    if grid1 is None:
        for ax, ay in anchors:
            for x, y in _gate_site_cells(ax, ay, pad):
                if 0 <= x < W and 0 <= y < H:
                    grid0[y][x] = fill0
        return protect1

    land1 = collections.Counter(grid1[y][x] for y in range(H) for x in range(W)
                                 if grid1[y][x] not in (TSG.WATER, TSG.ROCK))
    fill1 = land1.most_common(1)[0][0] if land1 else 6
    land1_before = {(x, y) for y in range(H) for x in range(W)
                    if grid1[y][x] not in (TSG.WATER, TSG.ROCK)}
    rng = random.Random(seed ^ 0xC0DE)
    for ax, ay in anchors:
        for x, y in _gate_site_cells(ax, ay, pad):
            if 0 <= x < W and 0 <= y < H:
                grid0[y][x] = fill0
                grid1[y][x] = fill1
        if land1_before:
            tx, ty = min(land1_before, key=lambda t: (t[0] - ax) ** 2 + (t[1] - ay) ** 2)
            land_bool = [[grid1[y][x] not in (TSG.WATER, TSG.ROCK) for x in range(W)]
                         for y in range(H)]
            MTOPO._carve_corridor(land_bool, (ax, ay), (tx, ty), W, H, rng,
                                  half_w=1, protect=protect1)
            for y in range(H):
                for x in range(W):
                    if land_bool[y][x] and grid1[y][x] in (TSG.WATER, TSG.ROCK):
                        grid1[y][x] = fill1
        land1_before.add((ax, ay))
    return protect1


def build(seed=3, size=72, water=None, players=0, water_mode="normal", subterrain=False):
    """Generate macro terrain + gameplay + vegetation, optionally with a second, underground
    level (`subterrain=True`) connected to the surface by Subterranean Gate pairs.
    Returns (levels, surfs, objs, info, player_towns): `levels`/`surfs` are 1-element lists
    (surface only) or 2-element lists (surface + underground); with players > 0 the player
    zones are picked by `select_player_zones` across BOTH active levels (big AND mutually far
    apart) and each is guaranteed a town at its CENTROID; `player_towns` lists those town
    objects in player order. `water_mode`: 'none' | 'normal' | 'islands' (see
    macro_topo.generate; underground always carves ROCK as its own corpus-drawn barrier,
    independent of `water_mode`)."""
    W = H = size
    grid0 = MTOPO.generate(size, size, seed=seed, water=water, water_mode=water_mode, level=0)
    tunnel_protect = set()
    if subterrain:
        grid1 = MTOPO.generate(size, size, seed=seed ^ 0x51E9, level=1, protect_out=tunnel_protect)
        # level0/level1 terrain are independent RNG streams, so a jointly-walkable
        # gate footprint is not guaranteed to occur by chance (measured: 0/3 seeds
        # did) -- carve a few before segmentation so `place_gates` has real sites.
        gate_anchors = _gate_anchor_points(W, H, seed)
        tunnel_protect |= _carve_gate_sites(grid0, grid1, W, H, gate_anchors, seed)
    # corpus-learned autotiling: despeckles + picks the H3-correct transition view per tile
    cells0 = ZE.tile_terrain(grid0, W, H)
    grid0 = [[c["t"] for c in row] for row in cells0]
    surf0 = [[FA.tile_string(c) for c in row] for row in cells0]
    zones0, _zl0, _ = ZE._segment_level(cells0)
    _warn_sliver_zones(zones0, 0)

    zones_by_level = {0: zones0}
    gate_occ0 = gate_blk0 = gate_occ1 = gate_blk1 = frozenset()
    gate_appr0 = gate_appr1 = ()
    gobjs0_pre = gobjs1_pre = []
    if subterrain:
        cells1 = ZE.tile_terrain(grid1, W, H, protect=tunnel_protect)
        grid1 = [[c["t"] for c in row] for row in cells1]
        surf1 = [[FA.tile_string(c) for c in row] for row in cells1]
        zones1, _zl1, _ = ZE._segment_level(cells1)
        _warn_sliver_zones(zones1, 1, protect=frozenset(tunnel_protect))
        zones_by_level[1] = zones1

        # Subterranean Gate pairs are placed FIRST, right after segmentation and before
        # either level's zone gameplay/vegetation pass runs (candidates = tiles walkable as
        # LAND zones on both levels; nothing is placed anywhere yet, so `place_gates` gets
        # first, uncontested pick). Each gate then seeds its own zone's `place_zone` call
        # (via `preoccupied`/`preblocked`/`preapproaches`) exactly like a pre-placed mine or
        # town, so ordinary density decorates right up to its GAP buffer instead of a whole
        # carved disc being held bare.
        ts0 = _land_tiles(zones0)
        ts1 = _land_tiles(zones1)
        # gates stay OFF both levels' inter-zone rims (passed through place_gates's
        # `reserved` doorway sets): a gate footprint/approach pinned on a border would be a
        # permanently-open, unsealable hole in the isolation ridge.
        (gobjs0_pre, gate_occ0, gate_blk0, gate_appr0), (gobjs1_pre, gate_occ1, gate_blk1, gate_appr1) = \
            PG.place_gates(ts0, ts1, set(), set(),
                           appr0=_rim8(zones0), appr1=_rim8(zones1), seed=seed)
        print(f"  gates: {len(gobjs0_pre)} Subterranean Gate pair(s) placed")

    player_zids = select_player_zones(zones_by_level, players)
    if players and len(player_zids) < players:
        print(f"  WARNING: only {len(player_zids)} zones can host a player town "
              f"(requested {players})")
    zids_by_level = collections.defaultdict(set)
    for lvl, zid in player_zids:
        zids_by_level[lvl].add(zid)

    # MAP-level mine economy: every basic resource covered somewhere, gold rationed to the
    # town count (zones are visited in sorted-zid order — deterministic); shared across both
    # levels since a mine placed underground still counts toward map-wide coverage.
    ledger = {"missing": set(PG.BASIC_MINE_RES), "towns": len(player_zids), "gold": 0}

    gstats0 = PG.mine_gameplay(level=0)
    objs0, targets0, zone_records0, town_of_zone0, has_water0, nz0, ridge0, border_guards0 = _run_level(
        0, W, H, grid0, zones0, zids_by_level[0], ledger, gstats0, seed, subterrain,
        gate_occ=gate_occ0, gate_blk=gate_blk0, gate_appr=gate_appr0)
    objs0.extend(gobjs0_pre)

    objs1 = targets1 = zone_records1 = town_of_zone1 = None
    nz1 = 0
    ridge1 = frozenset()
    border_guards1 = frozenset()
    if subterrain:
        gstats1 = PG.mine_gameplay(level=1)
        objs1, targets1, zone_records1, town_of_zone1, _has_water1, nz1, ridge1, border_guards1 = _run_level(
            1, W, H, grid1, zones1, zids_by_level[1], ledger, gstats1, seed, subterrain,
            gate_occ=gate_occ1, gate_blk=gate_blk1, gate_appr=gate_appr1,
            tunnel_protect=frozenset(tunnel_protect))
        objs1.extend(gobjs1_pre)

    # unreachable-zone rescue: guarded two-way monolith + special reward upgrade, BEFORE the
    # repair pass so the rescued zones' targets stop `fill_open_islands` from burying them.
    grids = {0: grid0}
    objs_by_level = {0: objs0}
    targets_by_level = {0: targets0}
    zone_records_by_level = {0: zone_records0}
    if subterrain:
        grids[1] = grid1
        objs_by_level[1] = objs1
        targets_by_level[1] = targets1
        zone_records_by_level[1] = zone_records1
    gate_xy = {(o["x"], o["y"]) for o in gobjs0_pre}
    start = None
    for lvl, zid in player_zids:                      # seed = first player town's tile
        t = (town_of_zone0 if lvl == 0 else town_of_zone1 or {}).get(zid)
        if t is not None:
            start = (lvl, (t["x"], t["y"]))
            break
    if start is None:                                 # no players: largest surface land zone
        big = max((z for z in zones0.values()
                   if ZE.TNAME.get(z["terrain_type"]) not in (None, "water", "rock")),
                  key=lambda z: z["area"], default=None)
        if big is not None:
            bx, by = big["centroid"]
            start = (0, min(big["tiles_set"],
                            key=lambda t: ((t[0] - bx) ** 2 + (t[1] - by) ** 2, t)))
    n_portals = 0
    if start is not None:
        n_portals = rescue_unreachable_zones(
            size, grids, zones_by_level, objs_by_level, targets_by_level,
            zone_records_by_level, start, gate_xy, seed)

    # G2 repair / island-fill / pocket caches / dup-guard cleanup run per level — each level
    # is its own passability field. A single `seerhut_artifacts` set is shared across both
    # calls so a named quest artifact is never doubly placed on both levels of the same map.
    seerhut_artifacts = set()
    objs0, ncarved0, nreconn0, nfilled0, npockets0, ndrop0 = _repair_and_finish_level(
        0, size, grid0, objs0, targets0, zone_records0, seed, ridge=ridge0,
        seerhut_artifacts=seerhut_artifacts, border_guards=border_guards0)
    if subterrain:
        objs1, ncarved1, nreconn1, nfilled1, npockets1, ndrop1 = _repair_and_finish_level(
            1, size, grid1, objs1, targets1, zone_records1, seed, boat_ok=False, ridge=ridge1,
            seerhut_artifacts=seerhut_artifacts, border_guards=border_guards1)
        # place_zone/pp_pickup/pp_sample always tag l=0 by default (level-agnostic helpers
        # called twice, not level-aware internally) — retag the whole underground level's
        # objects in this single post-pass. Gate objects already carry the right l (0/1)
        # from place_gates, so this is a harmless no-op for them.
        for o in objs1:
            o["l"] = 1

    objs = objs0 + (objs1 if subterrain else [])

    if ledger["missing"]:
        print(f"  WARNING: mine coverage incomplete — missing {sorted(ledger['missing'])} "
              f"(map too small / too few mine slots)")
    veg_n = sum(1 for o in objs if not o.get("purpose"))
    ndrop = ndrop0 + (ndrop1 if subterrain else 0)
    ncarved = ncarved0 + (ncarved1 if subterrain else 0)
    nreconn = nreconn0 + (nreconn1 if subterrain else 0)
    nfilled = nfilled0 + (nfilled1 if subterrain else 0)
    nz = nz0 + nz1
    info = (f"pp-map s{seed} {W}x{H}: {nz} zones, {len(objs) - veg_n} gameplay+pickups "
            f"({ndrop} dup guards removed), {veg_n} vegetation objects, "
            f"mines all-basics={'yes' if not ledger['missing'] else 'NO'} "
            f"gold={ledger['gold']}/{max(0, ledger['towns'] - 1)} towns={ledger['towns']}"
            + (f" (G2 repair carved {ncarved} veg)" if ncarved else " (G2 clean)")
            + (f" (islands: {nreconn} reconnected, {nfilled} filled)"
               if (nreconn or nfilled) else "")
            + (f" (portal reward zones: {n_portals})" if n_portals else "")
            + (f" (subterrain: L1 {nz1} zones, {npockets0 + npockets1} pockets)"
               if subterrain else f" (pockets: {npockets0})"))

    # player towns in zone-rank order; top up from surplus neutral towns if a forced
    # placement failed (rare: no legal anchor in the zone)
    town_lookup = {(0, zid): t for zid, t in town_of_zone0.items()}
    if subterrain:
        town_lookup.update({(1, zid): t for zid, t in town_of_zone1.items()})
    player_towns = [town_lookup[lz] for lz in player_zids if lz in town_lookup]
    if players:
        spare = [o for o in objs if o.get("purpose") == "TOWN" and o not in player_towns]
        player_towns += spare[:max(0, players - len(player_towns))]
        player_towns = player_towns[:players]

    levels = [cells0, cells1] if subterrain else [cells0]
    surfs = [surf0, surf1] if subterrain else [surf0]
    return levels, surfs, objs, info, player_towns


VCMI_MAPS_DIR = os.path.join(VPATH.vcmi_home(), "Maps", "pp-gen")


def gen_one(seed, size, water=None, water_mode="normal", players=2, teams_spec="ffa",
            vmap=True, install=False, tag="", name=None, subterrain=False):
    """Generate one playable map: PNG render + (optionally) a playable .vmap; with
    `install` the .vmap is also copied into the VCMI Maps/pp-gen/ folder so the editor
    lists it. With `subterrain`, a second underground-level PNG is also rendered.
    Returns (png_path, vmap_path_or_None)."""
    levels, surfs, objs, info, ptowns = build(seed=seed, size=size, water=water,
                                              players=players, water_mode=water_mode,
                                              subterrain=subterrain)
    print(info)
    from vcmi_mapgen import render_editor as RED
    stem = f"ppmap_s{seed}{tag}"
    png = os.path.join(ROOT, "out", "render", "pp", f"{stem}.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    objs0 = [o for o in objs if o.get("l", 0) == 0]
    RED.render_map(surfs[0], objs0, title="").save(png)
    print("->", png)
    if subterrain:
        png1 = os.path.join(ROOT, "out", "render", "pp", f"{stem}_L1.png")
        # render_map only draws l==0 objects; shift a copy of the underground objects to
        # l=0 for this second, level-1-only render.
        objs1 = [dict(o, l=0) for o in objs if o.get("l", 0) == 1]
        RED.render_map(surfs[1], objs1, title="").save(png1)
        print("->", png1)
    if not vmap:
        return png, None
    label = f"{name}{tag}" if name else f"pp {stem} {water_mode} {players}p"
    vp = export_vmap(levels, objs, os.path.join(ROOT, "out", "vmap", f"{stem}.vmap"),
                     name=label)
    if ptowns:
        try:
            teams = parse_teams(teams_spec, len(ptowns))
        except ValueError as e:
            print(f"  WARNING: {e} — falling back to ffa")
            teams = list(range(len(ptowns)))
        apply_playability(vp, ptowns, teams)
        print(f"   playable: {len(ptowns)} players, teams={teams}, victory=defeat-all")
    if install:
        import shutil
        os.makedirs(VCMI_MAPS_DIR, exist_ok=True)
        shutil.copy2(vp, os.path.join(VCMI_MAPS_DIR, os.path.basename(vp)))
        print(f"   installed -> {VCMI_MAPS_DIR}/{os.path.basename(vp)}")
    print("->", vp)
    return png, vp


def _pipeline_gen_one(seed, size, water=None, water_mode="normal", players=2,
                      teams_spec="ffa", vmap=True, install=False, tag="",
                      name=None, subterrain=False):
    """Generate one map using VcmiMapGenPipeline + renderers."""
    from vcmi_mapgen.pipeline import PlacementWorkspace, VcmiMapGenPipeline
    from vcmi_mapgen.steps import (TerrainGenStep, TileStep, SegmentStep,
                                   GateStep, GameplayStep, PickupStep,
                                   VegetationStep, RepairStep)
    from vcmi_mapgen.renderers import PngRenderer, VmapRenderer

    # shared inter-step handoff object (Gameplay -> Vegetation -> Pickup -> Repair);
    # see pipeline.PlacementWorkspace and pipeline-refactor-v2-folders.md
    workspace = PlacementWorkspace()

    pipeline = VcmiMapGenPipeline(ontology=None)
    pipeline.add_step(TerrainGenStep(size=size, seed=seed, water=water,
                                     water_mode=water_mode, subterrain=subterrain))
    pipeline.add_step(TileStep())
    pipeline.add_step(SegmentStep())
    if subterrain:
        pipeline.add_step(GateStep(seed=seed))
    pipeline.add_step(GameplayStep(seed=seed, players=players, workspace=workspace))
    pipeline.add_step(VegetationStep(seed=seed, workspace=workspace))
    pipeline.add_step(PickupStep(seed=seed, workspace=workspace))
    pipeline.add_step(RepairStep(seed=seed, workspace=workspace))
    state = pipeline.run()

    stem = f"ppmap_s{seed}{tag}"
    png_renderer = PngRenderer()
    png = png_renderer.save(state, f"{stem}.png", level=0)
    print("->", png)
    if subterrain:
        png1 = png_renderer.save(state, f"{stem}_L1.png", level=1)
        print("->", png1)

    if not vmap:
        return png, None

    label = f"{name}{tag}" if name else f"pp {stem} {water_mode} {players}p"
    vmap_renderer = VmapRenderer()
    vp = vmap_renderer.render(state, f"{stem}.vmap", name=label,
                              teams_spec=teams_spec)
    if install:
        import shutil
        os.makedirs(VCMI_MAPS_DIR, exist_ok=True)
        shutil.copy2(vp, os.path.join(VCMI_MAPS_DIR, os.path.basename(vp)))
        print(f"   installed -> {VCMI_MAPS_DIR}/{os.path.basename(vp)}")
    print("->", vp)
    return png, vp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--size", type=int, default=72)
    ap.add_argument("--water", type=float, default=None,
                    help="explicit water fraction override")
    ap.add_argument("--water-mode", choices=["none", "normal", "islands"], default=None,
                    dest="water_mode",
                    help="water style; in --batch, unset cycles all three for diversity")
    ap.add_argument("--vmap", action="store_true",
                    help="also export a playable .vmap to out/vmap/")
    ap.add_argument("--players", type=int, default=2,
                    help="number of players; the N largest zones each get a start town")
    ap.add_argument("--teams", default="ffa",
                    help="team matrix: 'ffa', '2v2'-style groups, or explicit '0,0,1,1'")
    ap.add_argument("--batch", type=int, default=0,
                    help="generate N maps (seeds seed..seed+N-1); water modes cycle unless "
                         "--water-mode is set; implies --vmap")
    ap.add_argument("--install", action="store_true",
                    help="copy the .vmap(s) into the VCMI Maps/pp-gen/ folder so the "
                         "editor can open them (explicit opt-in)")
    ap.add_argument("--name", default=None,
                    help="custom map display name shown in VCMI's map selector "
                         "(default: auto-generated from seed/water-mode/players)")
    ap.add_argument("--subterrain", action="store_true",
                    help="add a second, underground level connected to the surface by "
                         "Subterranean Gate pairs (classic-sparse scope, corpus-mined stats)")
    ap.add_argument("--use-pipeline", action="store_true",
                    help="use VcmiMapGenPipeline instead of the legacy build() function")
    args = ap.parse_args()

    gen_fn = _pipeline_gen_one if args.use_pipeline else gen_one

    if args.batch:
        modes = [args.water_mode] if args.water_mode else ["normal", "islands", "none"]
        for i in range(args.batch):
            seed = args.seed + i
            mode = modes[i % len(modes)]
            print(f"=== batch {i + 1}/{args.batch}: seed={seed} water={mode} ===")
            gen_fn(seed, args.size, water=args.water, water_mode=mode,
                   players=args.players, teams_spec=args.teams, vmap=True,
                   install=args.install, tag=f"_{mode}", name=args.name,
                   subterrain=args.subterrain)
        return
    gen_fn(args.seed, args.size, water=args.water,
           water_mode=args.water_mode or "normal", players=args.players,
           teams_spec=args.teams, vmap=args.vmap or args.install, install=args.install,
           name=args.name, subterrain=args.subterrain)


if __name__ == "__main__":
    main()
