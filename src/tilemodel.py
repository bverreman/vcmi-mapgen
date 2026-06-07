"""Autoregressive object-channel tile model (the empty-tile-as-token redesign).

Replaces the object->object adjacency graph (deps_adj.py), which had no notion of
empty tiles, no memory of what was already placed, and worked in relative-position
space -- so it could never target an ABSOLUTE layout.

Here every cell emits a token from {EMPTY} u {purposes}, in raster order, conditioned
on (the user's three asks):
  1. terrain at the cell                         -- "empty tiles / terrain as context"
  2. already-placed object tokens nearby (causal) -- "the past selection in the graph"
  3. (folded into 2 via the neighbourhood window)  -- "objects around"

Learned by counting over the corpus with Katz-style backoff, exactly like
markov_terrain.py does for terrain. Decoding modes:
  - free      : own predictions feed the context (true generation)
  - teacher   : real neighbours feed the context (next-token-accuracy upper bound)
  - inpaint   : a known-cell mask is given; unknown cells are filled in raster order
                using known+predicted context (Phase B held-out reconstruction)
"""

import sys, os, json, glob, collections, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tilegrid
from tilegrid import EMPTY


# Causal neighbourhood offsets (raster order: only cells already decoded).
#   L=left, U=up, UL=up-left, UR=up-right, LL=2-left, UU=2-up
_CTX = [(-1, 0), (0, -1), (-1, -1), (1, -1), (-2, 0), (0, -2)]


def _obj_at(grid2d, y, x, H, W):
    """grid2d is a single level's object grid [y][x]."""
    if 0 <= y < H and 0 <= x < W:
        return grid2d[y][x]
    return "#"  # off-map sentinel (distinct from EMPTY)


def _contexts(terr_here, neigh):
    """Backoff key ladder, most specific -> least. neigh is the tuple of causal
    object tokens in _CTX order."""
    return [
        ("f", terr_here, neigh),                      # terrain + full neighbourhood
        ("p", terr_here, neigh[:4]),                  # terrain + L,U,UL,UR
        ("t", terr_here, neigh[:3]),                  # terrain + L,U,UL
        ("d", terr_here, neigh[:2]),                  # terrain + L,U
        ("u", terr_here, (neigh[0],)),                # terrain + L
        ("m", terr_here, ()),                         # terrain only
    ]


def learn(map_names):
    """Count P(token | context) over the given corpus maps (both levels)."""
    tables = collections.defaultdict(collections.Counter)
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        g = tilegrid.tokenize(m)
        H, W, L = g["H"], g["W"], g["levels"]
        for l in range(L):
            terr, obj = g["terrain"][l], g["obj"][l]
            for y in range(H):
                for x in range(W):
                    th = terr[y][x]
                    neigh = tuple(_obj_at(obj, y + dy, x + dx, H, W) for dx, dy in _CTX)
                    tok = obj[y][x]
                    for key in _contexts(th, neigh):
                        tables[key][tok] += 1
    return tables


_MIN_COUNT = 6  # a backoff level must have >= this many observations to be trusted


def _predict(tables, terr_here, neigh, argmax=True, rng=None):
    for key in _contexts(terr_here, neigh):
        c = tables.get(key)
        if c and sum(c.values()) >= _MIN_COUNT:
            if argmax:
                return c.most_common(1)[0][0]
            toks, wts = zip(*c.items())
            return rng.choices(toks, weights=wts, k=1)[0]
    return EMPTY


def generate(tables, terrain, mode="free", real_obj=None, known_mask=None,
             argmax=True, seed=0):
    """Decode an object grid over `terrain` ([l][y][x] int).

    mode=free    : context from own predictions.
    mode=teacher : context from real_obj (upper bound; needs real_obj).
    mode=inpaint : start from real_obj where known_mask is True, predict elsewhere;
                   context uses known cells + own predictions for filled-in cells.
    """
    rng = random.Random(seed)
    L = len(terrain); H = len(terrain[0]); W = len(terrain[0][0])
    out = [[[EMPTY for _ in range(W)] for _ in range(H)] for _ in range(L)]
    for l in range(L):
        for y in range(H):
            for x in range(W):
                if mode == "inpaint" and known_mask[l][y][x]:
                    out[l][y][x] = real_obj[l][y][x]
                    continue
                ctx_src = real_obj[l] if mode == "teacher" else out[l]
                neigh = tuple(_obj_at(ctx_src, y + dy, x + dx, H, W) for dx, dy in _CTX)
                out[l][y][x] = _predict(tables, terrain[l][y][x], neigh, argmax, rng)
    return out


def all_map_names():
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(f"{ROOT}/out/maps/*.json"))
