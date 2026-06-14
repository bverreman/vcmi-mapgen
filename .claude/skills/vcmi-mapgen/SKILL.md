---
name: vcmi-mapgen
description: "VCMI map-generator repo root — what the project is, uv tooling, the generation pipeline, and how to run the benchmark / load-test. Load first for any work in this repo."
metadata:
  generated_by: farrier
  source: library/skills/vcmi-mapgen/vcmi-mapgen/SKILL.md
  do_not_edit: "edit the source in the central prompt library and re-run `make agent-install` to regenerate"
---

# VCMI map-generator — repository root

Goal: a generator whose probability distribution **contains** the 159 hand-made
HoMM3 maps — judged by objective metrics, not by eye. Architecture is form-first:
a dependency tree (gating skeleton) → 2D embedding (zones/barriers/chokepoints) →
realization (terrain, then objects placed by a learned **adjacency graph**) → an
editor-valid `.vmap`. Load `vcmi-mapgen-maps` for the domain details.

## Tooling — this is a `uv` Python project

- Run **everything** through uv: `uv run python src/<script>.py [...]`.
  Dependencies are in `pyproject.toml` (only Pillow; everything else is stdlib).
  Never `pip install`; never assume a system interpreter — `uv run` resolves the
  env. No network at runtime.
- Determinism: generation is seeded (`realize(seed=...)`); the benchmark fixes
  seeds so numbers reproduce. Don't introduce `random`/time without a seed.

## Where things are

- Generator: `src/deps_gen.py` (tree) → `src/deps_embed.py` (2D) →
  `src/deps_realize.py` (**the main file**: terrain, water, mountain barriers, and
  adjacency-graph object placement) → `src/faithful.py` (writer).
- Learned data (built from the corpus, do not delete): `out/deps.json`,
  `out/objlib.json`, `out/factors.json`, `out/spatial_prior.json`,
  `out/adjacency.json`.
- Corpus of real maps: `~/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/**/*.h3m`.

## How to measure (the fitness function)

- Benchmark (3 control-relative scores): `uv run python src/benchmark.py --maps 12 --seeds 10`
  → writes `out/benchmark.json`. This is what the research gates check.
- Single fit to a target map: `uv run python src/deps_fit.py "<Map Name>.h3m"`.
- Editor load-test: `src/gate.py` `load_test()` (headless VCMI editor; needs
  `xvfb` + `flatpak`; ~95s). `loaded:True, fatal:False` = valid.

## Rules

- Generated maps live in `out/`. Do **NOT** copy them into the VCMI `Maps/` folder
  except the single temp file the load-test uses.
- Claims are **control-relative**: the generator must beat the shuffled control —
  improving in absolute terms is not enough (see `research/README.md`).
- Commit only work that passes its gate; keep diffs small and seeds fixed.
