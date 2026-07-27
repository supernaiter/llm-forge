# Contributing

- `python3 -m pytest tests/ -q` must pass before any PR.
- This project does not accept search-loop or heuristic-tuning changes without an A/B
  measurement. `best_score` alone is not evidence — see [METRICS.md](METRICS.md) for
  why single-seed and single-metric comparisons are usually noise, and for how to run
  a proper `tools/run_ab.sh` comparison with a stated MDE (minimum detectable effect).
  A PR that changes `forge/loop.py`, `forge/operators.py`, `forge/archive.py`, or a
  problem pack's scoring/config in a way meant to improve search quality should include
  the A/B numbers (control vs. treatment, seed count, metric, MDE) in the PR description.
- Bug fixes, new problem packs, docs, and tooling improvements are welcome without an
  A/B run — that requirement is specifically for "this makes the search better" claims.
- New problem packs: a pack is a directory under `projects/` with `problem.py` (a
  `Problem` class exposing `DESCRIPTION`, `seed()`, and `score()`) and `config.json`.
  Run `python3 tools/validate_problem_pack.py projects/<name>` before opening a PR.
