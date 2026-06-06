#!/usr/bin/env python3
"""Load per-program research config and expose it as `cfg` for the generic workflow.

This is the start node of the single, program-agnostic research workflow. It
selects which program to run from its first argument — the workflow's `program`
param (default: grammar-semantics) — and reads its config from
`.agents/workflows/research/programs/<name>.json`. Every downstream node reads
program paths from `{{ cfg.* }}` instead of hardcoded vars, so one workflow.yaml
drives any research program. Pick a program at run time with
`--params '{"program": "<name>"}'`.

Stdlib-only: scripts run under the system `python3`, not the uv venv.

Outputs JSON: {"cfg": { repo_url, repo_branch, result_branch, program_dir,
                       progress_path, code_root, program }}
"""
import json
import sys
from pathlib import Path

REQUIRED = ["repo_url", "repo_branch", "result_branch", "program_dir", "code_root"]


def main() -> None:
    name = (sys.argv[1].strip() if len(sys.argv) > 1 else "") or "grammar-semantics"
    programs_dir = Path(__file__).resolve().parent.parent / "programs"
    cfg_path = programs_dir / f"{name}.json"

    if not cfg_path.is_file():
        available = sorted(p.stem for p in programs_dir.glob("*.json"))
        sys.exit(
            f"[load_config] unknown program '{name}'. "
            f"Add programs/{name}.json or pick one of: {available}"
        )

    try:
        cfg = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"[load_config] {cfg_path} is not valid JSON: {e}")

    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        sys.exit(f"[load_config] {cfg_path} missing required keys: {missing}")

    # Convenience default: progress log lives under the program dir unless overridden
    # (hrnet keeps its PROGRESS.md outside its program_dir, so it sets this explicitly).
    cfg.setdefault("progress_path", f"{cfg['program_dir']}/PROGRESS.md")
    cfg["program"] = name

    print(json.dumps({"cfg": cfg}))


if __name__ == "__main__":
    main()
