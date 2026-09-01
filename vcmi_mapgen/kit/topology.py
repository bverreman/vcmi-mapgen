"""Zone-shape planning: entrance/gate/front geometry and pocket (sealable-nook) detection.
Shared by the gameplay, pickup, repair and vegetation steps' entrance/backbone/cache logic."""
import collections

from vcmi_mapgen.kit.geometry import NB8

NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]

SPACING = 6    # farthest-point node spacing for the spanning backbone (bigger -> fewer, fatter corridors)
ENTRANCE_W = 3       # entrance band width in front tiles per side (hero + guard fit through)
LONG_FRONT = 20      # a zone-pair front at least this long earns a second entrance
MAX_ENTRANCES = 2    # "a few" — hard cap on planned crossings per zone pair
MIN_ENTRANCE_SEP = 12  # Chebyshev floor between two entrances of the same pair

POCKET_MAX_DIM = 16              # user's own definition: "a pocket is a zone of 16x16 or
                                 # less with only one entrance neck" — see find_pockets()
POCKET_MAX_TILES = 16           # tightened 2026-07-04: 16x16(=256) let pockets swallow most
                                 # of a small zone's reach, including whole zone-boundary
                                 # fronts; capping total tiles at 16 keeps a pocket a nook
POCKET_NOOK_BLOCKED = 4         # a ZoC tile counts as pocket interior when >=4 of its 8
                                 # neighbours are blocking (walled nook, not open ground)


def _geodesic_path(a, b, ts):
    """Shortest 4-connected path a->b staying inside the zone `ts`; [] if unreachable."""
    prev = {a: None}
    q = collections.deque([a])
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


def plan_entrances(zones, entrance_w=ENTRANCE_W, long_front=LONG_FRONT,
                   max_entrances=MAX_ENTRANCES, min_sep=MIN_ENTRANCE_SEP):
    """Map-level entrance plan: unlike `_zone_gate_bands` (per-zone, corpus-wide OPEN borders),
    this keeps zones ISOLATED — each adjacent land-zone pair gets only 1..`max_entrances`
    narrow aligned crossings and the rest of the border is left to the vegetation sampler's
    border densification (pp_sample). Computed ONCE per level over ALL zones so both sides of
    a pair agree on where the crossing is:

      - entrance 1 sits at the front's centroid-nearest tile (the same rep math as
        `_zone_gate_bands`), its far-side rep at the closest opposite-front tile;
      - a 2nd entrance only when the front is at least `long_front` tiles AND its rep (the
        front tile farthest from entrance 1) is >= `min_sep` Chebyshev away — long borders
        read badly with a single hole, short ones must stay single-entry;
      - each side's band = the `entrance_w` front tiles nearest its rep (protected from
        vegetation, so the crossing is guaranteed at least that wide).

    Returns {zid: [(rep, frozenset(band), other_zid), ...]} — the (rep, band) pairs are
    drop-in for every `_zone_gate_bands` consumer. Pure geometry, rng-free, deterministic
    (all argmin/argmax tie-break on the tile tuple)."""
    owner = {}
    for zz, z in zones.items():
        for t in z["tiles_set"]:
            owner[t] = zz
    fronts = collections.defaultdict(set)            # ordered pair (a, b) -> a-side tiles
    for t, zz in owner.items():
        for dx, dy in NB4:
            o = owner.get((t[0] + dx, t[1] + dy))
            if o is not None and o != zz:
                fronts[(zz, o)].add(t)

    out = {zid: [] for zid in zones}
    for (a, b) in sorted(fronts):
        if a >= b:
            continue                                 # each unordered pair planned once
        Ta = sorted(fronts[(a, b)])
        Tb = sorted(fronts.get((b, a), ()))
        if not Ta or not Tb:
            continue
        mx = sum(t[0] for t in Ta) / len(Ta)
        my = sum(t[1] for t in Ta) / len(Ta)
        rep_a = min(Ta, key=lambda t: ((t[0] - mx) ** 2 + (t[1] - my) ** 2, t))
        rep_b = min(Tb, key=lambda t: ((t[0] - rep_a[0]) ** 2 + (t[1] - rep_a[1]) ** 2, t))
        reps = [(rep_a, rep_b)]
        if len(Ta) >= long_front and max_entrances >= 2:
            rep_a2 = max(Ta, key=lambda t: (max(abs(t[0] - rep_a[0]),
                                                abs(t[1] - rep_a[1])), t))
            if max(abs(rep_a2[0] - rep_a[0]), abs(rep_a2[1] - rep_a[1])) >= min_sep:
                rep_b2 = min(Tb, key=lambda t: ((t[0] - rep_a2[0]) ** 2
                                                + (t[1] - rep_a2[1]) ** 2, t))
                reps.append((rep_a2, rep_b2))
        for ra, rb in reps[:max_entrances]:
            band_a = frozenset(sorted(
                Ta, key=lambda t: (max(abs(t[0] - ra[0]), abs(t[1] - ra[1])), t))[:entrance_w])
            band_b = frozenset(sorted(
                Tb, key=lambda t: (max(abs(t[0] - rb[0]), abs(t[1] - rb[1])), t))[:entrance_w])
            out[a].append((ra, band_a, b))
            out[b].append((rb, band_b, a))
    return out


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

    Returns {guard_tile: (frozenset(pocket_tiles), frozenset(mouth_tiles))} where the
    guard_tile is the canonical ZoC-centre that seals the pocket, mouth_tiles is the
    ≤2-tile entry cluster (ZoC-interior nook tiles ∪ first outside-ZoC layer adjacent
    to the ZoC), and the dict is deduped so each distinct pocket keeps a single
    canonical entry (best `mouth_key`). Distinct-but-overlapping candidates for the
    same physical nook still come out as separate entries — `steps.repair.caches.
    _dedupe_pockets` blob-merges those."""
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
        # Mouth size: 8-connected cluster of pocket tiles at the entry point.
        # Two layers: ZoC-interior nook tiles (≥4 blocked neighbours, already in pocket)
        # and the first outside-ZoC layer adjacent to the ZoC. Together they form the
        # physical opening. Zone-boundary entrances are typically 3+ tiles wide here;
        # genuine vegetation nooks/corridors are 1-2.
        mouth = ((pocket & zoc) |
                 {p for p in pocket if p not in zoc
                  and any((p[0] + dx, p[1] + dy) in zoc for dx, dy in NB8)})
        if len(mouth) > 2:
            continue
        comp = frozenset(pocket)
        key = mouth_key(reach, g, comp)
        if comp not in best or key < best[comp][0]:
            best[comp] = (key, g, frozenset(mouth))
    return {g: (comp, mouth_fs) for comp, (_k, g, mouth_fs) in best.items()}


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
