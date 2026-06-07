"""Driver for the patch-based reconstruction (8x8 zones as the unit)."""

import sys, os, json, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tilegrid, patchmodel, recon


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="All for One")
    ap.add_argument("--phase", choices=["A", "B"], default="A")
    ap.add_argument("--no-adj", action="store_true", help="disable patch-seam adjacency tie-break")
    ap.add_argument("--emit-vmap", action="store_true")
    args = ap.parse_args()

    m = json.load(open(f"{ROOT}/out/maps/{args.target}.json"))
    real = tilegrid.tokenize(m)

    names = patchmodel.all_map_names()
    if args.phase == "A":
        inv_maps = [args.target]                       # jigsaw of the target's own patches
    else:
        inv_maps = [n for n in names if n != args.target]
    print(f"Phase {args.phase}: patch inventory from {len(inv_maps)} map(s) "
          f"(target {'IN' if args.phase=='A' else 'OUT'}), adj={not args.no_adj}")

    inv = patchmodel.build_inventory(inv_maps)
    print(f"inventory: {len(inv['patches'])} patches, {len(inv['bucket'])} terrain-border buckets")

    gen = patchmodel.synthesize(real, inv, use_adj=not args.no_adj)

    gen_grid = {"H": real["H"], "W": real["W"], "levels": real["levels"],
                "terrain": real["terrain"], "obj": gen}
    acc = tilegrid.accuracy(gen_grid, real)
    print(f"per-tile  terrain_acc={acc['terrain_acc']}  obj_acc={acc['obj_acc']}  "
          f"obj_acc_gameplay={acc['obj_acc_gameplay']}")
    print(f"real_obj_cells={acc['real_obj_cells']}  gen_obj_cells={acc['gen_obj_cells']}")
    print("per-purpose F1 (gameplay):")
    for p in sorted(acc["per_purpose"], key=lambda k: -acc["per_purpose"][k]["f1"]):
        if p in tilegrid.GAMEPLAY:
            v = acc["per_purpose"][p]
            print(f"  {p:16s} P={v['precision']:.2f} R={v['recall']:.2f} F1={v['f1']:.2f}  "
                  f"(tp={v['tp']} fp={v['fp']} fn={v['fn']})")

    safe = args.target.replace(" ", "_")
    out = f"{ROOT}/out/render/patchrecon_{safe}_phase{args.phase}.png"
    recon.compose(real, gen, out, f"PATCH(P{args.phase})")
    print("rendered:", out)

    if args.emit_vmap:
        vpath = f"{ROOT}/out/PatchRecon-{safe}-phase{args.phase}.vmap"
        _, nobj = recon.detokenize_to_vmap(m, gen, vpath, f"PatchRecon {args.target} (P{args.phase})")
        print(f"vmap: {vpath}  ({nobj} objects)")


if __name__ == "__main__":
    main()
