---
name: vcmi-mapgen
description: "VCMI map-generator repo root — what the project is, uv tooling, the package layout, and how to run the zone engine / terrain generator / tests. Load first for any work in this repo."
metadata:
  generated_by: farrier
  source: library/skills/vcmi-mapgen/vcmi-mapgen/SKILL.md
  do_not_edit: "edit the source in the central prompt library and re-run `make agent-install` to regenerate"
---

# VCMI map-generator — repository root

A shape-driven **zone-rebuilding engine** for VCMI / Heroes 3 maps, plus a learned
terrain generator. Given a real map it segments same-terrain zones, records each zone's
object pattern in a shape-relative frame, then *replays* it onto a target shape:

- **Same shape ⇒ bit-exact reproduction** (integer-only replay; verified 2027/2027
  objects on *All for One*).
- **Larger shape ⇒ the same objects at the same relative placement on a larger tile
  grid** — VCMI objects are fixed-size tile objects, so the *grid/positions* scale, the
  sprites do NOT. No illegal overlaps, gameplay stays reachable. (Image-warp/pixel
  scaling was tried and rejected — it violates the fixed-size constraint.)

Load `vcmi-mapgen-maps` for the domain details (formats, segmentation, rendering).

## Tooling — this is a `uv` Python project

- Run **everything** through uv as a module: `uv run python -m vcmi_mapgen.<module> [...]`.
  Dependencies (`pyproject.toml`): Pillow + numpy; everything else is stdlib. Never
  `pip install`; never assume a system interpreter — `uv run` resolves the env.
- Determinism: replay is integer-only; the terrain generator is seeded. Don't introduce
  `random`/time without a seed.

## Where things are

- **`vcmi_mapgen/`** — the package (run modules with `python -m vcmi_mapgen.<name>`):
  - `zone_engine.py` — the CLI (`extract` / `inspect` / `features` / `reconstruct` /
    `rebuild` / `run`).
  - `terrain_segment.py` — same-terrain flood-fill segmentation + interior-depth features.
  - `obj_resolve.py`, `ontology.py` — faithful-map loader, object identity, purpose.
  - `faithful.py`, `vmapwrite.py`, `traverse.py` — faithful map dict → editor `.vmap`.
  - `render_editor.py` — editor-quality 32px H3 sprite rendering (decodes DEF fmt 0/1/2/3);
    `render.py` — schematic PNGs.
  - `markov_terrain.py` — the terrain generator (Markov chain learned from the corpus).
  - `h3m.py`, `vcmi_ids.py`, `h3m2vmap.py`, `extract_faithful.py` — `.h3m` → faithful pipeline.
  - `test_render_editor.py` — rendering-engine reliability tests.
- **`maps/`** — the `.h3m` corpus (159 maps), the source data.
- **`maps_json/`** — faithful JSON per map (the engine's input; regenerable from `maps/`).
- **`data/`** — corpus-derived priors (`objlib.json`).
- **`out/`** — transient outputs (templates, features, renders); **gitignored**.
- **`vcmi-h3m-format-reference/`** — verbatim VCMI C++ sources documenting the `.h3m` format
  (see `docs/vcmi-h3m-format-reference.md`).

## How to run

```bash
uv run python -m vcmi_mapgen.zone_engine run "All for One"      # full foundation pipeline
uv run python -m vcmi_mapgen.zone_engine rebuild "All for One" --identity --verify
uv run python -m vcmi_mapgen.zone_engine reconstruct "All for One" --zone 7 --deform
uv run python -m vcmi_mapgen.markov_terrain                     # learned terrain generator
uv run python -m vcmi_mapgen.extract_faithful                  # regenerate maps_json/ from maps/
uv run pytest                                                  # rendering-engine reliability tests
```

Editor-quality rendering reads the H3 sprite LOD files from a local VCMI install
(`~/.var/app/eu.vcmi.VCMI/data/vcmi/Data`); the rendering tests skip when those are absent.

## Rules

- **`ontology.py` is the SINGLE SOURCE OF TRUTH for objects, from tile placement through map
  rendering.** Object identity, footprint mask, terrain coupling and decoration category for the
  *generation* pipeline come from `ontology.py` — the hardcoded `TAXONOMY` + `LEAF_META` literals,
  re-derived from the authoritative editor table `objects.txt` via `python -m vcmi_mapgen.ontology
  --regen`. Use its accessors (`identity_of`, `mask_of`, `is_blocking`, `terrains_of`, `decor_pool`,
  `veg_categories`, `category_of`, `decode_identity`, `category_terrain_matrix`). When something the
  pipeline needs is missing, **extend the ontology** (parse it from `objects.txt` and regenerate) —
  do NOT reach into the corpus (`data/objlib.json` / `obj_resolve._OBJLIB`, faithful maps, or a
  `veg_data` corpus scan) for object identity/mask/category. The corpus may still inform spatial
  *statistics* (density/openness/frequency weights), never identity. `veg_data`'s category functions
  are thin ontology adapters; the extract/`rebuild --identity` corpus-replay path is separate and
  unaffected.
- The **same-shape identity guarantee is bit-exact** — `rebuild --identity --verify` must
  print `IDENTITY OK` and never re-roll object identity (no `pick_variant`), so relational
  portals/quest links survive.
- Generated artifacts live in `out/` (gitignored). Do **NOT** copy them into the VCMI
  `Maps/` folder.
- Treat `CLAUDE.md` and `.claude/` as generated adapter outputs — edit the canonical skill
  sources and re-run `make agent-install`, never hand-edit them.
