"""Patch-based reconstruction: the 8x8 ZONE is the unit, not the object.

The per-object model failed because object placement is a global decision a local
window can't infer, and EMPTY (84% of cells) swamps any per-cell argmax. This model
sidesteps both: it lifts whole 8x8 patches from real maps (each carries its objects
already arranged correctly) and only decides WHICH patch goes WHERE. The adjacency
tool is repurposed to patch<->patch seams.

Because we know the target's exact terrain, every patch slot has a known 8x8 terrain
block, so "which patch fits here" reduces to: retrieve the corpus patch whose terrain
best matches this block and stamp its object arrangement; patch-seam object adjacency
breaks ties between equally-good terrain matches.

  Phase A (self)   : patch inventory = the target's own patches -> jigsaw reconstruction
  Phase B (corpus) : inventory = all OTHER maps' patches        -> generalization
"""

import sys, os, json, glob, collections, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tilegrid
from tilegrid import EMPTY

P = 8  # patch edge (72 / 8 = 9 patches per axis, exact)


def _patches_of(grid):
    """Cut a tokenized grid into 8x8 patches. Returns list of patch dicts with the
    terrain block (flat tuple), the object subgrid, and border terrain tuples."""
    out = []
    H, W, L = grid["H"], grid["W"], grid["levels"]
    GI, GJ = H // P, W // P
    for l in range(L):
        terr, obj = grid["terrain"][l], grid["obj"][l]
        for I in range(GI):
            for J in range(GJ):
                tb = [terr[I * P + dy][J * P + dx] for dy in range(P) for dx in range(P)]
                ob = [[obj[I * P + dy][J * P + dx] for dx in range(P)] for dy in range(P)]
                out.append({
                    "terr": tuple(tb),
                    "obj": ob,
                    "top": tuple(tb[0:P]),                       # top row terrain
                    "bot": tuple(tb[(P - 1) * P:]),              # bottom row terrain
                    "left": tuple(tb[dy * P] for dy in range(P)),
                    "right": tuple(tb[dy * P + P - 1] for dy in range(P)),
                    "n_obj": sum(1 for r in ob for t in r if t != EMPTY),
                })
    return out


def build_inventory(map_names):
    """All patches from the given maps, plus a retrieval index bucketed by
    (top-row, left-col) terrain so neighbours align, and patch-seam adjacency counts."""
    patches = []
    bucket = collections.defaultdict(list)          # (top,left) -> [patch idx]
    by_dom = collections.defaultdict(list)           # dominant terrain -> [patch idx]
    h_adj = collections.Counter()                    # (left.right, right.left) seam freq
    v_adj = collections.Counter()                    # (up.bot, down.top) seam freq
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        g = tilegrid.tokenize(m)
        ps = _patches_of(g)
        GI = g["H"] // P; GJ = g["W"] // P; per_level = GI * GJ
        base = len(patches)
        for k, p in enumerate(ps):
            idx = base + k
            patches.append(p)
            bucket[(p["top"], p["left"])].append(idx)
            dom = collections.Counter(p["terr"]).most_common(1)[0][0]
            by_dom[dom].append(idx)
        # seam adjacency within each level grid
        for off in range(0, len(ps), per_level):
            for I in range(GI):
                for J in range(GJ):
                    cur = ps[off + I * GJ + J]
                    if J + 1 < GJ:
                        rt = ps[off + I * GJ + J + 1]
                        h_adj[(cur["right"], rt["left"])] += 1
                    if I + 1 < GI:
                        dn = ps[off + (I + 1) * GJ + J]
                        v_adj[(cur["bot"], dn["top"])] += 1
    return {"patches": patches, "bucket": bucket, "by_dom": by_dom,
            "h_adj": h_adj, "v_adj": v_adj}


def _hamming(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


def _candidates(inv, want):
    """Patch indices to consider for a slot whose terrain block is `want`."""
    top = want[0:P]; left = tuple(want[dy * P] for dy in range(P))
    cands = inv["bucket"].get((top, left))
    if cands:
        return cands
    dom = collections.Counter(want).most_common(1)[0][0]
    return inv["by_dom"].get(dom) or range(len(inv["patches"]))


def synthesize(target_grid, inv, seed=0, use_adj=True):
    """Place a patch in every slot of the target's coarse grid, conditioned on the
    target's exact terrain. Returns an object-token grid (same shape as target)."""
    rng = random.Random(seed)
    H, W, L = target_grid["H"], target_grid["W"], target_grid["levels"]
    GI, GJ = H // P, W // P
    out = [[[EMPTY for _ in range(W)] for _ in range(H)] for _ in range(L)]
    chosen = {}  # (l,I,J) -> patch dict
    for l in range(L):
        terr = target_grid["terrain"][l]
        for I in range(GI):
            for J in range(GJ):
                want = tuple(terr[I * P + dy][J * P + dx] for dy in range(P) for dx in range(P))
                cands = _candidates(inv, want)
                left_p = chosen.get((l, I, J - 1))
                up_p = chosen.get((l, I - 1, J))
                best = None; best_key = None
                for idx in cands:
                    p = inv["patches"][idx]
                    th = _hamming(p["terr"], want)            # terrain mismatch (primary)
                    adj = 0
                    if use_adj and left_p is not None:
                        adj += inv["h_adj"].get((left_p["right"], p["left"]), 0)
                    if use_adj and up_p is not None:
                        adj += inv["v_adj"].get((up_p["bot"], p["top"]), 0)
                    # primary: smallest terrain hamming; tie-break: higher seam adjacency
                    key = (th, -adj)
                    if best_key is None or key < best_key:
                        best_key, best = key, p
                chosen[(l, I, J)] = best
                if best is None:
                    continue
                for dy in range(P):
                    for dx in range(P):
                        tok = best["obj"][dy][dx]
                        if tok != EMPTY:
                            out[l][I * P + dy][J * P + dx] = tok
    return out


def _oedges(p):
    """Object-edge tuples of a patch: (top-row, bot-row, left-col, right-col)."""
    ob = p["obj"]
    top = tuple(ob[0]); bot = tuple(ob[P - 1])
    left = tuple(ob[r][0] for r in range(P)); right = tuple(ob[r][P - 1] for r in range(P))
    return top, bot, left, right


def build_inventory_seam(map_names):
    """Like build_inventory, but also records OBJECT-edge adjacencies: which object
    edge sat to the right of / below which, in real maps. Patches keep their object
    edges so placement can be conditioned on already-placed neighbours' object seams."""
    patches = []
    bucket = collections.defaultdict(list)
    by_dom = collections.defaultdict(list)
    h_objadj = collections.Counter()   # (left.oright, right.oleft) -> freq
    v_objadj = collections.Counter()   # (up.obot,  down.otop) -> freq
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        g = tilegrid.tokenize(m)
        ps = _patches_of(g)
        GI = g["H"] // P; GJ = g["W"] // P; per_level = GI * GJ
        base = len(patches)
        for k, p in enumerate(ps):
            top, bot, left, right = _oedges(p)
            p = dict(p, otop=top, obot=bot, oleft=left, oright=right)
            ps[k] = p
            idx = base + k
            patches.append(p)
            bucket[(p["top"], p["left"])].append(idx)
            dom = collections.Counter(p["terr"]).most_common(1)[0][0]
            by_dom[dom].append(idx)
        for off in range(0, len(ps), per_level):
            for I in range(GI):
                for J in range(GJ):
                    cur = ps[off + I * GJ + J]
                    if J + 1 < GJ:
                        rt = ps[off + I * GJ + J + 1]
                        h_objadj[(cur["oright"], rt["oleft"])] += 1
                    if I + 1 < GI:
                        dn = ps[off + (I + 1) * GJ + J]
                        v_objadj[(cur["obot"], dn["otop"])] += 1
    return {"patches": patches, "bucket": bucket, "by_dom": by_dom,
            "h_objadj": h_objadj, "v_objadj": v_objadj}


def synthesize_seam(target_grid, inv, seed=0, topk=1):
    """Place patches in raster order conditioned on the target's exact terrain AND on
    the object-seams of already-placed left/up neighbours.

    Selection per slot:
      1. restrict to candidates with the SMALLEST terrain hamming to the slot,
      2. score each by object-seam compatibility with the placed left/up patches
         (how often that object-edge pairing occurred in real maps),
      3. seed==0 (topk==1): take the most-compatible candidate -> reconstruction;
         seed>0: sample among the top-`topk` compatible candidates -> variation.
    """
    rng = random.Random(seed)
    H, W, L = target_grid["H"], target_grid["W"], target_grid["levels"]
    GI, GJ = H // P, W // P
    out = [[[EMPTY for _ in range(W)] for _ in range(H)] for _ in range(L)]
    placed = {}  # (l,I,J) -> patch dict
    for l in range(L):
        terr = target_grid["terrain"][l]
        for I in range(GI):
            for J in range(GJ):
                want = tuple(terr[I * P + dy][J * P + dx] for dy in range(P) for dx in range(P))
                cands = _candidates(inv, want)
                # 1. minimal terrain mismatch
                scored = [(_hamming(inv["patches"][i]["terr"], want), i) for i in cands]
                th_min = min(s[0] for s in scored)
                pool = [i for (th, i) in scored if th == th_min]
                left_p = placed.get((l, I, J - 1))
                up_p = placed.get((l, I - 1, J))
                # 2. object-seam compatibility score
                def seam(i):
                    p = inv["patches"][i]
                    s = 0
                    if left_p is not None:
                        s += inv["h_objadj"].get((left_p["oright"], p["oleft"]), 0)
                    if up_p is not None:
                        s += inv["v_objadj"].get((up_p["obot"], p["otop"]), 0)
                    return s
                pool.sort(key=lambda i: -seam(i))
                # 3. choose
                if topk <= 1:
                    chosen_i = pool[0]
                else:
                    best = seam(pool[0])
                    near = [i for i in pool if seam(i) >= best] or pool
                    near = (pool[:topk] if len(near) < 2 else near)
                    chosen_i = rng.choice(near[:topk])
                p = inv["patches"][chosen_i]
                placed[(l, I, J)] = p
                for dy in range(P):
                    for dx in range(P):
                        tok = p["obj"][dy][dx]
                        if tok != EMPTY:
                            out[l][I * P + dy][J * P + dx] = tok
    return out


def _seam_score(inv, p, nb):
    """Object-seam compatibility of candidate patch p against placed neighbours nb
    (dict with optional 'left','up','right','down' patches)."""
    s = 0
    if nb.get("left") is not None:
        s += inv["h_objadj"].get((nb["left"]["oright"], p["oleft"]), 0)
    if nb.get("up") is not None:
        s += inv["v_objadj"].get((nb["up"]["obot"], p["otop"]), 0)
    if nb.get("right") is not None:
        s += inv["h_objadj"].get((p["oright"], nb["right"]["oleft"]), 0)
    if nb.get("down") is not None:
        s += inv["v_objadj"].get((p["obot"], nb["down"]["otop"]), 0)
    return s


def synthesize_seam_iter(target_grid, inv, seed=0, topk=1, iters=4):
    """Seam-aware placement refined to a fixpoint over all four neighbours.

    Pass 0 is the raster left/up placement (synthesize_seam). Subsequent passes
    re-pick every slot using its now-placed left/up/right/down neighbours, so a real
    configuration -- mutually consistent on every seam -- is a stable fixpoint.
    seed==0/topk==1 => deterministic reconstruction; topk>1 => seeded variation.
    """
    rng = random.Random(seed)
    H, W, L = target_grid["H"], target_grid["W"], target_grid["levels"]
    GI, GJ = H // P, W // P
    # precompute per-slot candidate pool (min terrain hamming) once
    pools = {}
    wants = {}
    for l in range(L):
        terr = target_grid["terrain"][l]
        for I in range(GI):
            for J in range(GJ):
                want = tuple(terr[I * P + dy][J * P + dx] for dy in range(P) for dx in range(P))
                wants[(l, I, J)] = want
                scored = [(_hamming(inv["patches"][i]["terr"], want), i) for i in _candidates(inv, want)]
                th_min = min(s[0] for s in scored)
                pools[(l, I, J)] = [i for (th, i) in scored if th == th_min]
    placed = {}
    for it in range(iters):
        changed = 0
        for l in range(L):
            for I in range(GI):
                for J in range(GJ):
                    pool = pools[(l, I, J)]
                    nb = {"left": placed.get((l, I, J - 1)), "up": placed.get((l, I - 1, J)),
                          "right": placed.get((l, I, J + 1)), "down": placed.get((l, I + 1, J))}
                    ranked = sorted(pool, key=lambda i: -_seam_score(inv, inv["patches"][i], nb))
                    if topk <= 1:
                        chosen_i = ranked[0]
                    else:
                        best = _seam_score(inv, inv["patches"][ranked[0]], nb)
                        near = [i for i in ranked if _seam_score(inv, inv["patches"][i], nb) >= best]
                        chosen_i = rng.choice((near or ranked)[:topk])
                    prev = placed.get((l, I, J))
                    if prev is None or prev is not inv["patches"][chosen_i]:
                        changed += 1
                    placed[(l, I, J)] = inv["patches"][chosen_i]
        if changed == 0:
            break
    out = [[[EMPTY for _ in range(W)] for _ in range(H)] for _ in range(L)]
    for (l, I, J), p in placed.items():
        for dy in range(P):
            for dx in range(P):
                tok = p["obj"][dy][dx]
                if tok != EMPTY:
                    out[l][I * P + dy][J * P + dx] = tok
    return out


def all_map_names():
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(f"{ROOT}/out/maps/*.json"))
