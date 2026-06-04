# Story: Roads as Overlay Tiles

## Epic

Micro Generation — Terrain & Decoration: `docs/epics/micro-generation-terrain-decoration/epic.md`

## Implementation Status

- **Status**: To Do
- **Priority**: Medium
- **Plan**: TBD
- **Verification**: Open the generated `.vmap` in the editor; confirm roads render as road sprites on top of unchanged terrain.

## User / Operator Value

- end result: towns connected by real roads drawn on top of the terrain, the way human maps do it — not by overwriting terrain with dirt.
- developer / AI agent: a correct road-overlay encoder reusable by macro generation (Epic 4).

## Context

The current generator (`src/generate3.py`, `carve_roads`) connects towns by overwriting path tiles with the dirt terrain type. The user pointed out this is wrong: in VCMI, roads are an **overlay** layer (a `roadType`/`roadDir` per tile, rendered as road sprites on top of whatever terrain is there), analogous to rivers. The RMG `.vmap` corpus contains no roads, so the overlay tile-string encoding must come from VCMI's terrain/road config rather than being copied from a sample. The vmap terrain string carries the overlay as a suffix on the tile (the same place rivers appear, e.g. a `_ri…` suffix).

## Scope

- Determine the VCMI road-overlay encoding for a vmap terrain tile string (road type + road direction view), from VCMI terrain/road config.
- Add a road-overlay writer: given a set of road tiles and their connectivity, set the correct `roadType`/`roadDir` so the editor renders a continuous road.
- Update road generation to keep terrain unchanged and write the overlay instead of carving dirt.
- Pick the road direction/view per tile from its road neighbours so segments connect visually (corners, straights, ends).

## Out of Scope

- The routing algorithm itself (A\* over passable terrain already exists) — only the rendering layer changes.
- Rivers (out of scope unless trivially shared with the road encoder).
- Macro road-network topology (Epic 4) — this story provides the encoding it will reuse.

## Requirements

- Roads must render as overlay sprites on top of the original terrain; the underlying terrain type must be unchanged.
- A generated road must be visually continuous (correct direction/view per tile) when opened in the editor.
- The road tile-string encoding must be validated by opening the `.vmap` in the VCMI editor, not assumed.
- The encoder must be a reusable function consumed by both micro (this epic) and macro (Epic 4) road generation.

## Open Questions

- Exact suffix format and road-view indexing in the vmap tile string — resolve from VCMI terrain/road config before coding.
- Whether road direction/view selection can reuse the same adjacency logic planned for terrain edge-views.
