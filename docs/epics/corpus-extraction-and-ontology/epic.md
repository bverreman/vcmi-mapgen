# Epic: Corpus Extraction & Object Ontology

## Roadmap Source

MVP roadmap: Epic 1, "Corpus Extraction & Object Ontology" in `docs/roadmaps/mvp.md`.

## Purpose

Turn the binary `.h3m` corpus and VCMI `.vmap` files into a single normalized, lifted representation — terrain grids plus objects resolved to identity, purpose, and relationships — because every measurement and every generator downstream depends on it. This is the "lift each map into the right space" step that is 80% of the project's value.

## Outcome

Any of the 159 human maps (and the RMG `.vmap` maps) can be loaded into a uniform JSON shape; every object resolves to `{name, subtype, purpose, relational, terrain_coupled}`; any map (real or generated) can be rendered to a schematic PNG and written back to an editor-openable `.vmap`.

## Current State

Implemented and validated. `src/h3m.py` parses RoE/AB/SoD with 159/159 clean-EOF. `src/extract.py` writes `out/maps/*.json`. `src/ontology.py` resolves class/subclass at 100% coverage. `src/render.py`, `src/vmaplib.py` (read), and `src/vmapwrite.py` (write, round-trip validated on a 144×144 two-level map) complete the I/O loop.

## Scope

- Binary `.h3m` parser for RoE/AB/SoD using the VCMI loader as reference, validated by the clean-EOF oracle.
- Normalized map schema: header, per-level terrain grid (`{t, river, road}`), object list (`x, y, l, class, subclass, animation, footprint`).
- Object ontology: numeric class/subclass → human name, resolved subtype (resource/mine/faction), purpose, relational flag + key, terrain-coupled flag.
- Schematic renderer (terrain grid + objects coloured by purpose).
- `.vmap` reader and writer (round-trip faithful, editor-openable).

## Out of Scope

- HotA map format (absent from the corpus).
- Full gameplay-body decoding beyond what is needed to walk the object list and identify objects.
- Authoritative terrain/visitability from VCMI config (the mask `A`-marker proxy is sufficient).

## Key Decisions

- Parse `.h3m` directly rather than GUI-convert to `.vmap` (no headless converter exists).
- Object identity key = numeric class/subclass (+ animation `.def`); human names come from VCMI's `MapObjectID` enum.
- Decoration vs visitable is decided by the object mask: a visitable object's mask contains an `A` tile; pure decoration has only `B`/`V`. (Do **not** default unknown types to decoration.)

## Story Breakdown

- **Story name**: Parse H3M Corpus (RoE/AB/SoD)
  **Short goal**: Walk every map to clean EOF; emit normalized JSON.
  **Primary value**: The data foundation for everything.
  **Dependencies**: VCMI loader reference.
  **Suggested story file name**: `01-parse-h3m-corpus.md`
- **Story name**: Object Identity & Purpose Ontology
  **Short goal**: Resolve every object to name/subtype/purpose/relational/terrain-coupled.
  **Primary value**: Lets measurement and placement reason about *what* an object is *for*.
  **Dependencies**: Parsed corpus; `MapObjectID` enum.
  **Suggested story file name**: `02-object-ontology.md`
- **Story name**: Schematic Renderer
  **Short goal**: Render any map as a terrain+purpose PNG.
  **Primary value**: The agent's eyes for comparison.
  **Dependencies**: Normalized schema, ontology.
  **Suggested story file name**: `03-schematic-renderer.md`
- **Story name**: VMap Reader
  **Short goal**: Load RMG `.vmap` into the normalized shape with purposes pre-resolved.
  **Primary value**: Brings the contrastive corpus into the same lens.
  **Dependencies**: Ontology.
  **Suggested story file name**: `04-vmap-reader.md`
- **Story name**: VMap Writer (Round-Trip)
  **Short goal**: Emit editor-openable `.vmap` from the normalized shape.
  **Primary value**: Makes generated maps viewable in the editor.
  **Dependencies**: A real header to clone as template.
  **Suggested story file name**: `05-vmap-writer.md`

## Acceptance Criteria

- [x] All 159 `.h3m` maps parse without exception and end at clean EOF (only zero padding remaining).
- [x] Object class/subclass resolves to a known purpose for 100% of parsed objects.
- [x] A round-tripped real `.vmap` preserves dimensions, level count, and object count exactly.
- [x] Any map renders to a schematic PNG showing terrain and purpose-coloured objects.

## Dependencies

VCMI `MapFormatH3M.cpp`/`MapFeaturesH3M.cpp` (loader reference), `EntityIdentifiers.h` (`MapObjectID` enum), PIL, Python 3.12 stdlib.

## Risks

- Binary parser desync on an unhandled object body → mitigated by the clean-EOF oracle across all 159 maps.
- Ontology gaps for rare object types → tracked by coverage %, closed by adding to the purpose table.

## Verification Strategy

Run `src/extract.py` and confirm the validation report shows 159/159 clean. Run `src/stats2.py` and confirm 100% ontology coverage. Round-trip a real `.vmap` and diff dimensions/object counts. Spot-render maps of each size and visually confirm terrain and object placement read correctly.

## Notes for AI Agents

The mask `A`-marker is the authoritative decoration/visitable discriminator — use it, never "default to decoration". Keep object identity numeric/raw in the JSON; resolve to names only through `ontology.resolve()` so the lens stays single-sourced.
