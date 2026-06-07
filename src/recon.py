"""Per-tile reconstruction driver (Phase A overfit / Phase B held-out).

Trains the autoregressive tile model, decodes the object channel over the target's
real terrain, scores per-tile accuracy against the real map, and renders a
side-by-side (real | generated) PNG so the reproduction is judged by eye AND by number.

  Phase A (artifact)        : --include-target   train ON the target; argmax decode
  Phase B (held-out recon)  : default            target EXCLUDED; inpaint from a
                              fraction of known object cells
"""

import sys, os, json, argparse, collections
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tilegrid, tilemodel, faithful
from tilegrid import EMPTY
from render import TERRAIN_RGB, PURPOSE_RGB, BIG

TILE = 8
OBJ = json.load(open(f"{ROOT}/out/objlib.json"))
_FAC = json.load(open(f"{ROOT}/out/factors.json"))
_VIEW = {int(k): v for k, v in _FAC["terrain_view"].items()}


def _terr_cell(c):
    """Corpus terrain cell {t,river,road} -> faithful writer cell {t,view,m}."""
    return {"t": c["t"], "view": _VIEW.get(c["t"], 0), "m": 0}


def pick_entry(purpose, terrain_id):
    """Highest-weight objlib entry for (purpose, terrain), with any-terrain fallback."""
    by_terr = OBJ.get(purpose)
    if not by_terr:
        return None
    cands = by_terr.get(str(terrain_id))
    if not cands:
        # fall back to whatever terrain has entries for this purpose
        for v in by_terr.values():
            if v:
                cands = v
                break
    if not cands:
        return None
    return max(cands, key=lambda e: e.get("weight", 0))


def detokenize_to_vmap(real_map, obj_grid, out_path, name):
    """Real terrain + token-grid objects -> editor-loadable .vmap. Each non-EMPTY
    cell becomes a concrete objlib object of that purpose, terrain-correct."""
    terrain = real_map["terrain"]
    L = len(terrain); H = len(terrain[0]); W = len(terrain[0][0])
    wterrain = [[[_terr_cell(c) for c in row] for row in lvl] for lvl in terrain]
    objects = []
    main_town = None
    for l in range(L):
        for y in range(H):
            for x in range(W):
                tok = obj_grid[l][y][x]
                if tok in (EMPTY, "#"):
                    continue
                e = pick_entry(tok, terrain[l][y][x]["t"])
                if not e:
                    continue
                objects.append({"type": e["type"], "subtype": e["subtype"],
                                "animation": e["animation"], "mask": e["mask"],
                                "x": x, "y": y, "l": l})
                if tok == "TOWN" and l == 0 and main_town is None:
                    main_town = {"l": 0, "x": x, "y": y}
    fm = {"terrain": wterrain, "objects": objects, "main_town": main_town, "name": name}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    faithful.to_vmap(fm, out_path, name=name)
    return out_path, len(objects)


def render_level(terr, obj, title):
    H = len(terr); W = len(terr[0])
    img = Image.new("RGB", (W * TILE, H * TILE), (0, 0, 0))
    px = img.load()
    for y in range(H):
        for x in range(W):
            r, g, b = TERRAIN_RGB.get(terr[y][x], (0, 0, 0))
            for dy in range(TILE):
                for dx in range(TILE):
                    px[x * TILE + dx, y * TILE + dy] = (r, g, b)
    d = ImageDraw.Draw(img)
    for y in range(H):
        for x in range(W):
            tok = obj[y][x]
            if tok == EMPTY or tok == "#":
                continue
            col = PURPOSE_RGB.get(tok, (90, 90, 90))
            cx, cy = x * TILE + TILE // 2, y * TILE + TILE // 2
            rad = 3 if tok in BIG else 2
            d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
    d.text((4, 4), title, fill=(255, 255, 255))
    return img


def compose(real_g, gen_obj, out_path, label):
    L = real_g["levels"]
    panels = []
    for l in range(L):
        panels.append(render_level(real_g["terrain"][l], real_g["obj"][l], f"REAL L{l}"))
        panels.append(render_level(real_g["terrain"][l], gen_obj[l], f"{label} L{l}"))
    w = sum(p.width for p in panels) + 6 * (len(panels) - 1)
    h = max(p.height for p in panels)
    canvas = Image.new("RGB", (w, h), (20, 20, 20))
    x = 0
    for p in panels:
        canvas.paste(p, (x, 0)); x += p.width + 6
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="All for One")
    ap.add_argument("--include-target", action="store_true",
                    help="Phase A: train ON the target (overfit artifact)")
    ap.add_argument("--mode", choices=["free", "teacher", "inpaint"], default=None)
    ap.add_argument("--sample", action="store_true", help="sample instead of argmax")
    ap.add_argument("--known-frac", type=float, default=0.3,
                    help="inpaint: fraction of object cells given as known context")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--emit-vmap", action="store_true",
                    help="write an editor-loadable .vmap of the reconstruction")
    args = ap.parse_args()

    m = json.load(open(f"{ROOT}/out/maps/{args.target}.json"))
    real = tilegrid.tokenize(m)

    names = tilemodel.all_map_names()
    if args.include_target:
        train = names
        phase, default_mode = "A", "free"
    else:
        train = [n for n in names if n != args.target]
        phase, default_mode = "B", "inpaint"
    mode = args.mode or default_mode
    print(f"Phase {phase}: train on {len(train)} maps (target {'IN' if args.include_target else 'OUT'}), mode={mode}")

    tables = tilemodel.learn(train)

    known_mask = None
    if mode == "inpaint":
        import random
        rng = random.Random(args.seed)
        known_mask = [[[False] * real["W"] for _ in range(real["H"])] for _ in range(real["levels"])]
        for l in range(real["levels"]):
            for y in range(real["H"]):
                for x in range(real["W"]):
                    if real["obj"][l][y][x] != EMPTY and rng.random() < args.known_frac:
                        known_mask[l][y][x] = True

    gen = tilemodel.generate(tables, real["terrain"], mode=mode,
                             real_obj=real["obj"], known_mask=known_mask,
                             argmax=not args.sample, seed=args.seed)

    gen_grid = {"H": real["H"], "W": real["W"], "levels": real["levels"],
                "terrain": real["terrain"], "obj": gen}
    acc = tilegrid.accuracy(gen_grid, real)
    print(f"per-tile terrain_acc={acc['terrain_acc']}  obj_acc={acc['obj_acc']}  "
          f"obj_acc_gameplay={acc['obj_acc_gameplay']}")
    print(f"real_obj_cells={acc['real_obj_cells']}  gen_obj_cells={acc['gen_obj_cells']}")

    # Honest model skill in inpaint: the model is only credited/charged on UNKNOWN
    # cells. Known anchors are correct by construction and excluded.
    if mode == "inpaint":
        n_known = tp_u = real_u = gen_u = gp_match_u = gp_cells_u = 0
        for l in range(real["levels"]):
            for y in range(real["H"]):
                for x in range(real["W"]):
                    if known_mask[l][y][x]:
                        n_known += 1
                        continue
                    rp, gp = real["obj"][l][y][x], gen[l][y][x]
                    if rp != EMPTY: real_u += 1
                    if gp != EMPTY: gen_u += 1
                    if rp != EMPTY and gp == rp: tp_u += 1
                    if (rp in tilegrid.GAMEPLAY) or (gp in tilegrid.GAMEPLAY):
                        gp_cells_u += 1
                        if gp == rp: gp_match_u += 1
        recall_u = tp_u / real_u if real_u else 0.0
        prec_u = tp_u / gen_u if gen_u else 0.0
        print(f"UNKNOWN-only (honest model skill): known_anchors={n_known}  "
              f"recovered_tp={tp_u}/{real_u} real-obj cells  "
              f"precision={prec_u:.2f} recall={recall_u:.2f}  "
              f"gameplay_acc={gp_match_u/gp_cells_u:.3f}" if gp_cells_u else "n/a")
    print("per-purpose F1 (gameplay):")
    for p in sorted(acc["per_purpose"], key=lambda k: -acc["per_purpose"][k]["f1"]):
        if p in tilegrid.GAMEPLAY:
            v = acc["per_purpose"][p]
            print(f"  {p:16s} P={v['precision']:.2f} R={v['recall']:.2f} F1={v['f1']:.2f}  (tp={v['tp']} fp={v['fp']} fn={v['fn']})")

    safe = args.target.replace(" ", "_")
    out = f"{ROOT}/out/render/recon_{safe}_phase{phase}_{mode}.png"
    compose(real, gen, out, f"GEN(P{phase})")
    print("rendered:", out)

    if args.emit_vmap:
        vpath = f"{ROOT}/out/Recon-{safe}-phase{phase}.vmap"
        _, nobj = detokenize_to_vmap(m, gen, vpath, f"Recon {args.target} (P{phase})")
        print(f"vmap: {vpath}  ({nobj} objects)")


if __name__ == "__main__":
    main()
