"""Generate an abstract dependency tree that matches the corpus shape measured in
out/deps.json. This is the SKELETON -- the macro form the map is built around --
produced before any terrain or object placement. The spatial step (deps_embed.py)
then realizes it: zones for nodes, barriers + a guard at each parent->child edge,
portals as cross-edges.

Node  = a region/zone: {id, depth, parent, value (reward budget), size_class}
Edge  = the gate you cross to reach a child: {type, strength}
Plus a few portal cross-edges linking distant branches.
"""

import sys, os, json, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _interp_strength(pcts, rnd):
    """Sample a guard strength from a {p10,p50,p90} summary by linear interp."""
    if not pcts:
        return 0
    p10, p50, p90 = pcts.get("p10", 0), pcts.get("p50", 0), pcts.get("p90", 0)
    u = rnd.random()
    if u < 0.5:
        return int(p10 + (p50 - p10) * (u / 0.5))
    return int(p50 + (p90 - p50) * ((u - 0.5) / 0.5))


def generate_tree(
    W,
    H,
    levels=1,
    seed=0,
    deps_path=None,
    n_target=None,
    max_depth=None,
    portal_frac=None,
):
    """Overrides (n_target / max_depth / portal_frac) let a caller dial the tree
    toward a specific target map; defaults come from the corpus distribution."""
    rnd = random.Random(seed)
    D = json.load(open(deps_path or f"{ROOT}/out/deps.json"))
    area = W * H * levels

    if n_target is None:
        n_target = max(4, round(area / 1000.0 * D["regions_per_1000tiles"]["p50"]))
    if max_depth is None:
        max_depth = D["max_depth"]["p50"]
    if portal_frac is None:
        portal_frac = D["gate_type_mix"].get("portal", 0.1)
    branch = {int(k): v for k, v in D["branching_by_depth"].items()}
    grad = {int(k): v for k, v in D["gradient_by_depth"].items()}

    nodes = [
        {
            "id": 0,
            "depth": 0,
            "parent": None,
            "gate": None,
            "value": 0,
            "size_class": "commons",
        }
    ]
    edges = []

    def _spawn(par, cd):
        """Append one child node of `par` at depth `cd`; return its id."""
        cid = len(nodes)
        g = grad.get(cd, grad.get(max(grad), {}))
        is_portal = rnd.random() < portal_frac
        gate = {
            "type": "portal" if is_portal else "guard",
            "strength": 0 if is_portal else _interp_strength(g.get("guard_strength", {}), rnd),
        }
        val = max(1, int(rnd.expovariate(1.0 / max(1.0, g.get("mean_child_value", 10)))))
        nodes.append(
            {
                "id": cid,
                "depth": cd,
                "parent": par,
                "gate": gate,
                "value": val,
                "size_class": "pocket",
            }
        )
        edges.append({"a": par, "b": cid, **gate})
        return cid

    # grow the tree breadth-first; each node spawns ~branching_by_depth[depth] kids
    frontier = [0]
    while frontier and len(nodes) < n_target:
        nxt = []
        for par in frontier:
            d = nodes[par]["depth"]
            if d >= max_depth:
                continue
            mean_kids = branch.get(d, branch.get(max(branch), 1.5))
            # Poisson-ish small integer around the mean
            k = int(mean_kids) + (1 if rnd.random() < (mean_kids - int(mean_kids)) else 0)
            if d > 0:
                k = min(k, 2)  # deeper chains stay narrow
            for _ in range(k):
                if len(nodes) >= n_target:
                    break
                nxt.append(_spawn(par, d + 1))
        frontier = nxt

    # Breadth-first fill exhausts the node budget on shallow levels before it can
    # descend, capping the tree at depth ~4-5 vs the 5-7 of deep corpus maps and
    # inflating the struct distance. When BFS falls short, extend the single deepest
    # leaf into a thin "main quest" chain down to max_depth. This is a no-op when the
    # target depth is already reached (so shallow maps are unchanged), and only ever
    # appends a 1-wide chain off one leaf -- minimal reshaping, so the embedding keeps
    # each new node adjacent to its parent (a bushy root spine seals zones instead).
    cur_depth = max(n["depth"] for n in nodes)
    if cur_depth < max_depth:
        leaf = max(nodes, key=lambda n: n["depth"])["id"]
        for cd in range(cur_depth + 1, max_depth + 1):
            leaf = _spawn(leaf, cd)

    # a couple of portal cross-edges linking deep nodes on different branches
    deep = [n for n in nodes if n["depth"] >= 2]
    rnd.shuffle(deep)
    n_cross = max(0, round(len(edges) * portal_frac * 0.3))
    used = set()
    for i in range(min(n_cross, len(deep) // 2)):
        a, b = deep[2 * i]["id"], deep[2 * i + 1]["id"]
        if (a, b) in used:
            continue
        used.add((a, b))
        edges.append({"a": a, "b": b, "type": "portal", "strength": 0, "cross": True})

    tree = {
        "W": W,
        "H": H,
        "levels": levels,
        "seed": seed,
        "n_nodes": len(nodes),
        "max_depth": max(n["depth"] for n in nodes),
        "nodes": nodes,
        "edges": edges,
    }
    return tree


def describe(tree):
    import collections

    bydepth = collections.Counter(n["depth"] for n in tree["nodes"])
    gt = collections.Counter(e["type"] for e in tree["edges"])
    print(
        f"tree {tree['W']}x{tree['H']}x{tree['levels']} seed={tree['seed']}: "
        f"{tree['n_nodes']} nodes, max_depth={tree['max_depth']}, edges {dict(gt)}"
    )
    for d in sorted(bydepth):
        kids = [n for n in tree["nodes"] if n["depth"] == d]
        gs = [n["gate"]["strength"] for n in kids if n["gate"] and n["gate"]["type"] == "guard"]
        mg = sum(gs) // len(gs) if gs else 0
        print(
            f"  depth {d}: {bydepth[d]:2d} nodes  mean_guard={mg:4d}  "
            f"value={sum(n['value'] for n in kids)}"
        )


if __name__ == "__main__":
    W = int(sys.argv[1]) if len(sys.argv) > 1 else 72
    H = int(sys.argv[2]) if len(sys.argv) > 2 else 72
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    t = generate_tree(W, H, seed=seed)
    describe(t)
    out = f"{ROOT}/out/tree.json"
    json.dump(t, open(out, "w"), indent=1)
    print("wrote", out)
