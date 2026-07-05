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
NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
NUM_SWEEPS = 5  # Gibbs sweeps after the autoregressive seed pass
EDGE_W = 0.50  # strength of the rim-block bias (P(open) reduced by this at the very edge)
EDGE_R = 2     # tiles over which the rim-block bias ramps to zero (forest-belt thickness; THIN belt --
               # the freed blocked mass is relocated into the interior by the budget bisection)
SPACING = 6    # farthest-point node spacing for the spanning backbone (bigger -> fewer, fatter corridors)


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


def _run(x, y, dx, dy, openmap):
    """Length of the open run from (x,y) in direction (dx,dy), capped at R (the tile itself excluded)."""
    r = 0
    while openmap.get((x + dx * (1 + r), y + dy * (1 + r))) == 1:
        r += 1
        if r >= R:
            break
    return r


def edge_penalty(e, edge_w=EDGE_W, edge_r=EDGE_R):
    """Drop in P(open) for a tile `e` tiles from the rim: edge_w at the edge, ramping linearly to 0
    at distance edge_r. The learned edge_bin barely separates rim from interior, so this is the
    explicit forest-belt term -- higher P(blocked) the closer a tile is to the zone boundary."""
    if e >= edge_r:
        return 0.0
    return edge_w * (edge_r - e) / edge_r


def p_open_sym(x, y, openmap, edist, model, bias=0.0, edge_w=EDGE_W, edge_r=EDGE_R):
    """Symmetric Gibbs conditional from the causal (left/up) model, with an explicit rim-block bias.

    The learned model is P(open | horizontal_run, vertical_run, edge), where in raster learning the
    horizontal run is always the LEFT run and the vertical the UP run. For a Gibbs sweep the full
    Markov blanket is visible, so we take the run that CONTINUES in EITHER direction -- horizontal =
    max(left, right), vertical = max(up, down) -- the user's 'maximum in either direction' rule. Max
    (not average) preserves the sharp ~0.63 continuation probability, so open regions grow and
    coalesce instead of fragmenting. The edge_penalty term then lowers P(open) near the rim so a
    blocked forest belt rings the zone."""
    eb = min(edist.get((x, y), EBINS - 1), EBINS - 1)
    h = max(_run(x, y, -1, 0, openmap), _run(x, y, 1, 0, openmap))
    v = max(_run(x, y, 0, -1, openmap), _run(x, y, 0, 1, openmap))
    p = p_open((h, v, eb), model, bias) - edge_penalty(edist.get((x, y), edge_r), edge_w, edge_r)
    return min(1.0, max(0.0, p))


def generate(fm, zid, zones, model, seed=1, bias=0.0, sweeps=NUM_SWEEPS):
    """Gibbs/MRF sampler for the open/blocked field (see docs/specs/zone-field-mrf-generation.md).

    One autoregressive raster pass seeds `openmap`, then `sweeps` Gibbs passes rescan every tile in
    shuffled order, resampling from the local conditional P(open | left_run, up_run, edge_bin). The
    centroid-nearest tile is CLAMPED open (boundary condition) and never resampled. Multi-sweep lets
    open regions coalesce instead of the v1 one-pass salt-and-pepper.
    """
    import random
    z = zones[zid]
    ts = set(z["tiles_set"])
    edist = edge_dist(ts)
    cx, cy = z["centroid"]
    cx, cy = int(round(cx)), int(round(cy))
    # nearest in-zone tile to centroid = forced-open seed (clamped, never resampled)
    seedt = min(ts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    rng = random.Random(seed ^ (zid * 2654435761 & 0xFFFFFFFF))
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]

    # --- sweep 0: autoregressive raster pass to initialise the field ---
    openmap = {}
    for y in range(min(ys), max(ys) + 1):
        for x in range(min(xs), max(xs) + 1):
            if (x, y) not in ts:
                continue
            if (x, y) == seedt:
                openmap[(x, y)] = 1
                continue
            f = _feat(x, y, openmap, edist)
            openmap[(x, y)] = 1 if rng.random() < p_open(f, model, bias) else 0

    # --- Gibbs sweeps: full neighbourhood is now populated; shuffle order each pass ---
    order = list(ts)
    for _ in range(sweeps):
        rng.shuffle(order)
        for (x, y) in order:
            if (x, y) == seedt:
                openmap[(x, y)] = 1
                continue
            p = p_open_sym(x, y, openmap, edist, model, bias)
            openmap[(x, y)] = 1 if rng.random() < p else 0
    return ts, {t for t, v in openmap.items() if v == 1}, seedt


def generate_grow(fm, zid, zones, model, seed=1, bias=0.0, max_rounds=400):
    """Seeded frontier growth -- the connectivity-constrained sampler.

    A local Gibbs field cannot express 'every open tile is reachable from the centroid' (connectivity
    is global; clique potentials are local). So instead of sample-then-fix we GROW the open set
    outward from the clamped centroid seed: a frontier tile (blocked, 4-adjacent to the current open
    set) becomes open with probability p_open_sym(context). Because a tile only ever opens while
    touching an already-open tile, every open tile is 4-connected back to the seed BY CONSTRUCTION --
    the reachability guarantee, for free.

    Decisions are ONE-SHOT (Eden / invasion-percolation growth): each frontier tile is judged once,
    when first exposed; a rejected tile stays permanently blocked and becomes a hole in the web.
    Re-trying rejected tiles would turn their per-visit probability into "accept almost surely" over
    many visits, collapsing the field into a solid blob and destroying budget control -- so we do not.
    Open fraction is monotone in `bias` (lower bias -> sparser, more ramified cluster), so
    generate_budgeted's bisection tunes it to the target while connectivity stays guaranteed.
    """
    import random
    z = zones[zid]
    ts = set(z["tiles_set"])
    edist = edge_dist(ts)
    cx, cy = z["centroid"]
    cx, cy = int(round(cx)), int(round(cy))
    seedt = min(ts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    rng = random.Random(seed ^ (zid * 2654435761 & 0xFFFFFFFF))

    openmap = {t: 0 for t in ts}
    openmap[seedt] = 1

    def nbrs(t):
        x, y = t
        return [(x + dx, y + dy) for dx, dy in NB4 if (x + dx, y + dy) in ts]

    decided = {seedt}
    frontier = [n for n in nbrs(seedt) if openmap[n] == 0]
    infront = set(frontier)
    while frontier:
        i = rng.randrange(len(frontier))          # pop a random frontier tile (swap-remove)
        t = frontier[i]
        frontier[i] = frontier[-1]; frontier.pop()
        infront.discard(t)
        if t in decided:
            continue
        decided.add(t)
        p = p_open_sym(t[0], t[1], openmap, edist, model, bias)
        if rng.random() < p:
            openmap[t] = 1
            for n in nbrs(t):                     # expose newly-adjacent blocked tiles
                if openmap[n] == 0 and n not in decided and n not in infront:
                    frontier.append(n); infront.add(n)
        # else: permanently blocked (a web hole)
    return ts, {t for t, v in openmap.items() if v == 1}, seedt


def _geodesic_path(a, b, ts):
    """Shortest 4-connected path a->b staying inside the zone `ts`; [] if unreachable."""
    import collections as C
    prev = {a: None}
    q = C.deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            break
        x, y = cur
        for dx, dy in NB4:
            n = (x + dx, y + dy)
            if n in ts and n not in prev:
                prev[n] = cur
                q.append(n)
    if b not in prev:
        return []
    path, cur = [], b
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path


def _farthest_points(ts, seedt, spacing, cand=None):
    """Farthest-point sampling: node tiles spread across the zone so every tile is within ~`spacing`
    of a node. These are the destinations the spanning backbone must reach -> full-zone coverage.
    `cand` restricts where nodes may sit (e.g. interior-only, to keep the backbone off the rim)."""
    nodes = [seedt]
    if cand is None:
        cand = list(ts)
    s2 = spacing * spacing
    while True:
        best, bd = None, -1
        for t in cand:
            d = min((t[0] - n[0]) ** 2 + (t[1] - n[1]) ** 2 for n in nodes)
            if d > bd:
                bd, best = d, t
        if best is None or bd < s2:
            break
        nodes.append(best)
    return nodes


def _zone_fronts(ts, zones, zid):
    """Full contact FRONTS: {neighbour zid: [zone tiles 4-touching that neighbour]}. The complete
    per-pair border segment — `_zone_gates` collapses each front to one tile; `_zone_gate_bands`
    keeps a corpus-wide band of it."""
    owner = {}
    for zz, z in zones.items():
        for t in z["tiles_set"]:
            owner[t] = zz
    contacts = collections.defaultdict(list)
    for (x, y) in ts:
        for dx, dy in NB4:
            o = owner.get((x + dx, y + dy))
            if o is not None and o != zid:
                contacts[o].append((x, y))
    return contacts


def _zone_gate_bands(ts, zones, zid, open_frac=0.5, min_w=3):
    """Wide gates — corpus zone 'gates' are broad terrain borders, not 1-tile corridors.

    Returns [(rep, band)] per neighbouring zone: `rep` is the single representative tile
    (identical to `_zone_gates`) and `band` is a frozenset of contact-front tiles around it —
    the corpus-like OPEN share of the front (`open_frac` = fraction of corpus zone-border
    tiles left passable, mined per terrain), never fewer than `min_w` tiles. The protected
    web keeps the whole band vegetation-free, so the border stays as open as real maps.
    Isolated pockets get the synthesized antipodal pair with a small border band each."""
    contacts = _zone_fronts(ts, zones, zid)
    out = []
    for o in sorted(contacts):
        tiles = contacts[o]
        mx = sum(t[0] for t in tiles) / len(tiles)
        my = sum(t[1] for t in tiles) / len(tiles)
        rep = min(set(tiles), key=lambda t: (t[0] - mx) ** 2 + (t[1] - my) ** 2)
        k = min(len(set(tiles)), max(min_w, round(open_frac * len(set(tiles)))))
        band = sorted(set(tiles),
                      key=lambda t: (max(abs(t[0] - rep[0]), abs(t[1] - rep[1])), t))[:k]
        out.append((rep, frozenset(band)))
    if len(out) < 2:
        border = [t for t in ts if any((t[0] + dx, t[1] + dy) not in ts for dx, dy in NB4)]
        if border:
            mx = sum(x for x, _ in ts) / len(ts); my = sum(y for _, y in ts) / len(ts)
            a = max(border, key=lambda t: (t[0] - mx) ** 2 + (t[1] - my) ** 2)
            b = max(border, key=lambda t: (t[0] - a[0]) ** 2 + (t[1] - a[1]) ** 2)
            reps = {r for r, _band in out}
            for g in (a, b):
                if g not in reps:
                    band = frozenset(t for t in border
                                     if max(abs(t[0] - g[0]), abs(t[1] - g[1])) <= min_w // 2)
                    out.append((g, band | {g}))
    return out


POCKET_MAX_DIM = 16              # user's own definition: "a pocket is a zone of 16x16 or
                                 # less with only one entrance neck" — see find_pockets()
POCKET_MAX_TILES = 16           # tightened 2026-07-04: 16x16(=256) let pockets swallow most
                                 # of a small zone's reach, including whole zone-boundary
                                 # fronts; capping total tiles at 16 keeps a pocket a nook
POCKET_NOOK_BLOCKED = 4         # a ZoC tile counts as pocket interior when >=4 of its 8
                                 # neighbours are blocking (walled nook, not open ground)


def _bounded_fill(reach, exclude, start, max_dim, max_tiles):
    """BFS from `start` over `reach - exclude` (`exclude` is a SET of tiles treated as
    blocking). Returns the component as a frozenset if it stays within `max_tiles` cells
    and a `max_dim` x `max_dim` bounding box; returns None the moment it would exceed
    either bound — i.e. it leaked into the wider map rather than being sealed off, so it
    is NOT a pocket.

    8-connected (NB8): H3 heroes move diagonally, so a candidate neck that only blocks
    orthogonal movement is not a real single-entrance enclosure -- the hero just cuts the
    corner and the guard sits in the open next to nothing. Using NB4 here previously made
    ~66% of raw candidates false positives (a diagonal path around the "neck" reconnected
    to the wider map every time)."""
    comp = {start}
    minx = maxx = start[0]
    miny = maxy = start[1]
    q = collections.deque([start])
    while q:
        x, y = q.popleft()
        for dx, dy in NB8:
            m = (x + dx, y + dy)
            if m in exclude or m in comp or m not in reach:
                continue
            comp.add(m)
            minx, maxx = min(minx, m[0]), max(maxx, m[0])
            miny, maxy = min(miny, m[1]), max(maxy, m[1])
            if len(comp) > max_tiles or maxx - minx >= max_dim or maxy - miny >= max_dim:
                return None
            q.append(m)
    return frozenset(comp)


def mouth_key(reach, mouth, pocket):
    """Sort key ranking candidate mouths for the SAME physical nook, best (smallest)
    first. Preference order, derived from the user's drawings (the monster `O` sits at
    the pocket's natural opening):
      1. a mouth that is itself IN a neck (>=4 blocked neighbours — a corridor entrance)
         beats one standing a tile out in the open field, even when the open-field guard
         technically seals one extra tile via its ZoC;
      2. larger pocket (outermost mouth of a nested dead-end corridor);
      3. orthogonally adjacent to the pocket (guard facing the nook, not on a diagonal);
      4. more pocket tiles adjacent (better coverage), then plain tile order."""
    blocked = sum(1 for dx, dy in NB8 if (mouth[0] + dx, mouth[1] + dy) not in reach)
    orth = any(abs(mouth[0] - t[0]) + abs(mouth[1] - t[1]) == 1 for t in pocket)
    adj8 = sum(1 for t in pocket
               if max(abs(mouth[0] - t[0]), abs(mouth[1] - t[1])) == 1)
    return (0 if blocked >= POCKET_NOOK_BLOCKED else 1, -len(pocket),
            0 if orth else 1, -adj8, mouth)


def find_pockets(reach, max_dim=POCKET_MAX_DIM, max_tiles=POCKET_MAX_TILES):
    """Geometric pocket detection: small treasure nooks sealable by ONE guard.

    The neck is a guard's ZONE OF CONTROL, not a single tile (fixed 2026-07-05). H3
    wandering monsters threaten their own tile plus all 8 neighbours, so one guard seals
    everything a hero cannot reach — or stand on — without entering that 3x3 ZoC. The
    previous definition ("one walkable tile whose removal seals the pocket") could NEVER
    detect the most common nook of all, a 1-2 tile recess in a FLAT wall face: with
    diagonal movement such a nook has three entrance tiles (front + both diagonals), so
    no single tile seals it; it was only found when the flanking walls happened to
    protrude past the face. Tiles absent from `reach` — vegetation, other zones, or the
    true map edge — are all blocking alike.

    For every candidate guard tile `g`, its pocket is the union of:
      - bounded components of `reach - ZoC(g)` seeded next to the ZoC (the region BEHIND
        the guard — the old test generalized from a 1-tile to a 3x3 neck, which keeps
        dead-end corridors detected exactly as before), and
      - walkable ZoC tiles with >= POCKET_NOOK_BLOCKED blocked neighbours (the flat-face
        nook itself: it sits INSIDE the guard's ZoC, so grabbing its loot forces the
        fight even though a hero can path to it).

    Returns {mouth: frozenset(pocket_tiles)} where the mouth is the guard tile, deduped
    so each distinct pocket keeps a single canonical mouth (best `mouth_key`). Distinct-
    but-overlapping candidates for the same physical nook still come out as separate
    entries — `pp_pickup._dedupe_pockets` blob-merges those."""
    best = {}
    for g in sorted(reach):
        # a guard in fully open ground seals nothing and touches no nook: without a
        # blocking tile within Chebyshev distance 2 (the ZoC plus its rim), neither a
        # bounded component nor a >=4-blocked ZoC tile can exist. Cheap skip for the
        # bulk of any open field.
        if all((g[0] + dx, g[1] + dy) in reach
               for dx in range(-2, 3) for dy in range(-2, 3)):
            continue
        zoc = {g} | {(g[0] + dx, g[1] + dy) for dx, dy in NB8}
        pocket = set()
        seen = set()
        for z in sorted(zoc):
            for dx, dy in NB8:
                s = (z[0] + dx, z[1] + dy)
                if s in zoc or s in seen or s not in reach:
                    continue
                comp = _bounded_fill(reach, zoc, s, max_dim, max_tiles)
                if comp is None:        # leaked: open world, not sealed by this guard
                    seen.add(s)
                    continue
                seen |= comp
                pocket |= comp
        for z in sorted(zoc - {g}):
            if z not in reach:
                continue
            blocked = sum(1 for dx, dy in NB8 if (z[0] + dx, z[1] + dy) not in reach)
            if blocked >= POCKET_NOOK_BLOCKED:
                pocket.add(z)
        if not pocket or len(pocket) > max_tiles:
            continue
        if (max(x for x, _ in pocket) - min(x for x, _ in pocket) >= max_dim or
                max(y for _, y in pocket) - min(y for _, y in pocket) >= max_dim):
            continue
        comp = frozenset(pocket)
        key = mouth_key(reach, g, comp)
        if comp not in best or key < best[comp][0]:
            best[comp] = (key, g)
    return {mouth: comp for comp, (_k, mouth) in best.items()}


def _zone_gates(ts, zones, zid):
    """Passages (gates) through the rim belt -- the user's 'input and exit must correspond' rule.

    A blocked forest belt rings the zone, but a zone is not a sealed pocket: where it borders a
    DIFFERENT land zone there is a pass, and crucially an entry on one edge implies an exit on the
    far edge so the zone is TRAVERSABLE end-to-end (you can come in one side and leave the other).
    We return one representative tile per neighbouring zone (the centre of each contact segment).
    If the zone has fewer than two such neighbours (an isolated pocket), we synthesise an antipodal
    pair -- the two border tiles that are farthest apart -- so there is always a through-route. The
    spanning backbone then routes a corridor to every gate, punching the belt open exactly there."""
    contacts = _zone_fronts(ts, zones, zid)
    gates = []
    for o, tiles in contacts.items():
        mx = sum(t[0] for t in tiles) / len(tiles)
        my = sum(t[1] for t in tiles) / len(tiles)
        gates.append(min(set(tiles), key=lambda t: (t[0] - mx) ** 2 + (t[1] - my) ** 2))
    if len(gates) < 2:
        border = [t for t in ts if any((t[0] + dx, t[1] + dy) not in ts for dx, dy in NB4)]
        if border:
            # farthest-apart border pair: farthest from centroid -> a, then farthest from a -> b
            mx = sum(x for x, _ in ts) / len(ts); my = sum(y for _, y in ts) / len(ts)
            a = max(border, key=lambda t: (t[0] - mx) ** 2 + (t[1] - my) ** 2)
            b = max(border, key=lambda t: (t[0] - a[0]) ** 2 + (t[1] - a[1]) ** 2)
            for g in (a, b):
                if g not in gates:
                    gates.append(g)
    return gates


def generate_spanning(fm, zid, zones, model, seed=1, bias=0.0, spacing=SPACING,
                      edge_w=EDGE_W, edge_r=EDGE_R, gates=None):
    """Spanning-backbone + MRF thickening -- connected AND zone-spanning by construction.

    A local Gibbs field cannot express global reachability, and percolation growth gives a compact
    blob that leaves half the zone dead. So we first lay a SPANNING TREE of thin corridors that
    reaches farthest-point nodes covering the whole zone (every tile within ~`spacing` of an open
    corridor; the tree is connected and rooted at the centroid seed). Then one-shot frontier growth
    THICKENS the backbone with the learned run-length texture. Every open tile is 4-connected to the
    seed (backbone is connected; thickening only opens tiles adjacent to open ones), and no region is
    left blocked because the backbone already threads through all of them.

    `gates` (rim passages from `_zone_gates`) are added as backbone nodes too, so the spanning tree
    routes a corridor out to each one -- punching the forest belt open exactly there and making the
    zone traversable from one edge to the corresponding exit on another."""
    import random
    z = zones[zid]
    ts = set(z["tiles_set"])
    edist = edge_dist(ts)
    cx, cy = z["centroid"]
    cx, cy = int(round(cx)), int(round(cy))
    seedt = min(ts, key=lambda t: (t[0] - cx) ** 2 + (t[1] - cy) ** 2)
    rng = random.Random(seed ^ (zid * 2654435761 & 0xFFFFFFFF))

    def nbrs(t):
        x, y = t
        return [(x + dx, y + dy) for dx, dy in NB4 if (x + dx, y + dy) in ts]

    openm = {t: 0 for t in ts}
    openm[seedt] = 1

    # --- stage 1: spanning tree (Prim over nodes, edges = geodesic corridors) ---
    # keep interior nodes off the rim so the belt rings the zone, but ALSO route to the gates
    # (rim passages) so the belt is punched open there and the zone is traversable edge-to-edge
    if gates is None:
        gates = _zone_gates(ts, zones, zid)
    interior = [t for t in ts if edist.get(t, 0) >= edge_r] or list(ts)
    nodes = _farthest_points(ts, seedt, spacing, cand=interior)
    for g in gates:                                            # gates are mandatory backbone nodes
        if g in ts and g not in nodes:
            nodes.append(g)
            openm[g] = 1
    connected = [seedt]
    remaining = [n for n in nodes if n != seedt]
    while remaining:
        best_r = best_c = None
        bd = 1 << 60
        for r in remaining:
            for c in connected:
                d = (r[0] - c[0]) ** 2 + (r[1] - c[1]) ** 2
                if d < bd:
                    bd, best_r, best_c = d, r, c
        for t in _geodesic_path(best_c, best_r, ts):
            openm[t] = 1
        connected.append(best_r)
        remaining.remove(best_r)

    # --- stage 2: one-shot MRF thickening from the whole backbone ---
    decided = {t for t in ts if openm[t] == 1}
    frontier, infront = [], set()
    for t in list(decided):
        for n in nbrs(t):
            if openm[n] == 0 and n not in infront:
                frontier.append(n)
                infront.add(n)
    while frontier:
        i = rng.randrange(len(frontier))
        t = frontier[i]
        frontier[i] = frontier[-1]; frontier.pop()
        infront.discard(t)
        if t in decided:
            continue
        decided.add(t)
        p = p_open_sym(t[0], t[1], openm, edist, model, bias, edge_w, edge_r)
        if rng.random() < p:
            openm[t] = 1
            for n in nbrs(t):
                if openm[n] == 0 and n not in decided and n not in infront:
                    frontier.append(n); infront.add(n)
    return ts, {t for t, v in openm.items() if v == 1}, seedt


def generate_budgeted(fm, zid, zones, model, target=None, seed=1, iters=14,
                      lo=-0.40, hi=0.40, spacing=SPACING, edge_w=EDGE_W, edge_r=EDGE_R):
    """Generate (spanning backbone + MRF thickening), auto-tuning global `bias` to hit `target`.

    Thickening size is monotone in bias, so bisection lands the budget robustly across zones/terrains
    without hand-tuning. `target` defaults to the learned budget. The field is always a single open
    component containing the centroid seed and spanning the whole zone, with a blocked rim belt."""
    if target is None:
        target = model["budget"]
    gates = _zone_gates(set(zones[zid]["tiles_set"]), zones, zid)   # compute once, reuse each bisection step
    best = None
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        ts, O, seedt = generate_spanning(fm, zid, zones, model, seed=seed, bias=mid,
                                         spacing=spacing, edge_w=edge_w, edge_r=edge_r, gates=gates)
        frac = len(O) / max(len(ts), 1)
        best = (ts, O, seedt, mid, frac)
        if frac < target:          # too blocked -> need more open -> raise bias
            lo = mid
        else:
            hi = mid
    return best


def reach_from_seed(O, seedt):
    """Tiles in O that are 4-connected to seedt (BFS). Returns the reachable set."""
    import collections as C
    if seedt not in O:
        return set()
    seen = {seedt}
    q = C.deque([seedt])
    while q:
        x, y = q.popleft()
        for dx, dy in NB4:
            n = (x + dx, y + dy)
            if n in O and n not in seen:
                seen.add(n)
                q.append(n)
    return seen


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


def render_ascii(ts, O, seedt=None):
    """Text grid of the zone: O=open, B=blocked, N=outside the zone (S=clamped centroid seed)."""
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lines = []
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            if (x, y) not in ts:
                row.append("N")
            elif seedt is not None and (x, y) == seedt:
                row.append("S")
            elif (x, y) in O:
                row.append("O")
            else:
                row.append("B")
        lines.append("".join(row))
    return "\n".join(lines)


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


def render_markov_field(seed=3, W=72, H=72, scale=9, out=None, min_area=60,
                        spacing=SPACING, edge_w=EDGE_W):
    """Generate a Markov terrain, segment it into zones, run the MRF open/block field per zone, and
    paint the field over the terrain colours: blocked tiles -> dark forest/obstacle tint, open tiles
    -> bright passable ground, the clamped centroid seed -> blue. Water/rock and tiny zones are left
    as plain terrain. Returns the output path."""
    import random
    from PIL import Image
    import markov_terrain as MT
    import render as RND

    rng = random.Random(seed)
    M, M4 = MT.learn(0), MT.learn4(0)
    grid = MT.generate(M, W, H, rng)
    MT.gibbs(grid, M4, M["marg"], rng, sweeps=6)               # isotropic smoothing -> coherent patches
    lvl = [[{"t": t, "river": False, "road": False} for t in row] for row in grid]
    zones, zl, canon = ZE._segment_level(lvl)

    # terrain background
    img = RND.render_level(lvl, [], W, H)
    px = img.load()
    model_cache = {}
    nfields = 0
    for zid, z in zones.items():
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < min_area:
            continue                                           # leave sea / void / scraps as terrain
        if terrain not in model_cache:
            model_cache[terrain] = learn(terrain)
        model = model_cache[terrain]
        ts, O, seedt, bias, frac = generate_budgeted(None, zid, zones, model, seed=seed,
                                                      spacing=spacing, edge_w=edge_w)
        nfields += 1
        def _blend(a, b, t):                                   # a*(1-t)+b*t per channel
            return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))
        for (x, y) in ts:
            base = px[x * RND.TILE, y * RND.TILE]
            if (x, y) == seedt:
                col = (40, 120, 240)
            elif (x, y) in O:                                  # open: passable -> lighten toward sand
                col = _blend(base, (235, 232, 215), 0.32)
            else:                                              # blocked: obstacle -> darken toward forest
                col = _blend(base, (24, 46, 20), 0.62)
            for dy in range(RND.TILE):
                for dx in range(RND.TILE):
                    px[x * RND.TILE + dx, y * RND.TILE + dy] = col
    if out is None:
        out = os.path.join(ZE.ROOT, "out", "render", "field", f"markov_field_s{seed}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print(f"markov terrain {W}x{H}, {len(zones)} zones, {nfields} open/block fields painted -> {out}")
    return out


# terrain id -> (2-char tile code, full-ground view pool). The views are the corpus-observed
# "interior" (all-neighbours-same-terrain) tile variants, so the ground reads as clean texture
# rather than transition/edge tiles. See render_editor.TERR_DEF for the .def behind each code.
_TERR_VIEWS = {
    0: ("dt", [21, 22, 23, 24, 26, 27]),   1: ("sa", [1, 2, 3, 4, 6, 7]),
    2: ("gr", [49, 50, 51, 52, 55, 56]),   3: ("sn", [49, 50, 52, 53, 54, 55]),
    4: ("sw", [49, 50, 51, 53, 54, 56]),   5: ("rg", [49, 51, 52, 53, 55, 56]),
    6: ("sb", [49, 50, 51, 52, 55, 56]),   7: ("lv", [49, 52, 53, 54, 55, 56]),
    8: ("wt", [22, 23, 24, 28, 29, 30]),   9: ("rc", [0, 1, 2, 3]),
}


def render_markov_sprites(seed=3, W=72, H=72, out=None, min_area=60,
                          spacing=SPACING, edge_w=EDGE_W):
    """Same Markov terrain + per-zone open/blocked field as `render_markov_field`, but rendered with
    REAL VCMI sprites: every blocked tile is planted with a single-tile blocking DECORATION (trees,
    rocks, ...) drawn from the ontology's `decor_pool` for that terrain, and the whole thing is
    composited by `render_editor` over real 32x32 terrain tiles. Open tiles stay bare ground -> the
    navigable web reads as walkable land threading through real vegetation. The ontology is the sole
    source of object identity; we only choose WHERE (blocked tiles) and pick among its native pool."""
    import random
    import collections as _C
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import markov_terrain as MT
    import ontology as ON
    import obj_resolve as _OR
    import render_editor as RED

    name2id = {v: k for k, v in ZE.TNAME.items()}

    def _corpus_weights(terr_id):
        """anim -> corpus occurrence count for DECORATION on this terrain (a spatial statistic; the
        identity itself still comes from the ontology pool). Trees dominate; oddities (cracks, logs)
        are rare -> weighting the draw reproduces the corpus mix instead of a flat, garish scatter."""
        w = _C.Counter()
        for e in _OR._OBJLIB.get("DECORATION", {}).get(str(terr_id), []):
            w[e["animation"].lower()] += e.get("weight", 1)
        return w

    def _fit(ident, ax, ay, ts, O, covered):
        """Blocking footprint cells of `ident` anchored (bottom-right) at (ax,ay), or None if any
        would land off-zone, on an OPEN tile (would block a path), or on an already-covered tile."""
        blk = [(cx, cy) for cx, cy, b in _OR.mask_cells(ident["mask"], ax, ay) if b]
        for c in blk:
            if c not in ts or c in O or c in covered:
                return None
        return blk

    rng = random.Random(seed)
    M, M4 = MT.learn(0), MT.learn4(0)
    grid = MT.generate(M, W, H, rng)
    MT.gibbs(grid, M4, M["marg"], rng, sweeps=6)
    lvl = [[{"t": t, "river": False, "road": False} for t in row] for row in grid]
    zones, zl, canon = ZE._segment_level(lvl)

    # surface tile strings (clean full-ground view per terrain, picked deterministically per tile)
    surf = []
    for y in range(H):
        row = []
        for x in range(W):
            t = grid[y][x]
            code, views = _TERR_VIEWS.get(t, ("gr", [49]))
            v = views[(x * 73856093 ^ y * 19349663 ^ seed) % len(views)]
            row.append(f"{code}{v}_")
        surf.append(row)

    # plant corpus-weighted blocking vegetation packed into the blocked tiles of every field zone
    model_cache, pool_cache, objs = {}, {}, []
    nfields = 0
    ex = ZE.EXCLUDE_DECOR_TYPES                                  # drop water-delta / lake / reef tiles
    for zid, z in zones.items():
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < min_area:
            continue
        if terrain not in model_cache:
            model_cache[terrain] = learn(terrain)
            full = ON.decor_pool(terrain, blocking=True, exclude_types=ex) \
                or ON.decor_pool("land", blocking=True, exclude_types=ex)
            cw = _corpus_weights(name2id.get(terrain, -1))
            # Bucket the pool by footprint size, LARGEST first. The placer packs greedily big->small,
            # so a thick blocked mass gets a 15-tile mountain (a real range) and a 6/9-tile tree cluster
            # (a forest), while the thin 1-wide walls fall through to single trees -- which is exactly how
            # the corpus arranges obstacles (contiguous mountain ridges + forest clumps, not a tree-scatter).
            by_size = _C.defaultdict(list)
            for i in full:
                by_size[ON.footprint_size(i["animation"])].append(i)
            buckets = []
            for s in sorted(by_size, reverse=True):
                idents = by_size[s]
                bw = [cw.get(i["animation"].lower(), 0) + 0.3 for i in idents]   # 0.3 base keeps native-but-rare
                buckets.append((idents, bw))
            singles = by_size.get(1) or full
            swts = [cw.get(i["animation"].lower(), 0) + 0.3 for i in singles]
            pool_cache[terrain] = (buckets, singles, swts)
        buckets, singles, swts = pool_cache[terrain]
        ts, O, seedt, bias, frac = generate_budgeted(None, zid, zones, model_cache[terrain],
                                                     seed=seed, spacing=spacing, edge_w=edge_w)
        nfields += 1
        if not buckets:
            continue
        covered = set()
        # REVERSE raster: the mask anchor is the footprint's bottom-right tile, so its cells extend UP and
        # LEFT (smaller x,y). Visiting bottom-right -> top-left means those cells are still free when a big
        # object claims them, so multi-tile footprints actually fit instead of colliding with what we packed.
        for (x, y) in sorted(ts, reverse=True):
            if (x, y) in O or (x, y) in covered:
                continue                                       # open tile (bare path) or already wooded
            rr = random.Random((x * 2654435761 ^ y * 40503 ^ seed) & 0xFFFFFFFF)
            placed = None
            for idents, bw in buckets:                          # largest footprint first -> ranges & forests
                ident = rr.choices(idents, weights=bw, k=1)[0]
                blk = _fit(ident, x, y, ts, O, covered)
                if blk is not None:
                    placed = (ident, blk); break
            if placed is None:                                  # fall back to a guaranteed-fitting single tile
                ident = rr.choices(singles, weights=swts, k=1)[0]
                placed = (ident, [(x, y)])
            ident, blk = placed
            covered.update(blk)
            objs.append({"x": x, "y": y, "l": 0,
                         "template": {"animation": ident["animation"], "mask": ident["mask"]}})

    img = RED.render_map(surf, objs, title=f"markov+veg s{seed}")
    if out is None:
        out = os.path.join(ZE.ROOT, "out", "render", "field", f"markov_sprites_s{seed}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print(f"markov terrain {W}x{H}, {nfields} fields, {len(objs)} vegetation sprites -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="All for One")
    ap.add_argument("--zone", type=int, default=11)
    ap.add_argument("--markov-png", action="store_true",
                    help="generate a Markov terrain and paint per-zone MRF open/block fields over it")
    ap.add_argument("--markov-sprites", action="store_true",
                    help="like --markov-png but render real VCMI sprites: vegetation on blocked tiles")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--bias", type=float, default=None,
                    help="fixed add to P(open); omit to auto-tune bias to the learned budget")
    ap.add_argument("--target", type=float, default=None,
                    help="target open fraction for auto-bias (default: learned budget)")
    ap.add_argument("--ascii", action="store_true", help="print O/B/N text grid of the generated field")
    ap.add_argument("--spacing", type=int, default=SPACING,
                    help="backbone node spacing (bigger -> fewer, fatter corridors)")
    ap.add_argument("--edge-w", type=float, default=EDGE_W, dest="edge_w",
                    help="rim-block strength (higher -> thicker blocked forest belt at the zone edge)")
    args = ap.parse_args()

    if args.markov_sprites:
        render_markov_sprites(seed=args.seed, spacing=args.spacing, edge_w=args.edge_w)
        return
    if args.markov_png:
        render_markov_field(seed=args.seed, spacing=args.spacing, edge_w=args.edge_w)
        return

    fm = OR.load_faithful(args.map)
    lvl = fm["terrain"][0]
    zones, zl, canon = ZE._segment_level(lvl)
    z = zones[args.zone]
    terrain = ZE.TNAME.get(z["terrain_type"])
    ts_real = set(z["tiles_set"])
    O_real = SK.open_set(fm, ts_real)

    model = learn(terrain)
    print(f"learned {terrain}: zones={model['nzones']} budget(open frac)={model['budget']:.2f}")
    if args.bias is None:
        ts, O_gen, seedt, chosen_bias, _ = generate_budgeted(
            fm, args.zone, zones, model, target=args.target, spacing=args.spacing, edge_w=args.edge_w)
        print(f"auto-bias -> {chosen_bias:+.3f} (target open frac {args.target or model['budget']:.2f})"
              f"  spacing={args.spacing} edge_w={args.edge_w}")
    else:
        ts, O_gen, seedt = generate_spanning(fm, args.zone, zones, model, bias=args.bias,
                                             spacing=args.spacing, edge_w=args.edge_w)

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

    reach = reach_from_seed(O_gen, seedt)
    print(f"open frac: real={len(O_real)/len(ts_real):.2f}  gen={len(O_gen)/len(ts):.2f}")
    print(f"connectivity: {len(reach)}/{len(O_gen)} open tiles reachable from seed "
          f"({'OK single component' if len(reach) == len(O_gen) else 'BROKEN'})")
    hr, hg = run_lengths(ts_real, O_real), run_lengths(ts, O_gen)
    sr, sg = sum(hr.values()), sum(hg.values())
    print("open-run-length  k:  real%   gen%")
    for k in range(1, 8):
        print(f"   {k}: {100*hr.get(k,0)/max(sr,1):5.1f}  {100*hg.get(k,0)/max(sg,1):5.1f}")
    print(f"-> {rdir}/{base}.png  (real | gen, white=open red=blocked, blue=seed)")
    if args.ascii:
        print(f"\n=== {args.map} z{args.zone} ({terrain}) generated field  O=open B=blocked N=outside S=seed ===")
        print(render_ascii(ts, O_gen, seedt=seedt))


if __name__ == "__main__":
    main()
