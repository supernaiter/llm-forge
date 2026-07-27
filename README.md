# forge

A small, dependency-light harness for LLM-driven heuristic discovery, in the
style of DeepMind's [FunSearch](https://www.nature.com/articles/s41586-023-06924-6):
an LLM mutates candidate programs, each candidate is evaluated in a sandboxed
subprocess against a deterministic scoring function, and an archive of
survivors feeds the next generation's prompts. The search loop itself is
plain Python control flow — the LLM only ever fills in one function body per
call.

Optional features: islands (independent sub-populations that occasionally
exchange candidates), a model pool (round-robin across several LLM endpoints
per call to diversify failure modes and code style), and a two-tier LLM split
(a cheap model that does the bulk of the mutating, a smart model used
sparingly for a final review pass).

## Key results

On an [LLM4AD](https://github.com/Optima-CityU/llm4ad)-compliant Online Bin
Packing benchmark (Weibull(3)×45 item sizes, capacity 100, 5 instances ×
5000 items, `np.random.seed(2024)` — see
[`projects/bench_obp/BASELINES.md`](projects/bench_obp/BASELINES.md)), forge
discovered a heuristic that reaches **1.07% excess over the L1 lower bound**,
versus **4.00%** for best-fit, using **~480 LLM calls** on a **local
Qwen2.5-Coder-1.5B** (inference cost: $0). For reference, FunSearch's paper
reports 0.68% excess using on the order of 10^6 samples against a
proprietary model. Different search budget, different model, not a
head-to-head — but it's evidence that a tiny local model and a few hundred
calls can already land a heuristic that clearly beats a strong handwritten
baseline.

The discovered heuristic generalizes to instances it never saw during
search: re-scored on unseen seeds it holds at 1.30–1.41% excess (best-fit
degrades similarly, to 4.08–4.21%, so the relative gap is preserved). It is
not simply overfit to one instance draw.

These are local, reproducible measurements from this repository's own test
suite (`tests/test_bench_packs.py` recomputes and checks them against
`baselines.json` on every run) — not a benchmark leaderboard claim.

## Philosophy: measurement discipline

Search algorithms are easy to fool yourself about. `best_score` (a single
run's maximum) is a max-statistic, so its variance across seeds is large
enough to swallow most real improvements. This repo computes 19 metrics for
every run — all deterministic, all free (no LLM or network calls, just
reading `archive.jsonl`) — and uses MDE-aware (minimum-detectable-effect)
A/B comparison instead of eyeballing a single number. See
[METRICS.md](METRICS.md) for the full metric list, why the default headline
metric is `auc_by_candidate` rather than `best_score`, and worked examples
of changes that looked like wins on `best_score` but weren't statistically
distinguishable from noise. Behaviour fingerprints (hashes of what a
candidate actually does, not just its score) are used to tell "the archive
found a genuinely different idea" apart from "the archive is full of
near-duplicates that happen to score differently."

## Quickstart

```bash
pip install numpy pytest
python3 -m pytest tests/ -q

# No LLM needed — deterministic mock candidates, just to check the plumbing:
FORGE_MOCK=1 python3 cli.py projects/stringmax --mock
```

To run against a real LLM, point the cheap/smart layers at any
OpenAI-compatible chat-completions endpoint (a local llama.cpp/vLLM server,
a hosted API, or a compatible proxy):

```bash
export FORGE_CHEAP_BASE_URL=http://localhost:8000/v1
export FORGE_CHEAP_MODEL=your-model-name
export FORGE_CHEAP_API_KEY=...        # empty string is fine for local servers

python3 cli.py projects/stringmax
```

See [`.env.example`](.env.example) and the docstring at the top of
[`forge/llm.py`](forge/llm.py) for the full set of `FORGE_CHEAP_*` /
`FORGE_SMART_*` variables (timeouts, fallback endpoints, model pools,
subscription-CLI backends, thinking-block suppression).

Multi-seed and A/B measurement entry points:

```bash
# Run one pack 3 times with different seeds (also writes a report at the end):
zsh tools/run_benchmark.sh bench_obp 3
# Re-aggregate later:
python3 tools/report_benchmark.py bench_obp --date "$(date +%Y%m%d)"

# Compare a forge-internal change (config key) across two arms, same day:
zsh tools/run_ab.sh bench_obp max_per_score 0 3 3
```

Both scripts accept `--mock` as a trailing argument to exercise the plumbing
without calling an LLM.

## Adding a problem

A problem pack is a single directory under `projects/` with two files:

- `problem.py` — a `Problem` class with a `DESCRIPTION` string, a `seed()`
  method returning one or more starting candidates, and a `score(candidate)`
  method returning `(score: float, alive: bool)`.
- `config.json` — search hyperparameters (`generations`, `batch_size`,
  `max_cheap_calls`, `max_smart_calls`, `archive_capacity`, `parents`,
  `temperature`, `seed`, …).

```bash
python3 tools/scaffold_problem_pack.py my_problem   # writes projects/my_problem/
python3 tools/validate_problem_pack.py projects/my_problem
FORGE_MOCK=1 python3 cli.py projects/my_problem --mock
```

`projects/_probe_newproblem` and `projects/stringmax` are minimal worked
examples; `projects/bench_obp` and `projects/bench_tsp` are the LLM4AD-style
benchmark packs referenced above.

## Requirements

Python 3.10+, `numpy`, `pytest`. No other runtime dependencies — the LLM
call is a plain `urllib` HTTP request (or a subprocess call, for
subscription-CLI backends), and candidate execution uses only the standard
library (`multiprocessing`, `ast`).

## License

MIT — see [LICENSE](LICENSE).
