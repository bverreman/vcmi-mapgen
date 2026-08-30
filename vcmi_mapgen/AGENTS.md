# VCMI maps — domain & the zone engine

## Formats & identifiers

- `.h3m` (real maps): gzip binary, parsed by `vcmi_mapgen/h3m.py` (`parse_file` → `H3Map`
  with terrain tiles + objects; RoE/AB/SoD only). `.vmap` (editor): zip of relaxed JSON,
  written by `vcmi_mapgen/faithful.py` + `vcmi_mapgen/vmapwrite.py`.
- **Faithful JSON** (`maps_json/<name>.json`, loaded by `obj_resolve.load_faithful`) is the
  engine's input: writer-ready terrain cells `{t,view,rt,rd,ot,od,m}` + objects carrying
  their EXACT `{type, subtype, animation, mask}`. Regenerate from `maps/` with
  `python -m vcmi_mapgen.extract_faithful`.
- Object identity comes from VCMI's own config via `vcmi_mapgen/vcmi_ids.py`
  (`resolve(obj_class, obj_subid) → (type, subtype)`). Never guess subtypes. The reference
  C++ format sources are in `vcmi-h3m-format-reference/`.
- A visitable object's template needs `visitableFrom` (the 3×3 approach grid) or the editor
  warns "no visitable directions" — `faithful.to_vmap` sets it.
- Footprints: `obj_resolve.mask_cells(mask, x, y)` expands a mask anchored at its
  bottom-right cell; `'B'` = blocking, `'A'`/`'V'` = visitable/overlay, `' '` = empty.

## Segmentation

- `terrain_segment.segment(level, subdivide=False)` → `(zones, zone_label)`: 4-conn
  flood-fill by terrain type. Use `subdivide=False` — "sections of the same terrain".
  Water(8)/rock(9) are barriers (`zone_label = -1`). `compute_static_features(...)[:,:,20]`
  is the normalized BFS distance-to-boundary (interior depth).

## The zone template + identity guarantee

- `zone_engine.extract_template(name)` records, per zone, `bbox / centroid /
  mask_rel(sorted) / shape_hash / label` and per object `{purpose, identity, anchor_off,
  canon(depth,sweep)}`. Barrier-anchored objects go to a per-level absolute bucket.
- `rebuild_map(template, target_terrain, identity=True)` matches each template zone to the
  target zone with the same `(mask_rel, bbox)` and replays objects at `bbox_min + anchor_off`
  — **pure integer ⇒ bit-exact when the shape is unchanged.** `rebuild --identity --verify`
  multiset-compares all levels and prints `IDENTITY OK: N/N`. Identity is **never re-rolled**
  (no `pick_variant`) so relational portals survive.

## Stretch (different shape)

- **Stretch = the same objects at the same relative placement on a LARGER tile grid.** VCMI
  objects are fixed-size tile objects, so positions scale by the bbox-affine and footprints
  do NOT. Rigid gameplay = one tile, no overlap (snap to a free zone tile); decoration keeps
  its relative spot, may overlap decoration but must NOT bury gameplay or sit on a barrier; a
  VCMI-invalid (untraversable) result is rejected. Zone objects are gathered by **footprint
  overlap** (so the edge rim of mountains and edge mines come with the zone). `reconstruct
  "<name>" --zone N --deform [--fx --fy]` renders ORIGINAL | same-shape | stretched.
- Do NOT redo the rejected attempts: image-warp/pixel-resize (violates fixed-size),
  wall-fill (adds foreign objects), coverage-stretch (does not look the same).

## Rendering (editor-quality)

- `render_editor.py` composites real 32px H3 sprites from the local LOD files. `_decode_frame`
  handles all four H3 DEF formats: 0 (raw), 1 (per-line RLE), 2 (per-line typed RLE),
  **3 (one uint16 offset per 32-px block, row-major)** — getting format 3 wrong mangles every
  mountain/town/monster, so it is the key thing the tests guard.
- `vcmi_mapgen/render_editor_test.py` is the reliability suite: every DEF format decodes to its
  header dimensions and non-empty content, all terrain tiles decode, a corpus-wide sprite
  decode sweep, renderer determinism, and a golden **rebuilt == source** pixel-identical check.
  Run `uv run pytest`; tests skip when the H3 LOD files are absent.
