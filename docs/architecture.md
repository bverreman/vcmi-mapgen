# Technical Direction & Architecture

This document is the spine: *what* the engine is, the *single idea* behind it, and the
*guarantee* it rests on. Read this first.

## 1. The problem, restated

We want to reproduce and adapt hand-made VCMI / Heroes 3 maps. Earlier directions tried to
*predict* object placement from terrain (probabilistic energy fields, DL heatmaps, archetype
scoring, a learned adjacency/lift graph). They all failed the honest test — "given the same
map, reproduce it" — because predicting the exact tile of every object from terrain alone is
information-theoretically impossible from 159 examples.

So the objective was sharpened into something **achievable**:

> Segment a map into same-terrain zones, **record** each zone's object pattern in a
> shape-relative frame, and **replay** it onto a target shape. If the target shape equals the
> shape it was recorded from, reproduce it **exactly**. A different shape gets a principled
> adaptation.

Identity is a *guarantee*, not a hope, because replay is integer-only — it falls out of the
recording when the shape is unchanged.

## 2. The machine: segment → record → replay

```
   real map (.h3m -> maps_json/<name>.json, faithful: terrain + exact-identity objects)
        │
        ▼   ① SEGMENT   terrain_segment.segment(level, subdivide=False)
   same-terrain connected zones; water/rock are barriers (zone_label = -1);
   compute_static_features[:,:,20] = interior depth (BFS distance-to-boundary)
        │
        ▼   ② RECORD    zone_engine.extract_template(name)
   per zone:  bbox / centroid / mask_rel(sorted) / shape_hash / label
   per object: { purpose, exact_identity, anchor_off=(x-minx, y-miny),
                 canon=(depth, sweep) }            barrier-anchored objects -> absolute bucket
        │
        ▼   ③ REPLAY    zone_engine.rebuild_map(template, target_terrain, identity=…)
   match each template zone to the target zone with the SAME (mask_rel, bbox)
   ├─ same shape  -> place at bbox_min + anchor_off, verbatim identity  ⇒ BIT-EXACT
   └─ larger shape -> the SAME objects at the same relative placement on a larger grid
        │
        ▼   ④ EMIT / INSPECT
   faithful.to_vmap -> editor-loadable .vmap ;  render_editor -> real 32px H3 sprites
```

## 3. The two guarantees

- **Same shape ⇒ bit-exact.** `rebuild --identity --verify` multiset-compares every object
  across both levels and prints `IDENTITY OK: N/N`. Verified 2027/2027 on *All for One*.
  Identity is **never re-rolled** (no subtype resampling), so relational portals and quest
  links survive intact.

- **Larger shape ⇒ same objects, larger grid.** VCMI objects are **fixed-size tile objects**
  — you cannot scale a sprite. So a stretch scales the *grid and object positions* by the
  bbox-affine; the sprites/footprints stay fixed and the empty space between objects grows.
  Constraints honored: rigid gameplay keeps one tile and never overlaps other gameplay;
  decoration keeps its relative spot and may overlap decoration but must not bury gameplay or
  land on a barrier; an untraversable (VCMI-invalid) result is rejected. Zone objects are
  gathered by **footprint overlap**, so a zone's edge rim of mountains and its edge mines come
  with it.

  Rejected alternatives (do not revisit): image-warp / pixel resize (violates fixed-size),
  wall-fill (adds foreign objects), coverage-stretch (does not look the same).

## 4. Rendering is part of the contract

Inspection uses **real editor sprites**, not dots — `render_editor.py` composites 32px H3
sprites from the local LOD files. Its `_decode_frame` handles all four H3 DEF formats; format
3 (one uint16 offset per 32-px block, row-major) is the subtle one — getting it wrong mangles
every mountain/town/monster. `vcmi_mapgen/test_render_editor.py` guards this: every format
decodes to its header dimensions with real content, all terrain tiles decode, a corpus-wide
sprite sweep passes, the renderer is deterministic, and a golden check confirms the rebuilt
map renders **pixel-identical** to the source (the identity guarantee at the pixel level).

## 5. Where the pieces live

| Stage | Module(s) |
|---|---|
| Faithful data pipeline | `h3m.py`, `vcmi_ids.py`, `h3m2vmap.py`, `extract_faithful.py` → `maps_json/` |
| Segment | `terrain_segment.py` |
| Record / replay / stretch | `zone_engine.py` (the CLI), `obj_resolve.py`, `ontology.py` |
| Emit | `faithful.py`, `vmapwrite.py`, `traverse.py` |
| Render & inspect | `render_editor.py`, `render.py`, `test_render_editor.py` |
| Terrain generation | `markov_terrain.py` (Markov chain learned from the corpus) |

The `.h3m` corpus is in `maps/`; the format reference is in `vcmi-h3m-format-reference/`
(notes: [vcmi-h3m-format-reference.md](vcmi-h3m-format-reference.md)); transient outputs go to
the gitignored `out/`.

## 6. The frontier

The foundation (segment, record, bit-exact replay, editor-quality rendering) is done and
verified. Open levers: warping to an arbitrary outline (not just a rectangular stretch — an
oriented-bbox / depth-sweep shape coordinate); running across every zone of every corpus map;
and bridging the recorded zones onto genuinely new generated terrain.
