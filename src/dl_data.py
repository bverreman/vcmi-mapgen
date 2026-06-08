"""Data pipeline for the terrain->placement CNN.

Each corpus map (surface level) becomes:
  - a terrain FEATURE stack  X: [C,H,W]  (one-hot terrain + river/road + distance fields)
  - a placement TARGET stack Y: [P,H,W]  (per gameplay-purpose Gaussian density heatmap)

The model learns X -> Y: where, on THIS terrain, each kind of object tends to sit.
Features deliberately vary across same-type tiles (distance-to-water, distance-to-edge),
which is exactly the spatial structure the terrain-type-only baseline ignored.

Training samples are random 32x32 crops (fits even 36x36 maps) with D4 augmentation
(8 orientations) -> thousands of samples from 159 maps. Inference is fully convolutional,
so the trained net runs on a whole map of any size.
"""

import sys, os, json, glob, math, collections
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ontology as ON

PURPOSES = [
    "TOWN", "GUARD", "MINE", "DWELLING", "BANK", "REWARD_PICKUP", "RESOURCE_PILE",
    "TRANSPORT", "STAT_PERMANENT", "BONUS_TEMP", "SPELL_SKILL", "QUEST_GATE",
]
PIDX = {p: i for i, p in enumerate(PURPOSES)}
NPUR = len(PURPOSES)
N_TERR = 10                      # terrain ids 0..9
WATER, ROCK = 8, 9
CROP = 32
C_BASE = N_TERR + 4             # one-hot(10) + river + road + dist_water + dist_edge
NOTHING = C_BASE                # channel index of the NOTHING_TILE (outside-the-map marker)
C_IN = C_BASE + 1               # base features + NOTHING channel
CANVAS = 144                     # all maps written into the biggest grid; smaller = sub-grid


def _dist_field(mask):
    """Manhattan distance (multi-source BFS) from every cell to the nearest True cell
    in `mask`. If mask is empty, returns a large constant field."""
    H, W = mask.shape
    INF = H + W
    dist = np.full((H, W), INF, dtype=np.float32)
    q = collections.deque()
    for y in range(H):
        for x in range(W):
            if mask[y, x]:
                dist[y, x] = 0; q.append((x, y))
    while q:
        x, y = q.popleft()
        d = dist[y, x]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and dist[ny, nx] > d + 1:
                dist[ny, nx] = d + 1; q.append((nx, ny))
    return dist


def features(level_terr):
    """[C_IN,H,W] float32 terrain feature stack for one level grid (list of rows of
    cells {t,river,road})."""
    H = len(level_terr); W = len(level_terr[0])
    t = np.array([[c["t"] for c in row] for row in level_terr], dtype=np.int64)
    river = np.array([[1.0 if row[x].get("river") else 0.0 for x in range(W)] for row in level_terr], dtype=np.float32)
    road = np.array([[1.0 if row[x].get("road") else 0.0 for x in range(W)] for row in level_terr], dtype=np.float32)
    X = np.zeros((C_BASE, H, W), dtype=np.float32)
    for tid in range(N_TERR):
        X[tid] = (t == tid).astype(np.float32)
    X[N_TERR + 0] = river
    X[N_TERR + 1] = road
    dw = _dist_field(t == WATER)
    de = np.minimum.reduce([
        np.arange(W)[None, :].repeat(H, 0),
        (W - 1 - np.arange(W))[None, :].repeat(H, 0),
        np.arange(H)[:, None].repeat(W, 1),
        (H - 1 - np.arange(H))[:, None].repeat(W, 1),
    ]).astype(np.float32)
    X[N_TERR + 2] = np.tanh(dw / 12.0)        # saturating distance-to-water
    X[N_TERR + 3] = np.tanh(de / 12.0)        # saturating distance-to-edge
    return X


_GK = None
def _gauss_kernel(sigma=1.5, r=4):
    global _GK
    if _GK is None:
        ax = np.arange(-r, r + 1)
        xx, yy = np.meshgrid(ax, ax)
        _GK = np.exp(-(xx ** 2 + yy ** 2) / (2 * sigma ** 2)).astype(np.float32)
    return _GK, r


def target(m, level=0):
    """[NPUR,H,W] Gaussian-splatted density of gameplay objects on `level`."""
    terr = m["terrain"][level]
    H = len(terr); W = len(terr[0])
    Y = np.zeros((NPUR, H, W), dtype=np.float32)
    K, r = _gauss_kernel()
    for o in m["objects"]:
        if o.get("l", 0) != level:
            continue
        p = ON.resolve(o["class"], o["subclass"]).get("purpose", "UNKNOWN")
        if p not in PIDX:
            continue
        x, y = o["x"], o["y"]
        if not (0 <= x < W and 0 <= y < H):
            continue
        ci = PIDX[p]
        y0, y1 = max(0, y - r), min(H, y + r + 1)
        x0, x1 = max(0, x - r), min(W, x + r + 1)
        ky0, ky1 = y0 - (y - r), K.shape[0] - ((y + r + 1) - y1)
        kx0, kx1 = x0 - (x - r), K.shape[1] - ((x + r + 1) - x1)
        Y[ci, y0:y1, x0:x1] += K[ky0:ky1, kx0:kx1]
    return Y


def all_map_names():
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(f"{ROOT}/out/maps/*.json"))


def build(map_names, level=0):
    """Precompute (X, Y) full arrays per map (surface level)."""
    data = []
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        if level >= len(m["terrain"]):
            continue
        data.append((name, features(m["terrain"][level]), target(m, level)))
    return data


def to_canvas(Xb, Yb, canvas=CANVAS):
    """Write a native-size map (top-left) into the fixed CANVAS grid. Outside the map
    is the NOTHING_TILE: NOTHING channel = 1, all other features 0, target 0. Returns
    (Xc[C_IN,canvas,canvas], Yc[NPUR,canvas,canvas], M[1,canvas,canvas], h, w)."""
    h, w = Xb.shape[1], Xb.shape[2]
    Xc = np.zeros((C_IN, canvas, canvas), dtype=np.float32)
    Xc[NOTHING, :, :] = 1.0                      # everything starts as NOTHING
    Xc[:C_BASE, :h, :w] = Xb
    Xc[NOTHING, :h, :w] = 0.0                     # real map is not NOTHING
    Yc = np.zeros((NPUR, canvas, canvas), dtype=np.float32); Yc[:, :h, :w] = Yb
    M = np.zeros((1, canvas, canvas), dtype=np.float32); M[0, :h, :w] = 1.0
    return Xc, Yc, M, h, w


def build_canvas(map_names, level=0, rebuild=False):
    """Like build_cached but every map is embedded in the 144x144 canvas + NOTHING.
    Returns [(name, Xc, Yc, M, h, w)]."""
    import pickle, time
    path = f"{ROOT}/out/dl_canvas_l{level}.pkl"
    cache = {}
    if os.path.exists(path) and not rebuild:
        cache = pickle.load(open(path, "rb"))
    missing = [n for n in map_names if n not in cache]
    if missing:
        t0 = time.time()
        base = build(missing, level)             # native features/targets (reused, cached upstream)
        for i, (name, X, Y) in enumerate(base):
            cache[name] = to_canvas(X, Y)
            if (i + 1) % 25 == 0:
                print(f"  canvas {i+1}/{len(base)} ({time.time()-t0:.0f}s)", flush=True)
        pickle.dump(cache, open(path, "wb"))
        print(f"  cached {len(missing)} canvas maps -> {path} ({time.time()-t0:.0f}s)", flush=True)
    return [(n, *cache[n]) for n in map_names if n in cache]


def _cache_path(level):
    return f"{ROOT}/out/dl_cache_l{level}.pkl"


def build_cached(map_names, level=0, rebuild=False):
    """Build once, cache to disk (the pure-Python distance fields are slow). Returns
    only the requested maps, in order."""
    import pickle, time
    path = _cache_path(level)
    cache = {}
    if os.path.exists(path) and not rebuild:
        cache = pickle.load(open(path, "rb"))
    missing = [n for n in map_names if n not in cache]
    if missing:
        t0 = time.time()
        for i, (name, X, Y) in enumerate(build(missing, level)):
            cache[name] = (X, Y)
            if (i + 1) % 25 == 0:
                print(f"  built {i+1}/{len(missing)}  ({time.time()-t0:.0f}s)", flush=True)
        pickle.dump(cache, open(path, "wb"))
        print(f"  cached {len(missing)} maps -> {path} ({time.time()-t0:.0f}s)", flush=True)
    return [(n, cache[n][0], cache[n][1]) for n in map_names if n in cache]


# ---- D4 augmentation + random crop ----
def _d4(arr, k, flip):
    a = np.rot90(arr, k, axes=(1, 2))
    if flip:
        a = a[:, :, ::-1]
    return np.ascontiguousarray(a)


def sample_crop(X, Y, rng, crop=CROP):
    _, H, W = X.shape
    if H < crop or W < crop:
        padH, padW = max(0, crop - H), max(0, crop - W)
        X = np.pad(X, ((0, 0), (0, padH), (0, padW)))
        Y = np.pad(Y, ((0, 0), (0, padH), (0, padW)))
        _, H, W = X.shape
    y0 = rng.randint(0, H - crop); x0 = rng.randint(0, W - crop)  # randint is inclusive
    xc = X[:, y0:y0 + crop, x0:x0 + crop]
    yc = Y[:, y0:y0 + crop, x0:x0 + crop]
    k = rng.randint(0, 4); flip = rng.random() < 0.5
    return _d4(xc, k, flip), _d4(yc, k, flip)


if __name__ == "__main__":
    import random
    names = all_map_names()
    print(f"{len(names)} maps; building features for 3 as a check...")
    d = build(names[:3])
    for name, X, Y in d:
        print(f"  {name[:30]:30s} X={X.shape} Y={Y.shape}  objs/heat={Y.sum():.0f}  "
              f"nonzero purposes={(Y.reshape(NPUR,-1).sum(1)>0).sum()}/{NPUR}")
    rng = random.Random(0)
    xc, yc = sample_crop(d[0][1], d[0][2], rng)
    print(f"crop X={xc.shape} Y={yc.shape}")
