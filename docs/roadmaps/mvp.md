# VCMI Map Generation — Roadmap

## Goal

Generate VCMI (Heroes of Might & Magic 3) maps that feel **hand-made**, by learning the structure of ~159 real human-authored maps rather than hand-coding heuristics. Generated maps must open in the VCMI map editor and match the statistical "fingerprint" of human maps where it matters: terrain texture, object composition, terrain affinity, object clustering/spacing, and macro connectivity.

## Read this first

The high-level technical direction, the single generative-model idea behind every epic, and the assumptions it rests on are in **`docs/architecture.md`**. The epic table below is the breakdown; `architecture.md` is the machine they form. In one line: **we learn a *factored generative model* of maps from the 159 examples — `P(map) = P(M)·P(T|M)·P(O|T,M)` — and generate by sampling it; the epics are the factors and the pipeline stages that estimate and sample them.**

## Core thesis (validated)

A single model cannot do this. Maps decompose into two scales that need different engines:

- **Micro** — terrain texture and decoration. Dense, local, texture-like. A learned local model (Markov / MRF, terrain affinity, pairwise lift) is the right engine.
- **Macro** — region layout, connectivity, towns, portals, purpose-justified gameplay objects. Sparse, semantic, long-range. Needs a region-graph scaffold plus a small authored ontology of hard constraints the data cannot teach.

Empirically proven during spike work: a pure local Markov chain produces realistic *local* terrain texture but cannot carry *global* structure (raster ordering streaks; Gibbs drifts to self-reinforcing terrain). The architecture is therefore **macro scaffold + micro texture**, consistently, for both terrain and objects.

## Source corpus

- 159 `.h3m` maps (48 RoE, 55 AB, 56 SoD; no HotA) in `~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps`.
- 4 VCMI RMG `.vmap` maps used as the *contrastive negative* (what generated maps should NOT look like).
- 378,699 parsed objects; 73.7% decoration, 26.3% gameplay.

## Epics

Each epic is a stage of the pipeline in `architecture.md` (represent → estimate → sample → score). The "Role in the model" column is the linkage.

| # | Epic | Role in the model | Outcome | Status |
|---|---|---|---|---|
| 1 | **Corpus Extraction & Object Ontology** | ① Represent — define the variables | Parse every map into a normalized representation; resolve every object to identity + purpose | Largely done |
| 2 | **Statistical Baselines & Contrastive Analysis** | ② Estimate — fit every factor (N, U, Φ, macro metrics, Markov tables) | The learned tables that drive generation; quantified human-vs-RMG gap | Partly done |
| 4 | **Macro Generation: Region Graph & Structure** | ③ Sample **M** | Non-linear, loopy region topology with towns, roads, and relational connectors | Not started |
| 3 | **Micro Generation: Terrain & Decoration** | ③ Sample **T** (+ decoration part of O) | Data-driven terrain + decoration matching human texture, density, affinity, clustering | In progress |
| 5 | **Gameplay Object Placement (Purpose-Driven)** | ③ Sample **O \| T, M** | Resources/guards/banks/quests placed by budget, affinity, spacing, guard↔reward | Not started |
| 6 | **Validation & Quality Harness** | ④ Score & refine — likelihood under P | Render, editor round-trip, and a statistical/discriminative critic | Partly done |

## Sequencing

Foundation order: **1 → 2** (you must represent the variables before you can estimate the factors). Generation order is the ancestral-sampling order **M → T → O**, i.e. **4 → 3 → 5**, each reading Epic 2's estimated factors. The *same* pairwise lift **Φ** drives both decoration clustering (3) and guard↔reward coupling (5) — that shared factor is the literal link between the epics. **6** runs continuously: every generation milestone is scored against P and its deviations feed back into 2.

**Current keystone:** the pairwise lift **Φ** (Epic 2) is the one unbuilt factor and unblocks both 3 and 5.

## Standing constraints for all work

- **The only ground-truth quality signal is visual inspection in the VCMI editor.** Every generation milestone must produce an editor-openable `.vmap` plus a schematic render.
- **Learn from data; never hand-roll a heuristic where a table can be measured.** Defaulting unknowns to a bucket is a generation bug (it leaked visitable objects into decoration once).
- **The few hard "nevers" are authored, not learned** — data has zero examples of them (e.g. a return-portal beside its own town).
