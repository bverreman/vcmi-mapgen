# Epic: Gameplay Object Placement (Purpose-Driven)

## Roadmap Source

MVP roadmap: Epic 5, "Gameplay Object Placement (Purpose-Driven)" in `docs/roadmaps/mvp.md`.

## Purpose

Place the visitable/gameplay objects — resources, mines, dwellings, rewards, guards, banks, stat/bonus buildings, quest objects — so each is *justified by its context*: on the right terrain, in the right quantity, spaced or clustered as humans do, with guards coupled to the rewards they protect, and with the rare hard "nevers" enforced by an authored rule layer.

## Outcome

Given a macro scaffold (Epic 4) and the learned tables (Epic 2), the generator places gameplay objects by purpose budget and conditional sampling: economy on suitable terrain and spaced out, rewards distributed with their guards on the access tile, dwellings and stat buildings at human density, relational quest objects paired correctly — and never produces a purpose-unjustified object (the same failure that made early decoration-on-everything look random).

## Current State

Not started. Earlier spike work either transplanted RMG gameplay objects onto regenerated terrain (nonsensical) or omitted them entirely. This epic is the principled placement engine for the 26.3% of objects that are gameplay.

## Scope

- Purpose budget sampler: how many of each type per map, sampled from Epic 2's marginal *distributions* (matching variance, including bimodality).
- Economy placement: resources/mines by terrain affinity, with learned short-range repulsion (spacing).
- Reward placement with guard↔reward coupling: place a reward, then place its guard on the access tile from the learned pairwise lift; scale guard tier to reward value.
- Dwellings, stat-permanent, bonus-temp, info, mana, spell/skill placement at human density and terrain affinity.
- Relational/quest objects: seer-hut↔target, border-guard↔keymaster by colour, placed consistently.
- Authored hard-constraint layer: the small set of "nevers" the data cannot teach (e.g. a return-portal beside its own town), evaluated against the macro graph.

## Out of Scope

- Decoration/terrain (Epic 3).
- Region topology and connectors (Epic 4) — this epic consumes that scaffold.
- Balance tuning for competitive play (correctness of *placement*, not fairness, is the MVP bar).

## Key Decisions

- Same conditional engine as decoration (affinity + lift + Gibbs), with gameplay's characteristic *repulsion* (spacing) and *cross-attraction* (guard↔reward).
- Composition is set by the purpose budget; arrangement by the conditional — never conflate the two.
- The hard "nevers" are authored, because the corpus contains zero violations and a statistical model cannot distinguish "forbidden" from "rare".

## Story Breakdown

- **Story name**: Purpose Budget Sampler
  **Short goal**: Sample per-type counts from the human marginal distributions.
  **Primary value**: Right composition, right variance.
  **Dependencies**: Epic 2 marginals.
  **Suggested story file name**: `01-purpose-budget-sampler.md`
- **Story name**: Economy Placement With Spacing
  **Short goal**: Resources/mines on affinity terrain, repelled at short range.
  **Primary value**: Believable economy distribution.
  **Dependencies**: Affinity + lift; Epic 4 regions.
  **Suggested story file name**: `02-economy-placement.md`
- **Story name**: Guard↔Reward Coupling
  **Short goal**: Place guards on reward access tiles, tier scaled to value.
  **Primary value**: Rewards feel earned, not random.
  **Dependencies**: Pairwise lift.
  **Suggested story file name**: `03-guard-reward-coupling.md`
- **Story name**: Buildings & Bonus Objects
  **Short goal**: Dwellings/stat/bonus/info/mana at human density + affinity.
  **Primary value**: A map with the human object vocabulary.
  **Dependencies**: Marginals + affinity.
  **Suggested story file name**: `04-buildings-and-bonus-objects.md`
- **Story name**: Relational & Quest Objects
  **Short goal**: Pair seer-hut/quest-guard and border-guard/keymaster correctly.
  **Primary value**: Coherent quest structure.
  **Dependencies**: Relational ontology; Epic 4 graph.
  **Suggested story file name**: `05-relational-quest-objects.md`
- **Story name**: Authored Hard-Constraint Layer
  **Short goal**: Enforce the rare "nevers" the data can't teach.
  **Primary value**: No nonsensical purpose-redundant placements.
  **Dependencies**: Macro graph; relational ontology.
  **Suggested story file name**: `06-authored-hard-constraints.md`

## Acceptance Criteria

- [ ] Per-type gameplay counts match the human marginal distribution (mean and spread).
- [ ] Every gameplay object sits on a terrain in its affinity set.
- [ ] Mines/towns exhibit short-range spacing; rewards have an adjacent guard with tier correlated to value.
- [ ] Relational pairs (seer/target, guard/keymaster) are consistent and solvable.
- [ ] No object violates an authored hard constraint (verified by a checker over generated maps).

## Dependencies

Epic 2 (marginals, affinity, lift), Epic 4 (macro scaffold, relational placement). Judged by Epic 6.

## Risks

- Guard↔reward coupling mis-specified → validate against the measured lift and spot-check generated maps.
- Authored constraints over-reach and suppress legitimate placements → keep the rule set small and evidence-based.

## Verification Strategy

Statistically compare generated gameplay composition and spacing to human distributions. Run the hard-constraint checker and confirm zero violations. Visually inspect in the editor that rewards are guarded, economy is spaced, and quests pair up.

## Notes for AI Agents

Purpose-justification is the test for every placement: an object goes down only where its purpose is met (right terrain, right relation, not redundant). This single rule simultaneously fixes overuse and bad adjacency.
