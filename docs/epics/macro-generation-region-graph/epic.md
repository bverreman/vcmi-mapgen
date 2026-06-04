# Epic: Macro Generation — Region Graph & Structure

## Roadmap Source

MVP roadmap: Epic 4, "Macro Generation: Region Graph & Structure" in `docs/roadmaps/mvp.md`.

## Purpose

Produce the macro skeleton — region layout, connectivity, towns, roads, and relational connectors (portals/gates) — with the *non-linear, loopy* character of human maps, explicitly avoiding the RMG's partition-first artifacts (clean-cut zones, tree-like topology, portals used as universal glue).

## Outcome

A generated region-adjacency graph with human-like topology (multiple cycles, redundant routes, fuzzy boundaries, connection-type mix dominated by terrain adjacency), towns placed as anchors, a road network connecting them, and portals/subterranean gates placed at human rates as deliberate relational links — not as the default connector between every region.

## Current State

Only a heuristic placeholder exists from spike work: towns placed on spaced land, connected by an MST of A\*-routed **dirt** paths. There is no learned region graph, no connection-type model, and portals are not placed relationally. This epic builds the real macro layer.

## Scope

- Extract the region-adjacency graph + macro metrics from human maps: region count/size, adjacency, independent-cycle count, connection-type mix, portal frequency (capturing its bimodality).
- Region-graph sampler: generate a topology matching the human cycle-count and connection-type distributions (growth/graph-first, not Voronoi partition).
- Embed the graph into 2D and grow regions with fuzzy boundaries.
- Town/zone anchor placement.
- Road network as overlay (consumes Epic 3's road encoding), connecting regions primarily by terrain adjacency.
- Relational connectors: place two-way monoliths (by channel/pair), subterranean gates, whirlpools at learned rates and as genuine shortcuts.

## Out of Scope

- Per-object gameplay placement inside regions (Epic 5).
- Decoration/terrain texture (Epic 3).
- Campaign/scripted events.

## Key Decisions

- Generation is graph-first/growth-based, not partition-first — partition-first is the root cause of linearity, clean cuts, and portal overuse.
- Portals are sampled from the human frequency distribution (low, bimodal), demoting them from "universal connector" to rare deliberate feature.
- Connectivity is redundant (loops), not a balanced tree.

## Story Breakdown

- **Story name**: Extract Region Graph & Macro Metrics
  **Short goal**: Per-map region graph + cycle count + connection-type mix + portal frequency.
  **Primary value**: The macro targets and the macro half of the critic.
  **Dependencies**: Epic 1 corpus.
  **Suggested story file name**: `01-extract-region-graph.md`
- **Story name**: Region-Graph Sampler
  **Short goal**: Sample a topology with human-like loops and connection mix.
  **Primary value**: Non-linear structure by construction.
  **Dependencies**: Region-graph metrics.
  **Suggested story file name**: `02-region-graph-sampler.md`
- **Story name**: Embed & Grow Regions
  **Short goal**: Place the graph in 2D; grow fuzzy-bordered regions.
  **Primary value**: Organic, non-clean-cut zones.
  **Dependencies**: Sampler; Epic 3 terrain.
  **Suggested story file name**: `03-embed-and-grow-regions.md`
- **Story name**: Town & Road Network
  **Short goal**: Anchor towns; connect via terrain-first roads (overlay).
  **Primary value**: A traversable map.
  **Dependencies**: Regions; Epic 3 road overlay.
  **Suggested story file name**: `04-town-and-road-network.md`
- **Story name**: Relational Connectors at Human Rates
  **Short goal**: Place portals/gates/whirlpools by pair and at learned frequency.
  **Primary value**: Portals as shortcuts, not glue.
  **Dependencies**: Epic 2 marginals; relational ontology.
  **Suggested story file name**: `05-relational-connectors.md`

## Acceptance Criteria

- [ ] Generated maps' region-graph metrics (cycle count, connection-type mix, portal frequency) fall within the human distribution, not the RMG distribution.
- [ ] Region boundaries are fuzzy (gradient transitions), not single-seam partitions.
- [ ] Portals appear at the human rate (often zero), paired by channel, and connect distant regions.
- [ ] Towns are reachable and connected by an overlay road network.

## Dependencies

Epic 1 (corpus), Epic 2 (marginals, relational ontology), Epic 3 (terrain + road overlay). Provides the scaffold Epic 5 needs.

## Risks

- Region-graph metrics hard to define unambiguously → pin the extraction definition first (Story 1) and validate against eyeballed maps.
- Embedding a loopy graph without falling back into Voronoi partition → grow regions, don't cut them.

## Verification Strategy

Compare generated macro metrics against the human and RMG distributions on the same axes; success is landing on the human side. Visually confirm loops, fuzzy borders, and sparse purposeful portals in the editor.

## Notes for AI Agents

The whole point is to *not* reproduce the RMG. If a design choice resembles "cut the map into zones then connect them with portals", it is wrong. Measure linearity and portal-as-connector explicitly and drive them toward the human distribution.
