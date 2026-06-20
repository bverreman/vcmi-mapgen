"""Conditional GAN for vegetation over a (markov) terrain patch — phase 1.

A U-Net **generator** emits per-tile (C+1) logits — empty + one channel per decoration category
from :mod:`veg_data` — turned into a C-channel occupancy **field** by a per-tile softmax
(:func:`to_field`); this matches the data (≤1 decoration anchor per tile). A PatchGAN
**discriminator/critic** scores (condition + occupancy) real-vs-fake on local patches.

The generator's softmax field is a *continuous preference field*; it is the GA decoder
(:mod:`veg_decode`) that turns it into concrete, footprint-valid objects. Training optionally
runs a **coevolution loop**: every ``ga_every`` steps a ``neg_provider`` (the GA, wired from
veg_decode) supplies discrete, valid layouts as extra hard negatives for D — closing the
train/inference gap. See the approved plan.

torch is required only for this module; importers elsewhere stay torch-free.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veg_data as VD

import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WEIGHTS = os.path.join(ROOT, "out", "veg_gan.pt")

NOISE_CH = 8
FM_LAMBDA = 10.0   # feature-matching weight (the key small-data stabilizer)
INST_NOISE = 0.1   # initial instance-noise sigma (decays to 0); stops the D sharpness-cheat
NOISE_DECAY_STEPS = 2000  # global horizon for the instance-noise decay (resume-stable)
EMA_DECAY = 0.999  # EMA of generator weights -> the inference generator (GAN quality win)
VAL_WINDOWS = 256  # held-out windows for the fidelity metric


def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    return random.Random(seed)


def _device(want_gpu: bool = False) -> str:
    return "cuda" if (want_gpu and torch.cuda.is_available()) else "cpu"


def _gn(ch: int) -> nn.GroupNorm:
    g = 8
    while g > 1 and ch % g != 0:
        g //= 2
    return nn.GroupNorm(g, ch)


# ---------------------------------------------------------------------------
# Generator — conditional U-Net
# ---------------------------------------------------------------------------

class _Down(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cin, cout, 4, 2, 1), _gn(cout), nn.LeakyReLU(0.2, True))

    def forward(self, x):
        return self.net(x)


class _Up(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.net = nn.Sequential(
            nn.ConvTranspose2d(cin, cout, 4, 2, 1), _gn(cout), nn.ReLU(True))

    def forward(self, x):
        return self.net(x)


class VegGenerator(nn.Module):
    def __init__(self, cond_ch=VD.COND_CH, veg_ch=None, noise_ch=NOISE_CH, width=64):
        super().__init__()
        veg_ch = veg_ch if veg_ch is not None else VD.num_categories()
        self.cond_ch, self.veg_ch, self.noise_ch = cond_ch, veg_ch, noise_ch
        w = width
        self.inp = nn.Sequential(
            nn.Conv2d(cond_ch + noise_ch, w, 3, 1, 1), _gn(w), nn.LeakyReLU(0.2, True))  # 32
        self.d1 = _Down(w, 2 * w)      # 16
        self.d2 = _Down(2 * w, 4 * w)  # 8
        self.d3 = _Down(4 * w, 4 * w)  # 4
        self.u1 = _Up(4 * w, 4 * w)    # 8
        self.u2 = _Up(8 * w, 2 * w)    # 16  (cat with d2)
        self.u3 = _Up(4 * w, w)        # 32  (cat with d1)
        # out = (empty + C categories); per-tile softmax models "≤1 anchor per tile".
        self.out = nn.Conv2d(2 * w, veg_ch + 1, 1)  # cat with inp
        # terrain-coupling mask: only categories that actually anchor on a terrain may be
        # emitted there. Non-persistent so it isn't saved/loaded with weights but follows .to().
        self.register_buffer("allowed", torch.from_numpy(VD.allowed_matrix()), persistent=False)

    def forward(self, cond, noise=None):
        if noise is None:
            noise = torch.randn(cond.shape[0], self.noise_ch, cond.shape[2], cond.shape[3],
                                device=cond.device)
        x0 = self.inp(torch.cat([cond, noise], 1))
        x1 = self.d1(x0)
        x2 = self.d2(x1)
        x3 = self.d3(x2)
        y = self.u1(x3)
        y = self.u2(torch.cat([y, x2], 1))
        y = self.u3(torch.cat([y, x1], 1))
        logits = self.out(torch.cat([y, x0], 1))  # (C+1) logits; channel 0 = empty
        # mask non-native category logits to -inf per tile (terrain = argmax of cond[:8]);
        # the empty channel (0) is always kept so a tile can stay free.
        terr = cond[:, :8].argmax(1)                       # [B,H,W] in 0..7
        m = self.allowed[terr].permute(0, 3, 1, 2)         # [B,C,H,W] bool
        cat = logits[:, 1:].masked_fill(~m, -1e9)
        return torch.cat([logits[:, :1], cat], 1)


def to_field(logits):
    """(C+1) logits -> C-channel category-occupancy field via per-tile softmax (the empty
    class absorbs probability so each tile's category probs sum to ≤1, matching real data)."""
    return F.softmax(logits, dim=1)[:, 1:]


# ---------------------------------------------------------------------------
# Discriminator — PatchGAN with spectral norm + feature taps
# ---------------------------------------------------------------------------

def _sn(conv):
    return nn.utils.spectral_norm(conv)


class VegDiscriminator(nn.Module):
    def __init__(self, cond_ch=VD.COND_CH, veg_ch=None, width=64):
        super().__init__()
        veg_ch = veg_ch if veg_ch is not None else VD.num_categories()
        w = width
        cin = cond_ch + veg_ch
        self.b1 = nn.Sequential(_sn(nn.Conv2d(cin, w, 4, 2, 1)), nn.LeakyReLU(0.2, True))      # 16
        self.b2 = nn.Sequential(_sn(nn.Conv2d(w, 2 * w, 4, 2, 1)), _gn(2 * w), nn.LeakyReLU(0.2, True))   # 8
        self.b3 = nn.Sequential(_sn(nn.Conv2d(2 * w, 4 * w, 4, 2, 1)), _gn(4 * w), nn.LeakyReLU(0.2, True))  # 4
        self.out = _sn(nn.Conv2d(4 * w, 1, 3, 1, 1))   # 4×4 logit map

    def forward(self, cond, veg):
        x = torch.cat([cond, veg], 1)
        f1 = self.b1(x)
        f2 = self.b2(f1)
        f3 = self.b3(f2)
        return self.out(f3), [f1, f2, f3]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _feature_match(real_feats, fake_feats):
    return sum(F.l1_loss(a, b) for a, b in zip(fake_feats, real_feats)) / max(len(real_feats), 1)


def make_score_fn(D, cond_single):
    """Batched discriminator score for the GA: occ[P,C,H,W] (numpy) -> realism[P] (numpy),
    all sharing one condition tile (mean PatchGAN logit = 'how real')."""
    dev = next(D.parameters()).device
    cond_t = torch.from_numpy(np.asarray(cond_single, np.float32)).to(dev)

    @torch.no_grad()
    def score(occ):
        occ_t = torch.from_numpy(np.asarray(occ, np.float32)).to(dev)
        P = occ_t.shape[0]
        dmap, _ = D(cond_t.unsqueeze(0).expand(P, *cond_t.shape), occ_t)
        return dmap.mean(dim=(1, 2, 3)).cpu().numpy()

    return score


def make_neg_provider(pop=24, gens=6, neg_k=8):
    """Coevolution hook: (cond[B,12,n,n], G, D, seed) -> hard-negative occupancy [k,C,n,n].

    For the first k windows: G's field -> GA-evolved discrete valid layout (scored by D) ->
    anchor-occupancy. These discrete, realistic layouts are exactly what the decoder emits at
    inference, so feeding them to D closes the train/inference gap.
    """
    import veg_decode as DEC

    def provider(cond, G, D, seed):
        dev = next(G.parameters()).device
        k = min(neg_k, cond.shape[0])
        cond_np = cond[:k].detach().cpu().numpy()
        fields = sample_field(G, cond_np, seed=seed)  # [k,C,n,n]
        out = []
        for i in range(k):
            terr, depth, land = VD.cond_to_arrays(cond_np[i])
            score_fn = make_score_fn(D, cond_np[i])
            negs = DEC.evolve_negatives(fields[i], land, depth, terr, score_fn,
                                        seed=seed + i, pop=pop, gens=gens, topk=1)
            out.append(negs[0])
        return torch.from_numpy(np.stack(out)).float().to(dev)

    return provider


def _ema_init(G):
    return {k: v.detach().clone() for k, v in G.state_dict().items()}


def _ema_update(ema, G, decay):
    with torch.no_grad():
        for k, v in G.state_dict().items():
            if v.dtype.is_floating_point:
                ema[k].mul_(decay).add_(v.detach(), alpha=1.0 - decay)
            else:
                ema[k].copy_(v)


@torch.no_grad()
def fidelity(G, conds, vegs):
    """Category-mix fidelity (lower=better): L1 between the real and the generated per-category
    occupancy histograms, plus the absolute density gap. Generated occupancy is the argmax
    decode of the softmax field (what the GA starts from), so this tracks real sample quality,
    not just the adversarial loss. Returns (cat_L1, density_gap)."""
    cls = G(conds).argmax(dim=1)                      # [B,H,W]; 0 = empty
    Cp1 = G(conds).shape[1]
    gen = F.one_hot(cls, Cp1).permute(0, 3, 1, 2)[:, 1:].float()  # drop empty -> [B,C,H,W]
    real_h = vegs.mean(dim=(0, 2, 3))
    gen_h = gen.mean(dim=(0, 2, 3))
    cat_l1 = (real_h - gen_h).abs().sum().item()
    density_gap = abs(float(vegs.mean()) - float(gen.mean()))
    return cat_l1, density_gap


def train(seed: int = 0, epochs: int = 5, out: str = DEFAULT_WEIGHTS,
          n_windows: int | None = None, width: int = 64, batch: int = 32,
          ga_every: int = 0, neg_provider=None, n: int = VD.N, want_gpu: bool = False,
          log_every: int = 50, resume: bool = False) -> str:
    """Train the conditional GAN. Writes the latest full state to ``out+'.ckpt'`` (resume) and
    the BEST-fidelity inference weights (EMA generator + D + config) to ``out`` every time the
    held-out category-mix fidelity improves.

    resume=True continues from ``out+'.ckpt'`` (G/D/optimizers/EMA/step) for ``epochs`` more.
    ga_every>0 enables the GA->D coevolution loop (extra hard negatives). """
    seed_everything(seed)
    dev = _device(want_gpu)
    C = VD.num_categories()
    if ga_every and neg_provider is None:
        neg_provider = make_neg_provider()

    ds = VD.WindowDataset(seed=seed, n=n, max_windows=n_windows)
    if len(ds) == 0:
        raise RuntimeError("no training windows; run veg_data taxonomy build first")
    # fixed held-out split for the fidelity metric (last VAL_WINDOWS windows)
    nval = min(VAL_WINDOWS, max(1, len(ds) // 10))
    val_cond = ds.conds[-nval:].to(dev)
    val_veg = ds.vegs[-nval:].to(dev)
    train_idx = list(range(len(ds) - nval))
    sub = torch.utils.data.Subset(ds, train_idx)
    gen = torch.Generator().manual_seed(seed)
    loader = torch.utils.data.DataLoader(sub, batch_size=min(batch, len(sub)), shuffle=True,
                                         drop_last=len(sub) >= batch, num_workers=0, generator=gen)

    G = VegGenerator(veg_ch=C, width=width).to(dev)
    D = VegDiscriminator(veg_ch=C, width=width).to(dev)
    optG = torch.optim.Adam(G.parameters(), lr=2e-4, betas=(0.5, 0.999))
    optD = torch.optim.Adam(D.parameters(), lr=2e-4, betas=(0.5, 0.999))
    ema = _ema_init(G)
    step, best_fid = 0, float("inf")

    ckpt_path = out + ".ckpt"
    if resume and os.path.exists(ckpt_path):
        ck = torch.load(ckpt_path, map_location=dev, weights_only=False)
        G.load_state_dict(ck["G"]); D.load_state_dict(ck["D"])
        optG.load_state_dict(ck["optG"]); optD.load_state_dict(ck["optD"])
        ema = {k: v.to(dev) for k, v in ck["G_ema"].items()}
        step, best_fid = ck["step"], ck.get("best_fid", float("inf"))
        print(f"resumed from {ckpt_path} at step {step} (best_fid {best_fid:.4f})")

    cfg = {"width": width, "veg_ch": C, "cond_ch": VD.COND_CH,
           "noise_ch": NOISE_CH, "n": n, "categories": VD.categories()}

    def _save_best(fid):
        torch.save({"G_ema": {k: v.cpu() for k, v in ema.items()},
                    "G": G.state_dict(), "D": D.state_dict(),
                    "config": cfg, "fidelity": fid, "step": step}, out)

    def _save_ckpt(fid):
        torch.save({"G": G.state_dict(), "D": D.state_dict(),
                    "optG": optG.state_dict(), "optD": optD.state_dict(),
                    "G_ema": {k: v.cpu() for k, v in ema.items()},
                    "step": step, "best_fid": best_fid, "fidelity": fid, "config": cfg},
                   ckpt_path)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    for ep in range(epochs):
        for cond, veg_real in loader:
            cond, veg_real = cond.to(dev), veg_real.to(dev)
            B = cond.shape[0]
            noise = torch.randn(B, NOISE_CH, n, n, device=dev)
            # instance noise (decaying on a global horizon so resume stays stable): blur BOTH
            # real and fake so D can't trivially separate them — keeps G's gradient alive.
            sigma = INST_NOISE * max(0.0, 1.0 - step / NOISE_DECAY_STEPS)

            def _in(v):
                return v + sigma * torch.randn_like(v) if sigma > 0 else v

            # --- D step ---
            with torch.no_grad():
                veg_fake = to_field(G(cond, noise))
            d_real, _ = D(cond, _in(veg_real))
            d_fake, _ = D(cond, _in(veg_fake))
            lossD = F.relu(1.0 - d_real).mean() + F.relu(1.0 + d_fake).mean()
            if ga_every and neg_provider is not None and step % ga_every == 0:
                veg_ga = neg_provider(cond, G, D, seed + step).to(dev)
                d_ga, _ = D(cond[:veg_ga.shape[0]], _in(veg_ga))
                lossD = lossD + 0.5 * F.relu(1.0 + d_ga).mean()
            optD.zero_grad(); lossD.backward(); optD.step()

            # --- G step ---
            veg_fake = to_field(G(cond, noise))
            d_fake, fake_feats = D(cond, _in(veg_fake))
            with torch.no_grad():
                _, real_feats = D(cond, _in(veg_real))
            lossG = -d_fake.mean() + FM_LAMBDA * _feature_match(real_feats, fake_feats)
            optG.zero_grad(); lossG.backward(); optG.step()
            _ema_update(ema, G, EMA_DECAY)

            if log_every and step % log_every == 0:
                print(f"  ep{ep} step{step}  D {lossD.item():.3f}  G {lossG.item():.3f}")
            step += 1

        # end of epoch: evaluate fidelity on the EMA generator, checkpoint, keep the best
        emaG = VegGenerator(veg_ch=C, width=width).to(dev)
        emaG.load_state_dict(ema); emaG.eval()
        cat_l1, dens = fidelity(emaG, val_cond, val_veg)
        improved = cat_l1 < best_fid
        if improved:
            best_fid = cat_l1
            _save_best(cat_l1)
        _save_ckpt(cat_l1)
        print(f"[epoch {ep}] step {step}  fidelity(cat_L1) {cat_l1:.4f}  density_gap {dens:.4f}"
              f"  best {best_fid:.4f}{'  <= saved' if improved else ''}")

    print(f"done: best fidelity {best_fid:.4f} -> {out}  (latest state -> {ckpt_path})")
    return out


# ---------------------------------------------------------------------------
# Loading / sampling
# ---------------------------------------------------------------------------

def load_models(weights: str = DEFAULT_WEIGHTS, want_gpu: bool = False):
    """Load (G, D, config). Uses the EMA generator weights for inference when present
    (smoother, higher-quality samples than the raw generator)."""
    ckpt = torch.load(weights, map_location="cpu", weights_only=False)
    cfg = ckpt["config"]
    dev = _device(want_gpu)
    G = VegGenerator(cond_ch=cfg["cond_ch"], veg_ch=cfg["veg_ch"],
                     noise_ch=cfg["noise_ch"], width=cfg["width"]).to(dev)
    D = VegDiscriminator(cond_ch=cfg["cond_ch"], veg_ch=cfg["veg_ch"], width=cfg["width"]).to(dev)
    G.load_state_dict(ckpt.get("G_ema") or ckpt["G"]); D.load_state_dict(ckpt["D"])
    G.eval(); D.eval()
    return G, D, cfg


def load_generator(weights: str = DEFAULT_WEIGHTS, want_gpu: bool = False):
    return load_models(weights, want_gpu)[0]


def _pad8(a: np.ndarray) -> np.ndarray:
    """Pad a [C,H,W] array on bottom/right so H,W are multiples of 8 (≥8) for the U-Net."""
    C, H, W = a.shape
    nh = max(8, -(-H // 8) * 8)
    nw = max(8, -(-W // 8) * 8)
    out = np.zeros((C, nh, nw), dtype=a.dtype)
    out[:, :H, :W] = a
    return out


def decorate_zone(z, layers, passage_tiles, gameplay_hard, level, G, D, seed,
                  margin=8) -> list[dict]:
    """GAN+GA vegetation for one generated zone -> concrete DECORATION objects (global
    coords). The condition is cropped from the FULL-map ``layers`` over the zone's bbox
    expanded by ``margin`` tiles, so the generator sees the real adjacent terrain and the
    map edge (not "everything outside is water"). Only the target zone's tiles are decoded;
    neighbour tiles merely inform the field. ``passage_tiles`` (the one-tile inter-zone
    connectors) are protected from blocking footprints so the gap stays open even if the
    generator walls the whole seam. Used by zone_engine.synthesize_zone / cmd_veg_sample."""
    import veg_decode as DEC
    tiles = z["tiles"]
    Hf, Wf = layers["terr"].shape
    xs = [x for x, _ in tiles]; ys = [y for _, y in tiles]
    # expanded bbox (clamped to the map) → real neighbour context in the condition.
    minx = max(0, min(xs) - margin); maxx = min(Wf - 1, max(xs) + margin)
    miny = max(0, min(ys) - margin); maxy = min(Hf - 1, max(ys) + margin)
    NW = maxx - minx + 1; NH = maxy - miny + 1

    cond = VD._encode_cond(layers, minx, miny, NH, NW)
    field = sample_field(G, _pad8(cond), seed=seed)[:, :NH, :NW]

    # decode ONLY the target zone's tiles (neighbours stay un-decorated here).
    land = np.zeros((NH, NW), dtype=bool)
    for (x, y) in tiles:
        land[y - miny, x - minx] = True
    depth = np.asarray(layers["depth"][miny:maxy + 1, minx:maxx + 1], dtype=np.float32)
    terr = np.asarray(layers["terr"][miny:maxy + 1, minx:maxx + 1], dtype=int)

    # protect gameplay + the one-tile passages: no blocking footprint may cover them.
    gh = np.zeros((NH, NW), dtype=bool)
    for (x, y) in set(gameplay_hard) | set(passage_tiles or ()):
        if minx <= x <= maxx and miny <= y <= maxy:
            gh[y - miny, x - minx] = True

    # D needs ≥8 tiles per side (three stride-2 convs); smaller windows use the proxy score.
    score_fn = make_score_fn(D, cond) if min(NH, NW) >= 8 else None
    return DEC.decode_zone(field, land, gh, depth, terr, seed=seed, score_fn=score_fn,
                           level=level, ox=minx, oy=miny)


@torch.no_grad()
def sample_field(G: VegGenerator, cond: np.ndarray, seed: int = 0) -> np.ndarray:
    """cond [COND_CH,H,W] or [B,COND_CH,H,W] -> category field, same batch layout."""
    torch.manual_seed(seed)
    dev = next(G.parameters()).device
    arr = np.asarray(cond, dtype=np.float32)
    single = arr.ndim == 3
    t = torch.from_numpy(arr[None] if single else arr).to(dev)
    field = to_field(G(t)).cpu().numpy()
    return field[0] if single else field


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="train the vegetation GAN")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--windows", type=int, default=None)
    ap.add_argument("--width", type=int, default=64)
    ap.add_argument("--out", default=DEFAULT_WEIGHTS)
    a = ap.parse_args()
    train(seed=a.seed, epochs=a.epochs, n_windows=a.windows, width=a.width, out=a.out)
