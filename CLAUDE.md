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

---

# CLI & Makefile Command Timeouts

**Every command an agent can run must be bounded by a wall-clock timeout.** A
command without a timeout can block forever — a dev server that never returns, a
test runner waiting on a missing service, a `docker compose up` with no
healthcheck, a prompt for stdin. An autonomous agent has no human to hit
`Ctrl-C`, so an unbounded command stalls the whole run until an outer timeout
(if any) kills it minutes or hours later.

This applies in two places: **Makefile/script recipes you author** and **ad-hoc
shell commands an agent runs directly**.

---

## Rule 1 — Makefile & script recipes wrap blocking commands in `timeout`

Any recipe whose command can block — tests, lint, build, codegen, dev servers,
`docker compose`, DB waits, network calls — wraps the command in
`timeout <seconds> <command>`. Trivially-fast, non-blocking commands (`echo`,
`rm`, `mkdir`, `cp`, `gofmt -l`) do not need one.

```makefile
# ✅ blocking commands are bounded
test:
	@echo "Running tests..."
	timeout 300 go test ./...

lint:
	@echo "Running linters..."
	timeout 60 go vet ./...

generate:
	@echo "Generating code from OpenAPI spec..."
	timeout 90 go run ./cmd/codegen ...

dev:
	@echo "Starting dev server..."
	timeout 120 npm run dev
```

```makefile
# ❌ unbounded — an agent calling `make test` hangs if a test deadlocks
test:
	go test ./...
```

### Standard ceilings

Pick the smallest ceiling that comfortably exceeds the normal run. The timeout is
a safety net against *hangs*, not a performance budget — leave generous headroom
so a slow-but-healthy run never trips it.

| Recipe kind | Typical ceiling |
|---|---|
| Lint / typecheck / vet | 30–60s |
| Codegen (OpenAPI, sqlc, mocks) | 60–120s |
| Unit tests | 120–300s |
| Build | 120–300s |
| Integration tests (Docker/DB) | 300–600s |
| Dev server / watch (foreground) | 60–120s |

### Recipes that wait on a service

A bare `sleep` to "wait for the database" is itself an unbounded gamble — the
service may never come up. Prefer a bounded readiness poll, and bound the
`docker compose up` and the test run too:

```makefile
# ✅ bounded bring-up, bounded wait-loop, bounded test run, guaranteed teardown
test-integration:
	timeout 120 docker compose -f docker-compose.dev.yml up -d mysql-test
	timeout 60 sh -c 'until docker compose exec -T mysql-test mysqladmin ping --silent; do sleep 1; done'
	INTEGRATION_TEST=1 timeout 600 go test -tags=integration ./internal/... ; status=$$? ; \
		docker compose -f docker-compose.dev.yml down ; exit $$status
```

Note the teardown runs even when the test run times out (the `; status=$$? ; ...
; exit $$status` pattern), so a hang never leaks containers.

### Recipes that compose other recipes

When a recipe just calls sub-`make`s (e.g. a root `test` that runs `cd api &&
make test` then `cd web && make test`), the timeout belongs on the **leaf**
recipes, not the aggregator. Don't double-wrap — a `timeout` around a `make`
that already wraps its commands only muddies which limit fired.

---

## Rule 2 — Ad-hoc shell commands an agent runs get a timeout too

When you (the agent) run a command directly rather than through a recipe, apply
the same discipline. The harness's own tool timeout is a last resort, not a
plan: wrap commands that can block so a hang fails fast and visibly.

- **Servers, watchers, REPLs, `tail -f`, `logs -f`** — these never return by
  design. Either run them with a `timeout` (to capture N seconds of output) or
  start them in the background and poll, rather than blocking the foreground.
- **Test / build / codegen invoked directly** — prefer the Makefile recipe (it
  already carries the ceiling). If you must run the raw command, add `timeout`.
- **Network calls** (`curl`, `gh`, package installs) — bound them; a hung TLS
  handshake or auth prompt otherwise blocks indefinitely.

```bash
# ✅ bound a direct test run
timeout 300 go test ./internal/foo/...

# ✅ capture 10s of server logs instead of blocking forever
timeout 10 npm run dev

# ❌ blocks the agent until an outer limit kills it
go test ./...        # may deadlock
npm run dev          # never returns
```

---

## Behaviour & exit codes

- `timeout` exits **124** when the command is killed for exceeding the limit.
  Treat 124 as a hang/too-slow signal distinct from the command's own failure.
- For commands that ignore `SIGTERM`, use `timeout --kill-after=<dur> <limit>`
  so a `SIGKILL` follows if the process doesn't exit promptly.
- `timeout` is part of GNU coreutils; it is present on Linux and in CI/Docker
  images. On a bare macOS dev box it may need `coreutils` (`gtimeout`) — the
  agent and CI run on Linux, so author recipes for `timeout`.

## Checklist before adding or editing a recipe

- [ ] Does this command ever block, wait, serve, or watch? → wrap in `timeout`.
- [ ] Is the ceiling generous vs. a healthy run but still bounded? 
- [ ] Does any service it starts get torn down even on timeout?
- [ ] Are pure-filesystem / `echo` steps left unwrapped (no needless timeout)?
