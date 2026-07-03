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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import faithful as FA           # noqa: E402
import macro_topo as MTOPO      # noqa: E402
import obj_resolve as OR        # noqa: E402
import pp_gameplay as PG        # noqa: E402
import pp_pickup as PK          # noqa: E402
import pp_sample as PP          # noqa: E402
import zone_engine as ZE        # noqa: E402
import zone_field as ZF         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_AREA = 25          # vegetate even smallish zones (the stats floor stays 60 in pp_stats)


def g2_repair(size, grid, objs, targets):
    """Map-level G2 validity gate + repair: every target tile (gameplay approach, pickup)
    must be reachable from every other across zone borders. Pickups/monsters count as
    passable (they are removable); vegetation is carvable; gameplay bodies and water/rock
    are not. Unreachable targets get a least-vegetation corridor carved to them (Dijkstra:
    open=1, veg-blocked=40) and the intersecting VEGETATION objects are deleted.
    Returns (objs, removed_count)."""
    import heapq
    import obj_resolve as OR
    W = H = size
    land = {(x, y) for y in range(H) for x in range(W) if grid[y][x] < 8}

    def veg_cells():
        cells = collections.defaultdict(list)        # blocking cell -> [veg obj idx]
        hard = set()                                 # gameplay bodies: never carved
        for i, o in enumerate(objs):
            purpose = o.get("purpose")
            removable_pickup = purpose in ("RESOURCE_PILE", "REWARD_PICKUP", "GUARD")
            for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
                if not blk or removable_pickup:
                    continue
                if purpose is None:
                    cells[(cx, cy)].append(i)
                else:
                    hard.add((cx, cy))
        return cells, hard

    removed = set()
    for _round in range(6):
        cells, hard = veg_cells()
        open_set = land - set(cells) - hard
        targets_in = [t for t in targets if t in open_set]
        if not targets_in:
            break
        root = targets_in[0]
        seen = {root}
        q = collections.deque([root])
        while q:
            x, y = q.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in open_set and n not in seen:
                        seen.add(n)
                        q.append(n)
        bad = [t for t in targets_in if t not in seen]
        if not bad:
            break
        # one Dijkstra over the whole field (veg priced high, gameplay/water = wall), then
        # carve to the first bad target that is land-connectable at all — targets on OTHER
        # ISLANDS are legitimately boat-reachable only and must not abort the repair loop
        dist = {root: 0.0}
        prev = {}
        heap = [(0.0, root)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, 1e18):
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (u[0] + dx, u[1] + dy)
                    if n not in land or n in hard:
                        continue
                    nd = d + (40.0 if n in cells else 1.0)
                    if nd < dist.get(n, 1e18):
                        dist[n] = nd
                        prev[n] = u
                        heapq.heappush(heap, (nd, n))
        tgt = next((t for t in bad if t in prev), None)
        if tgt is None:
            break                                    # all remaining bad targets are off-island
        node = tgt
        while node != root:
            for i in cells.get(node, ()):
                removed.add(i)
            node = prev[node]
    if removed:
        objs = [o for i, o in enumerate(objs) if i not in removed]
    return objs, len(removed)


def export_vmap(cells, objs, out_path, name="pp-map"):
    """Write the generated map as an editor-valid .vmap via the proven faithful writer.
    The first town becomes player 0's start (faithful.to_vmap wires mainTown slots)."""
    towns = [o for o in objs if o.get("purpose") == "TOWN"]
    fm = {"name": name, "terrain": [cells],
          "objects": [o for o in objs if o.get("type")],
          "main_town": ({"l": 0, "x": towns[0]["x"] - 2, "y": towns[0]["y"] - 2}
                        if towns else None)}
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
            pl.pop("allowedFactions", None)          # randomTown start: any faction
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


def select_player_zones(zones, players):
    """Deterministic player-zone pick: big land zones that are MUTUALLY FAR APART, so the
    players do not all start next to each other. Candidates are land zones >= 60 tiles,
    preferring real zones (>= 100 tiles and >= 1/4 of the largest). The first pick is the
    largest zone; each next pick greedily maximizes the minimum centroid distance to the
    zones already chosen (tie-break: area desc, zid). Returns player_zids in player order."""
    cand = [(z["area"], zid, z["centroid"]) for zid, z in zones.items()
            if ZE.TNAME.get(z["terrain_type"]) in PG.LAND and z["area"] >= 60]
    if not cand or players <= 0:
        return []
    cand.sort(key=lambda c: (-c[0], c[1]))
    amax = cand[0][0]
    pool = [c for c in cand if c[0] >= max(100, amax // 4)]
    if len(pool) < players:                          # too few big zones: admit smaller ones
        pool = cand
    chosen = [pool[0]]
    rest = pool[1:]
    while len(chosen) < players and rest:
        best = max(rest, key=lambda c: (min((c[2][0] - ch[2][0]) ** 2
                                            + (c[2][1] - ch[2][1]) ** 2 for ch in chosen),
                                        c[0], -c[1]))
        chosen.append(best)
        rest.remove(best)
    return [zid for _a, zid, _c in chosen]


def build(seed=3, size=72, water=None, players=0, water_mode="normal"):
    """Generate macro terrain + gameplay + vegetation.
    Returns (cells, surf, objs, info, player_towns): with players > 0 the player zones are
    picked by `select_player_zones` (big AND mutually far apart) and each is guaranteed a
    town at its CENTROID; `player_towns` lists those town objects in player order.
    `water_mode`: 'none' | 'normal' | 'islands' (see macro_topo.generate)."""
    grid = MTOPO.generate(size, size, seed=seed, water=water, water_mode=water_mode)
    W = H = size
    # corpus-learned autotiling: despeckles + picks the H3-correct transition view per tile
    cells = ZE.tile_terrain(grid, W, H)
    grid = [[c["t"] for c in row] for row in cells]
    surf = [[FA.tile_string(c) for c in row] for row in cells]
    zones, zl, _ = ZE._segment_level(cells)

    player_zids = select_player_zones(zones, players)
    if players and len(player_zids) < players:
        print(f"  WARNING: only {len(player_zids)} zones can host a player town "
              f"(requested {players})")
    town_of_zone = {}

    # MAP-level mine economy: every basic resource covered somewhere, gold rationed to the
    # town count (zones are visited in sorted-zid order — deterministic)
    ledger = {"missing": set(PG.BASIC_MINE_RES), "towns": len(player_zids), "gold": 0}
    gstats = PG.mine_gameplay()

    models = {}
    objs = []
    targets = []                                     # G2: tiles that must stay reachable
    nz = ngame = npick = 0
    # water is a segmentation BARRIER (never a zone) — populate its connected bodies directly:
    # flotsam / sea chests / buoys / boats / whirlpools / wrecks / sea guards
    water = {(x, y) for y in range(H) for x in range(W) if grid[y][x] == 8}
    seen_w = set()
    wi = 0
    for t0 in sorted(water):
        if t0 in seen_w:
            continue
        comp, q = {t0}, [t0]
        while q:
            x, y = q.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in water and n not in comp:
                    comp.add(n)
                    q.append(n)
        seen_w |= comp
        if len(comp) >= MIN_AREA:
            wobjs = PK.place_water(comp, zones, 1000 + wi, seed=seed)
            objs.extend(wobjs)
            npick += len(wobjs)
            print(f"  sea  {wi:>3} water    {len(comp):>5} tiles: {len(wobjs):>3} sea objects")
        wi += 1

    for zid, z in sorted(zones.items()):
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < MIN_AREA:
            continue
        ts = set(z["tiles_set"])
        coastal = frozenset(t for t in ts
                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                            if 0 <= t[0] + dx < W and 0 <= t[1] + dy < H
                            and grid[t[1] + dy][t[0] + dx] == 8)

        # L3 gameplay first: rigid objects at spread nodes (corpus densities, ontology pools)
        gobjs, occupied, gblocked, approaches = PG.place_zone(ts, zones, zid, terrain,
                                                              seed=seed, coastal=coastal,
                                                              force_town=zid in player_zids,
                                                              ledger=ledger)
        objs.extend(gobjs)
        ngame += len(gobjs)
        if zid in player_zids:
            t = next((o for o in gobjs if o.get("purpose") == "TOWN"), None)
            if t is not None:
                town_of_zone[zid] = t
            else:
                print(f"  WARNING: player zone {zid} could not fit its town")

        # protected walkable web: backbone + gates + gameplay approaches, routed around the
        # IMPASSABLE gameplay cells (approach tiles themselves are passable and stay nodes)
        edist = ZF.edge_dist(ts)
        cx, cy = z["centroid"]
        seedt = min(ts, key=lambda t: (t[0] - int(round(cx))) ** 2
                    + (t[1] - int(round(cy))) ** 2)
        prot = PP.protected_web(ts, zones, zid, edist, seedt,
                                extra_nodes=approaches, avoid=gblocked,
                                open_frac=gstats[terrain].get("border_open_frac", 0.5))

        # L2 vegetation: gameplay cells + approaches admit no vegetation at all; the
        # annulus around MINE footprints ATTRACTS vegetation (sawmills nestle in forest)
        if terrain not in models:
            models[terrain] = PP.build_model(terrain)
        model = models[terrain]
        if not model["cats"]:
            continue
        forbid = frozenset(occupied) | frozenset(approaches)
        mine_cells = {(cx, cy) for o in gobjs if o.get("purpose") == "MINE"
                      for cx, cy, blk in OR.mask_cells(o["mask"], o["x"], o["y"]) if blk}
        attract = frozenset(t for t in ts if t not in forbid and any(
            max(abs(t[0] - mx), abs(t[1] - my)) <= 3 for mx, my in mine_cells)) \
            if mine_cells else frozenset()
        zobjs, blocked, _ = PP.sample_zone(ts, zones, zid, model, seed=seed,
                                           prot=prot, forbid=forbid, attract=attract)
        objs.extend(zobjs)

        # L4 pickups over the finished open field (resources, artifacts, monster guards);
        # approach tiles stay clear — mines may already carry their guard there
        open_set = ts - blocked - gblocked - set(occupied) - set(approaches)
        pobjs = PK.place_pickups(ts, zones, zid, terrain, open_set, prot, seed=seed)
        objs.extend(pobjs)
        npick += len(pobjs)
        targets.extend(approaches)
        targets.extend((o["x"], o["y"]) for o in pobjs)
        nz += 1
        pk = collections.Counter(o["purpose"] for o in pobjs)
        print(f"  zone {zid:>3} {terrain:<8} {z['area']:>5} tiles: {len(gobjs):>2} gameplay, "
              f"{len(zobjs):>4} veg (blocked {len(blocked) / len(ts):.2f}/"
              f"{model['target']:.2f}), pickups res={pk.get('RESOURCE_PILE', 0)} "
              f"art={pk.get('REWARD_PICKUP', 0)} guard={pk.get('GUARD', 0)}")
    # both sides of one corridor may have guarded the same gate — keep only the stronger
    # of any two GUARDs within Chebyshev 2 (deterministic scan order)
    kept, drop = [], set()
    guards = [(i, o) for i, o in enumerate(objs) if o.get("purpose") == "GUARD"]
    for a in range(len(guards)):
        ia, oa = guards[a]
        if ia in drop:
            continue
        for b in range(a + 1, len(guards)):
            ib, ob = guards[b]
            if ib in drop:
                continue
            if max(abs(oa["x"] - ob["x"]), abs(oa["y"] - ob["y"])) <= 2:
                # randomMonsterLevelN sorts by N lexically (levels 1..7)
                drop.add(ib if str(oa.get("type")) >= str(ob.get("type")) else ia)
    if drop:
        objs = [o for i, o in enumerate(objs) if i not in drop]

    # G2 map-level gate: every approach/pickup reachable across zones, or carve vegetation
    objs, ncarved = g2_repair(size, grid, objs, targets)
    if ledger["missing"]:
        print(f"  WARNING: mine coverage incomplete — missing {sorted(ledger['missing'])} "
              f"(map too small / too few mine slots)")
    veg_n = sum(1 for o in objs if not o.get("purpose"))
    info = (f"pp-map s{seed} {W}x{H}: {nz} zones, {len(objs) - veg_n} gameplay+pickups "
            f"({len(drop)} dup guards removed), {veg_n} vegetation objects, "
            f"mines all-basics={'yes' if not ledger['missing'] else 'NO'} "
            f"gold={ledger['gold']}/{max(0, ledger['towns'] - 1)} towns={ledger['towns']}"
            + (f" (G2 repair carved {ncarved} veg)" if ncarved else " (G2 clean)"))
    # player towns in zone-rank order; top up from surplus neutral towns if a forced
    # placement failed (rare: no legal anchor in the zone)
    player_towns = [town_of_zone[z] for z in player_zids if z in town_of_zone]
    if players:
        spare = [o for o in objs if o.get("purpose") == "TOWN" and o not in player_towns]
        player_towns += spare[:max(0, players - len(player_towns))]
        player_towns = player_towns[:players]
    return cells, surf, objs, info, player_towns


VCMI_MAPS_DIR = "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/pp-gen"


def gen_one(seed, size, water=None, water_mode="normal", players=2, teams_spec="ffa",
            vmap=True, install=False, tag=""):
    """Generate one playable map: PNG render + (optionally) a playable .vmap; with
    `install` the .vmap is also copied into the VCMI Maps/pp-gen/ folder so the editor
    lists it. Returns (png_path, vmap_path_or_None)."""
    cells, surf, objs, info, ptowns = build(seed=seed, size=size, water=water,
                                            players=players, water_mode=water_mode)
    print(info)
    import render_editor as RED
    stem = f"ppmap_s{seed}{tag}"
    png = os.path.join(ROOT, "out", "render", "pp", f"{stem}.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    RED.render_map(surf, objs, title="").save(png)
    print("->", png)
    if not vmap:
        return png, None
    vp = export_vmap(cells, objs, os.path.join(ROOT, "out", "vmap", f"{stem}.vmap"),
                     name=f"pp {stem} {water_mode} {players}p")
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
    args = ap.parse_args()

    if args.batch:
        modes = [args.water_mode] if args.water_mode else ["normal", "islands", "none"]
        for i in range(args.batch):
            seed = args.seed + i
            mode = modes[i % len(modes)]
            print(f"=== batch {i + 1}/{args.batch}: seed={seed} water={mode} ===")
            gen_one(seed, args.size, water=args.water, water_mode=mode,
                    players=args.players, teams_spec=args.teams, vmap=True,
                    install=args.install, tag=f"_{mode}")
        return
    gen_one(args.seed, args.size, water=args.water,
            water_mode=args.water_mode or "normal", players=args.players,
            teams_spec=args.teams, vmap=args.vmap or args.install, install=args.install)


if __name__ == "__main__":
    main()
