# Technical Direction & Architecture

This document is the spine. The roadmap lists *what* we build; the epics build the *parts*; this explains the *machine they form*, the *single idea* behind it, and the *assumptions* it rests on. Read this first.

## 1. The problem, restated as one technical objective

A human-made map is not defined by a rule we can write down — it's defined by *being the kind of thing a human designer would produce*. The honest formalization:

> Real maps are samples from some probability distribution **P(map)**. "Feels hand-made" means **"is a high-probability sample under P."** Our job is to approximate P from the 159 examples and sample new maps from it.

We never see P. We see 159 draws from it. Everything below is about making that learnable.

## 2. The central bet: factor the distribution

We cannot learn P(map) directly — a 144×144×2 grid with hundreds of object types is astronomically high-dimensional, and 159 examples is nothing at that scale (this is exactly why an end-to-end neural generator is a non-starter here). So we make **one structural bet**: P factors into a small chain of conditional distributions, each of which is *low-dimensional enough to estimate from 159 maps*.

```
P(map) = P(M)              ·  P(T | M)        ·  P(O | T, M)
         macro structure      terrain | macro    objects | terrain, macro
```

- **M — macro structure**: the region graph, connectivity topology, town/portal skeleton. "Where are the zones and how do they connect."
- **T — terrain**: per-tile terrain + overlays (rivers, roads), conditioned on the macro layout.
- **O — objects**: every placed object, conditioned on the terrain and the structure.

The object factor **P(O | T, M)** factors *again*, into the four terms we keep returning to:

```
P(O | T, M) = composition (N)   ·  affinity (U)        ·  lift (Φ)            ·  constraints (C)
              how many of each      where each can go      how they relate        the hard "nevers"
              (marginals)           (P type | terrain)     (pairwise attract/      (authored; zero
                                                            repel/guard)            data to learn)
```

That's the entire model on one page. **Each factor is low-dimensional** — a histogram of counts, a type→terrain table, a pairwise matrix, a short rule list — so 159 maps is *plenty* to estimate each one, even though it's nowhere near enough to learn the joint. **The factoring is the whole trick that makes small data sufficient.**

## 3. The machine: represent → estimate → sample → score → refine

Generation is **ancestral sampling** down the factor chain (macro first, because terrain depends on it, because objects depend on both). Learning is estimating each factor's parameters. Validation is checking the sample's likelihood under P. As a pipeline:

```
   159 real maps
        │
        ▼   ① REPRESENT  — lift each map into observable variables
   (terrain grids, typed objects with purpose, region graphs)
        │
        ▼   ② ESTIMATE   — fit every factor's parameters from the corpus
   ┌─────────────┬──────────────────┬───────────────────────────┐
   │ macro metrics│ Markov terrain   │ N (counts), U (affinity), │
   │  → P(M)      │  tables → P(T|M) │ Φ (lift), C → P(O|T,M)    │
   └─────────────┴──────────────────┴───────────────────────────┘
        │
        ▼   ③ SAMPLE     — ancestral sampling, macro → terrain → objects
   M  =  sample region graph + connectivity                 (from P(M))
   T  =  sample terrain given M  (biome scaffold + Markov)   (from P(T|M))
   O  =  sample objects given T,M:                           (from P(O|T,M))
            draw counts from N → place by U (affinity gate)
            → arrange by Φ (Gibbs: cluster/repel/guard)
            → wire relational pairs → enforce C (hard nevers)
        │
        ▼   ④ SCORE      — is this a high-probability sample?
   fingerprint: compare the sample's N/U/Φ/macro-metrics to the corpus
   discriminator: learn human-vs-generated; what it catches = unmodeled structure
        │
        └──▶ deviations & tells become new work → re-estimate / add a factor
```

This loop is the answer to "how is this not just hand-tuned heuristics": **every knob is a measured parameter of a factor, and the critic tells us which factor is still wrong.**

## 4. How the epics are the parts of this one machine

The epics are not separate projects — they are the **stages of the pipeline above**. This is the linkage:

| Epic | Pipeline stage | What it owns in the model |
|---|---|---|
| **1 · Corpus Extraction & Ontology** | ① Represent | Defines the random variables — lifts maps into (terrain, typed objects, graph) so the factors are *observable*. Nothing can be estimated until this exists. |
| **2 · Statistical Baselines** | ② Estimate | Fits the factor parameters: macro metrics for P(M), Markov tables for P(T\|M), and **N, U, Φ** for P(O\|T,M). The shared parameter store every generator reads. |
| **4 · Macro Generation** | ③ Sample M | Samples the *first* factor — region graph + connectivity. Runs first at generation time even though it's later in foundation order. |
| **3 · Micro Generation** | ③ Sample T | Samples terrain given M (biome scaffold + Markov texture) and the decoration part of O. |
| **5 · Gameplay Placement** | ③ Sample O | Samples the object factor given T, M, using N → U → Φ → relational → C. Same engine as decoration, different term emphasis (repel + guard↔reward). |
| **6 · Validation & Quality** | ④ Score / refine | Approximates the likelihood under P (fingerprint) and catches what the factors miss (discriminator). Closes the loop back to ②. |

So the dependency spine reads: **1 makes variables observable → 2 estimates the factors → 4/3/5 sample them in order M→T→O → 6 scores the sample and feeds corrections back to 2.** Epic 2's tables (especially the **pairwise lift Φ**) are the shared currency: the *same* Φ drives decoration clustering (Epic 3) and guard↔reward coupling (Epic 5). That shared currency is why these aren't disconnected — they read and write the same model.

## 5. Assumptions the whole approach rests on

If one of these is false, the corresponding part of the machine fails — so they're stated plainly, each with what it buys and what would break it.

1. **Factorization** — P(map) really does decompose into M · (T\|M) · (O\|T,M) with manageable cross-terms.
   *Buys:* tractability and data-efficiency. *Breaks if:* maps carry strong global dependencies no factor captures (e.g. a deliberate thematic narrative across the whole map). *Guard:* the discriminator (Epic 6) exists to detect uncaptured structure → we add a factor when it does.

2. **Locality (Markov)** — terrain and object *arrangement* is dominated by local context (a few tiles), modelable by Markov/MRF + short-range lift.
   *Buys:* the entire micro engine. *Breaks for:* long-range *intent* (a quest chain spanning the map). *Guard:* long-range intent is handled by the **macro factor M and authored constraints C**, not by locality — this is why pure-local generation streaks/drifts and *must* have a macro scaffold. (Already proven on terrain.)

3. **Low-dimensional factors are data-sufficient** — 159 maps can estimate marginals/affinity/lift/macro-metrics, even though they can't learn the joint.
   *Buys:* "small data is enough." *Breaks for:* sparse factors (rare type-pairs in Φ). *Guard:* back-off to purpose level + log what's dropped; never silently truncate.

4. **Contrastive human-ness** — quality is measurable as the *difference* from the RMG (and from our own generations), so we don't need an absolute quality metric.
   *Buys:* an objective to optimize without a labeled "goodness" signal. *Breaks for:* tells shared by both human and contrast (invisible to a discriminator). *Guard:* keep the human eye in the loop.

5. **The authored residue is small** — the hard "nevers" that data can't teach (zero positive examples) are few and driven by game knowledge.
   *Buys:* correctness on rules the statistics can't represent. *Breaks if:* the residue is large — then we're hand-authoring a game, not learning a distribution. *Guard:* every authored rule must be evidence-bounded (the contrast shows most violations; only the truly zero-data ones get authored).

6. **Editor visual inspection is ground truth** — statistical scores are proxies for it.
   *Buys:* a reality check the proxies can't fake. *Breaks if:* we over-trust a good fingerprint score (a sample can match all measured statistics and still look wrong). *Guard:* every milestone produces a render and, at checkpoints, a real editor open.

## 6. What we've already proven about the direction

- **The factoring across scales is necessary, not optional.** A pure local Markov model produces realistic *local* terrain texture but cannot carry *global* structure (raster ordering streaks; Gibbs drifts to self-reinforcing terrain). This is assumption #2's failure mode observed directly — and it's why the architecture is macro-scaffold + micro-texture everywhere, for terrain *and* objects.
- **Frequency and arrangement must stay separate.** Conflating "how many" (N) with "where, relative to what" (U, Φ) is what made portals look like a count problem when they're really a structural one, and what let a noise-field fake "clustering." The factoring keeps them honest.
- **"Default to a bucket" is a generation bug.** Estimation tolerance (unknown → decoration) leaked visitable objects into the decoration pool. Generation must use *positive*, measured membership (the mask-`A` rule, the affinity gate), never a fallback.

## 7. The current frontier

Estimation is the bottleneck right now: **the pairwise lift Φ is the one unbuilt factor**, and it's the keystone — it's what turns "right terrain, random spot" into "clusters, guards, and spacing." It unblocks Epic 3 (decoration clustering) and Epic 5 (guard↔reward) simultaneously, because both read the same Φ. That's the next thing to build.
