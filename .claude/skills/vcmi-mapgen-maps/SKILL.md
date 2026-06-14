---
name: vcmi-mapgen-maps
description: "VCMI maps domain — h3m/vmap formats, object identifiers, the metrics (object-distance, density, traversability), the adjacency-graph placement model, and the controls. Load when changing src/ generation or measurement code."
metadata:
  generated_by: farrier
  source: library/skills/vcmi-mapgen/maps/SKILL.md
  do_not_edit: "edit the source in the central prompt library and re-run `make agent-install` to regenerate"
---

# VCMI maps — domain & measurement

## Formats & identifiers

- `.h3m` (real maps): gzip binary, parsed by `src/h3m.py` (`parse_file` → `H3Map`
  with terrain tiles + objects; RoE/AB/SoD only). `.vmap` (editor): zip of relaxed
  JSON, written by `src/faithful.py` + `src/vmapwrite.py`.
- Object identity comes from VCMI's own config via `src/vcmi_ids.py`
  (`resolve(obj_class, obj_subid) → (type, subtype)`). Never guess subtypes.
- A visitable object's template needs `visitableFrom` (the 3×3 approach grid) or
  the editor warns "no visitable directions" — `faithful.to_vmap` sets it.

## The metrics (what "good" means, objectively)

- **object-distance** (`src/deps_spatial.py`): median nearest-neighbour distance
  per purpose pair — the *spatial arrangement*, not counts. Core quality bar: ≤ 3
  tiles, and must beat the shuffled control. This is where the adjacency graph pays.
- **density**: objects per 1000 tiles per purpose, vs corpus rate (`factors.json`
  budget); validity wants every purpose within [0.5, 2.0]×.
- **traversability** (`src/traverse.py`): BFS over passable land + chokepoints from
  the start town; every zone/town/mine must be reachable (both levels).
- **fit** (`src/deps_fit.py`): dial params from a target map, search seeds, report
  density/terrain/struct/object-distance distance.

## Adjacency-graph placement (the heart of object placement)

- `src/deps_adj.py` learns, from the corpus, an **immediate-touch** graph
  (footprints touching, Chebyshev-1), **type-specific** (oakTrees vs pineTrees vs
  mountain — never a generic DECORATION bucket), **terrain-conditioned**
  (`terrain|type` keys: a grass mine touches oakTrees; a snow mine touches
  pineTrees + deadVegetation). Output: `out/adjacency.json`.
- `deps_realize.py` GROWS objects from this graph (each placed object spawns the
  specific types that abut it, at real offsets) instead of independent scatter.
  Density is only a CAP; the graph sets the shape. Guard↔treasure coupling is
  emergent, not a hack.
- **Decoration may overlap** other decoration (even multi-tile) — only avoid
  gameplay/barrier/water tiles. This is what lets trees pack into forests.

## Controls (claims must beat these)

- **shuffled**: same generated objects, random positions (built into
  `benchmark.py`). The graph's object-distance must beat it by ≥ 1 tile.
- **histogram-only**: density matched, no graph (random scatter) — a gate the graph
  passes but histogram-only fails proves the graph is load-bearing.
