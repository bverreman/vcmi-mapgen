"""Train the terrain->placement CNN from scratch on CPU.

Loss: positive-weighted MSE on the density heatmaps (objects are sparse, so plain MSE
collapses to all-zero). Validation is the HONEST metric we care about -- per-8x8-zone
correlation between PREDICTED density and REAL object counts on held-out maps: "does
the model predict WHERE objects go?" -- not a self-score.
"""

import sys, os, json, time, random, argparse
import numpy as np
import torch
import torch.nn as nn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dl_data as D
from dl_model import PlacementNet, PlacementUNet, n_params

VAL_MAPS = ["All for One"]            # always hold this out (our running comparison)
POS_W = 12.0                          # upweight object regions in the MSE


def make_batch(train, rng, bs, crop=D.CROP):
    xs, ys = [], []
    for _ in range(bs):
        _, X, Y = train[rng.randrange(len(train))]
        xc, yc = D.sample_crop(X, Y, rng, crop)
        xs.append(xc); ys.append(yc)
    return (torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys)))


def full_map_aug(X, Y, rng):
    """Whole map (no crop) with a random D4 orientation -> batch of 1."""
    k = rng.randint(0, 3); flip = rng.random() < 0.5
    xa = D._d4(X, k, flip); ya = D._d4(Y, k, flip)
    return torch.from_numpy(xa)[None], torch.from_numpy(ya)[None]


def canvas_batch(train, rng, bs):
    """Batch of fixed-canvas maps with per-sample D4 (X, Y, mask rotated together)."""
    xs, ys, ms = [], [], []
    for _ in range(bs):
        _, Xc, Yc, M, h, w = train[rng.randrange(len(train))]
        k = rng.randint(0, 3); flip = rng.random() < 0.5
        xs.append(D._d4(Xc, k, flip)); ys.append(D._d4(Yc, k, flip)); ms.append(D._d4(M, k, flip))
    return (torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys)),
            torch.from_numpy(np.stack(ms)))


def evaluate_canvas(net, val):
    net.eval(); corrs = []
    with torch.no_grad():
        for name, Xc, Yc, M, h, w in val:
            pred = net(torch.from_numpy(Xc)[None], torch.from_numpy(M)[None])[0].numpy()
            corrs.append((name, zone_corr(pred[:, :h, :w], Yc[:, :h, :w])))
    net.train()
    return corrs


def zone_corr(pred, Y_real_counts, z=8):
    """Per-zone (z x z) correlation between predicted density and real object counts,
    pooled over all purposes. pred:[P,H,W] np, Y_real_counts:[P,H,W] np (splatted)."""
    P, H, W = pred.shape
    gy, gx = H // z, W // z
    rv, gv = [], []
    for p in range(P):
        for i in range(gy):
            for j in range(gx):
                rv.append(Y_real_counts[p, i*z:(i+1)*z, j*z:(j+1)*z].sum())
                gv.append(pred[p, i*z:(i+1)*z, j*z:(j+1)*z].sum())
    rv = np.array(rv); gv = np.array(gv)
    if rv.std() < 1e-9 or gv.std() < 1e-9:
        return 0.0
    return float(np.corrcoef(rv, gv)[0, 1])


def evaluate(net, val):
    net.eval(); corrs = []
    with torch.no_grad():
        for name, X, Y in val:
            pred = net(torch.from_numpy(X)[None])[0].numpy()
            corrs.append((name, zone_corr(pred, Y)))
    net.train()
    return corrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--bs", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--out", default=f"{ROOT}/out/dl_placement.pt")
    ap.add_argument("--arch", choices=["crop", "unet"], default="unet",
                    help="unet = full-map U-Net (global context); crop = old 32x32 dilated net")
    args = ap.parse_args()

    rng = random.Random(0); torch.manual_seed(0)
    names = D.all_map_names()
    val_names = [n for n in VAL_MAPS if n in names]
    train_names = [n for n in names if n not in set(val_names)]
    canvas = (args.arch == "unet")
    print(f"building data: {len(train_names)} train, {len(val_names)} val maps "
          f"({'144x144 canvas + NOTHING' if canvas else 'crops'})...", flush=True)
    if canvas:
        train = D.build_canvas(train_names); val = D.build_canvas(val_names)
    else:
        train = D.build_cached(train_names); val = D.build_cached(val_names)

    net = PlacementUNet() if canvas else PlacementNet()
    print(f"arch={args.arch}  params: {n_params(net):,}", flush=True)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr)
    steps = args.steps if not canvas else max(1, len(train) // args.bs)

    best = -1.0
    for ep in range(args.epochs):
        t0 = time.time(); tot = 0.0
        for _ in range(steps):
            if canvas:
                xb, yb, mb = canvas_batch(train, rng, args.bs)
                pred = net(xb, mb)
                w = 1.0 + POS_W * yb
                num = (mb * w * (pred - yb) ** 2).sum()
                loss = num / (mb.sum() * D.NPUR + 1e-6)     # masked mean over the real map only
            else:
                xb, yb = make_batch(train, rng, args.bs)
                pred = net(xb)
                w = 1.0 + POS_W * yb
                loss = (w * (pred - yb) ** 2).mean()
            opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item()
        tot /= steps
        corrs = evaluate_canvas(net, val) if canvas else evaluate(net, val)
        mc = np.mean([c for _, c in corrs])
        flag = ""
        if mc > best:
            best = mc
            torch.save({"state": net.state_dict(), "purposes": D.PURPOSES,
                        "c_in": D.C_IN, "arch": args.arch}, args.out)
            flag = " *saved"
        print(f"ep {ep:3d}  loss={tot:.4f}  "
              f"val_zone_corr={mc:+.3f} ({', '.join(f'{n[:14]}={c:+.2f}' for n,c in corrs)})  "
              f"{time.time()-t0:.1f}s{flag}")
    print(f"\nbest val zone-correlation: {best:+.3f}  -> {args.out}")
    print("(baseline to beat: terrain-type Method A regional corr = +0.25)")


if __name__ == "__main__":
    main()
