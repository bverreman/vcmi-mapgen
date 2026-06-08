"""Generate a map from the trained terrain->placement CNN, and compare it HONESTLY to
the real map (regional + per-tile match -- never a self-score).

  predict per-purpose density heatmaps on the terrain
  -> sample object positions (greedy peaks for seed 0 = closest to real; weighted
     sampling for seed>0 = a different in-distribution map)
  -> instantiate (objlib) + decoration fill + reachability repair
  -> emit .vmap; report match-to-real.

Counts come from the model itself: a Gaussian splat contributes KSUM mass, so
predicted_mass / KSUM ~= expected object count for that purpose on this terrain.
"""

import sys, os, json, argparse, collections
import numpy as np
import torch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dl_data as D
from dl_model import PlacementNet, PlacementUNet
import energy_place as EP
import traverse as TR
import faithful
import tilegrid

GATED = {"TOWN", "MINE"}                              # reachability-critical
RADIUS = {"TOWN": 5, "MINE": 3, "DWELLING": 3, "BANK": 2}   # min separation per purpose
DEF_RADIUS = 1                                        # loot: tiny, preserve density spread


def load(path=f"{ROOT}/out/dl_placement.pt"):
    ck = torch.load(path, map_location="cpu")
    net = PlacementUNet() if ck.get("arch") == "unet" else PlacementNet()
    net.load_state_dict(ck["state"]); net.eval()
    return net, ck["purposes"]


def predict(net, terr_level):
    """Embed the terrain in the 144x144 canvas (+NOTHING), run the U-Net with the
    validity mask (post-process forces NOTHING outside), crop back to the map size."""
    Xb = D.features(terr_level)
    h, w = Xb.shape[1], Xb.shape[2]
    Xc, _, M, _, _ = D.to_canvas(Xb, np.zeros((D.NPUR, h, w), dtype=np.float32))
    with torch.no_grad():
        pred = net(torch.from_numpy(Xc)[None], torch.from_numpy(M)[None])[0].numpy()
    return pred[:, :h, :w]                                       # [P,h,w] native


def calibrate(net, purposes, names=None, cache=f"{ROOT}/out/dl_calib.json"):
    """Per-purpose count calibration a_p so that round(predicted_mass * a_p) matches
    corpus counts. The positive-weighted loss inflates density amplitude, so KSUM is
    unreliable; this fits predicted-mass -> true-count over the corpus instead."""
    if os.path.exists(cache):
        return json.load(open(cache))
    import ontology as ON
    names = names or D.all_map_names()
    num = collections.Counter(); den = collections.Counter()
    for name in names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        pred = predict(net, m["terrain"][0])
        for pi, P in enumerate(purposes):
            den[P] += float(pred[pi].sum())
        for o in m["objects"]:
            if o.get("l", 0) != 0:
                continue
            P = ON.resolve(o["class"], o["subclass"]).get("purpose")
            if P in purposes:
                num[P] += 1
    a = {P: (num[P] / den[P] if den[P] > 0 else 0.0) for P in purposes}
    json.dump(a, open(cache, "w"), indent=1)
    return a


def _nms(heat, n, radius, rng, sample):
    """Pick n positions from a density heatmap. mode 'sample' (sample>0) draws
    proportional to density -> reproduces the per-zone DENSITY (best resemblance);
    'peak' (sample==0/seed0) takes the strongest maxima. A small suppression radius
    avoids stacking objects on one tile."""
    H, W = heat.shape
    h = heat.copy().astype(np.float64); pts = []
    sup = max(1, radius)
    for _ in range(int(n)):
        if h.max() <= 0:
            break
        if sample:
            flat = h.ravel(); s = flat.sum()
            idx = rng.choice(len(flat), p=flat / s)
        else:
            idx = int(h.argmax())
        y, x = divmod(idx, W)
        pts.append((x, y))
        y0, y1 = max(0, y - sup), min(H, y + sup + 1)
        x0, x1 = max(0, x - sup), min(W, x + sup + 1)
        h[y0:y1, x0:x1] = 0.0
    return pts


def sample_points(pred, purposes, terr, seed, level=0, calib=None):
    """Heatmaps -> [(l,x,y,purpose)] for gameplay objects."""
    rng = np.random.default_rng(seed)
    KSUM = float(D._gauss_kernel()[0].sum())
    comp0 = EP._largest_component(terr, level)
    H, W = pred.shape[1], pred.shape[2]
    out = []
    for pi, P in enumerate(purposes):
        heat = pred[pi].copy()
        # confine reachability-critical purposes to the passable largest component
        if P in GATED:
            mask = np.zeros((H, W), bool)
            for (x, y) in comp0:
                mask[y, x] = True
            heat = heat * mask
        mass = float(pred[pi].sum())
        n = round(mass * calib[P]) if calib else round(mass / KSUM)
        # proportional sampling reproduces the predicted DENSITY (best regional match);
        # seed just selects the draw. peak mode kept available for seed<0.
        pts = _nms(heat, n, RADIUS.get(P, DEF_RADIUS), rng, sample=(seed >= 0))
        for (x, y) in pts:
            out.append((level, x, y, P))
    return out


def add_decorations(terr, pts, place_model, seed):
    rng = __import__("random").Random(seed + 7)
    L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    occupied = {(l, x, y) for (l, x, y, _) in pts}
    deco_mu = sum(place_model["rate"].get("DECORATION", {}).get(c["t"], 0.0)
                  for lvl in terr for row in lvl for c in row)
    EP.sample_tiles_deco(terr, place_model["rate"], rng, occupied, pts, int(round(deco_mu)), L, H, W)
    return pts


def regional_corr(gen_pts, real_map, level=0, z=8):
    real = tilegrid.tokenize(real_map)
    H, W = real["H"], real["W"]
    gp = collections.defaultdict(lambda: collections.Counter())
    rp = collections.defaultdict(lambda: collections.Counter())
    for (l, x, y, p) in gen_pts:
        if l == level and p in tilegrid.GAMEPLAY:
            gp[(y // z, x // z)][p] += 1
    for y in range(H):
        for x in range(W):
            t = real["obj"][level][y][x]
            if t in tilegrid.GAMEPLAY:
                rp[(y // z, x // z)][t] += 1
    rv, gv = [], []
    for i in range(H // z):
        for j in range(W // z):
            for p in tilegrid.GAMEPLAY:
                rv.append(rp[(i, j)][p]); gv.append(gp[(i, j)][p])
    rv = np.array(rv, float); gv = np.array(gv, float)
    if rv.std() < 1e-9 or gv.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(rv, gv)[0, 1])


def generate(terrain_name, seed=0, emit=True, decorate=True):
    net, purposes = load()
    src = json.load(open(f"{ROOT}/out/maps/{terrain_name}.json"))
    terr = src["terrain"]
    pred = predict(net, terr[0])
    calib = calibrate(net, purposes)
    pts = sample_points(pred, purposes, terr, seed, calib=calib)
    corr = regional_corr(pts, src)

    place_model = EP.fit(D.all_map_names()) if decorate else None
    if decorate:
        pts = add_decorations(terr, pts, place_model, seed)
    fm = EP.to_fm(terr, pts, name=f"DLGen {terrain_name} s{seed}")
    n_rem, _ = EP.repair_reachability(fm)
    rr = TR.traverse(fm)
    report = {"terrain": terrain_name, "seed": seed, "regional_corr_vs_real": round(corr, 3),
              "reachable": rr["ok"], "removed": n_rem, "objects": len(fm["objects"])}
    if emit and rr["ok"]:
        out = f"{ROOT}/out/DLGen-{terrain_name.replace(' ', '_')}-s{seed}.vmap"
        faithful.to_vmap(fm, out, name=fm["name"]); report["vmap"] = out
    return fm, pred, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-deco", action="store_true")
    args = ap.parse_args()
    fm, pred, rep = generate(args.terrain, args.seed, decorate=not args.no_deco)
    print(json.dumps(rep, indent=1))
    print("(baseline regional corr: Method A = +0.25)")


if __name__ == "__main__":
    main()
