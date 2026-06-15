# vcmi-mapgen

A shape-driven **zone-rebuilding engine** for VCMI / Heroes 3 maps, plus a learned
terrain generator. Given a real map, it segments it into same-terrain zones, records
each zone's object pattern in a shape-relative frame, and *replays* it onto a target
shape:

- **Same shape ⇒ bit-exact reproduction** (integer-only replay; verified 2027/2027
  objects on *All for One*).
- **Larger shape ⇒ the same objects at the same relative placement on a larger tile
  grid**, respecting VCMI constraints (fixed-size tile objects, no illegal overlaps,
  gameplay stays reachable).

## Layout

```
vcmi_mapgen/        the Python package (engine + renderer + data pipeline)
  zone_engine.py      the CLI: extract / inspect / features / reconstruct / rebuild / run
  terrain_segment.py  same-terrain flood-fill segmentation + interior-depth features
  obj_resolve.py      faithful-map loader, object identity, footprint mask expansion
  ontology.py         (class,subclass) -> purpose / relational / terrain-coupled
  faithful.py         faithful map dict -> editor-valid .vmap, and JSON save
  render_editor.py    editor-quality 32px H3 sprite rendering (decodes DEF fmt 0/1/2/3)
  render.py           schematic terrain/purpose PNGs
  markov_terrain.py   terrain generator: Markov chain learned from the corpus
  h3m.py, vcmi_ids.py, h3m2vmap.py, extract_faithful.py   .h3m -> faithful JSON pipeline
  vmapwrite.py, traverse.py                               .vmap writer + reachability
  test_render_editor.py   rendering-reliability tests (sprite decode + golden identity)
maps/               the .h3m corpus (159 real maps) — source data
maps_json/          faithful JSON per map (the engine's input; regenerable from maps/)
data/               corpus-derived priors (objlib.json)
docs/               specs, architecture, and the VCMI H3M format reference notes
vcmi-h3m-format-reference/   verbatim VCMI C++ sources documenting the .h3m format
out/                transient outputs (templates, features, renders) — gitignored
```

## Usage (uv project — run everything through `uv`)

```bash
# Foundation pipeline: extract -> segmentation -> identity rebuild + verify -> editor render
uv run python -m vcmi_mapgen.zone_engine run "All for One"

# Individual steps
uv run python -m vcmi_mapgen.zone_engine extract "All for One"
uv run python -m vcmi_mapgen.zone_engine inspect "All for One"
uv run python -m vcmi_mapgen.zone_engine rebuild "All for One" --identity --verify
uv run python -m vcmi_mapgen.zone_engine reconstruct "All for One" --zone 7 --deform

# Learned terrain generator
uv run python -m vcmi_mapgen.markov_terrain

# Regenerate maps_json/ from the .h3m corpus
uv run python -m vcmi_mapgen.extract_faithful

# Tests (rendering-engine reliability)
uv run pytest
```

Editor-quality rendering reads the H3 sprite LOD files from a local VCMI install
(`~/.var/app/eu.vcmi.VCMI/data/vcmi/Data`). The rendering tests are skipped when those
files are absent.
