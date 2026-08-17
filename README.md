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

## Research V3 contract

The proposed machine-auditable research contract is recorded in
[`RESEARCH_V3_TERMINATION_CRITERION.md`](RESEARCH_V3_TERMINATION_CRITERION.md),
with a reusable execution prompt in
[`FORGE_RESEARCH_V3_GOAL_PROMPT.md`](FORGE_RESEARCH_V3_GOAL_PROMPT.md).
The machine-readable constants, requirement traceability matrix, and frozen
asset templates (all explicitly marked `DRAFT` until an external authority
freezes them) live under [`protocol/`](protocol/). V3 instrumentation is
opt-in so legacy runs remain compatible:

```bash
FORGE_MOCK=1 python3 cli.py projects/_probe_newproblem \
  --mock --protocol-v3 --run-dir /tmp/forge-v3-mock
python3 tools/replay_run.py /tmp/forge-v3-mock/events.jsonl
```

V3 mode records every generation slot, including failed or rejected attempts,
in a hash-chained `events.jsonl`. A mock run is an engineering dry run only;
it is not a holdout result or a paper verdict.

Legacy CLI runs are labeled `FORGE_LEGACY` in `manifest.json` and are never
research-eligible. A non-mock V3 run additionally requires a frozen,
development-only controller policy and a complete registered run identity:

```bash
python3 tools/freeze_controller.py \
  --traces /path/to/development_traces.jsonl \
  --actions /path/to/registered_actions.json \
  --out /path/to/controller_policy.json
python3 cli.py /path/to/problem_pack --protocol-v3 \
  --controller-policy /path/to/controller_policy.json \
  --controller-model-routes /path/to/controller_model_routes.json \
  --model-manifest /path/to/model_manifest.json \
  --run-identity /path/to/frozen_run_identity.json \
  --run-dir /path/to/run
```

`--controller-model-routes` is a separate, content-hashed binding from each
frozen controller `generator_model` identity to a Forge adapter tier. Every
identity in the policy must be present; non-mock V3 CLI runs fail closed when
the route manifest is absent or incomplete. The route file does not replace
the externally frozen model/weight/runtime manifest. Supplying
`--model-manifest` verifies that every route hash matches that validated file;
the route and model-manifest hashes are recorded in `manifest.json`.

Development traces can be recomputed from a Forge search ledger before the
policy is frozen. The collector uses only search-side incumbent checkpoints
and observed generation cost; it never opens a holdout pack:

```bash
python3 tools/collect_controller_traces.py \
  --events /path/to/dev-run/events.jsonl \
  --problem-id obp_dev_v1 \
  --out /path/to/development_traces.jsonl
```

For a non-mock V3 run, every `generator_model` identity selected by the frozen
controller must also be bound to a callable adapter: use the CLI's
`--controller-model-routes` manifest or the Python API's
`controller_model_callers` mapping. If that binding is absent or non-callable,
the loop rejects the run rather than silently using the default cheap caller.
Public mock runs may use the default caller for plumbing tests only.

Each V3 attempt also carries a normalized `resource_usage` record. Generation
telemetry (tokens, model/sampling identity, wall time, GPU allocation, and
model-forward time) is kept separate from evaluator telemetry and budgets.
When an adapter cannot provide a field, the ledger stores `null` plus an
explicit `missing` entry; it never imputes a value. `tools/replay_run.py`
recomputes both the search decision hash and the resource-ledger hash.
An external terminal bundle must additionally attest to complete budget
telemetry. The mock fixture records explicit counts from its declared
`MOCK_WHITESPACE_V1` tokenizer, but it remains an engineering dry run only;
native runs must provide observed GPU allocation and model-forward telemetry.

The public bundle verifier is deliberately read-only. It checks a frozen
study bundle's content hashes, sealed task/model/baseline manifests, frozen
run matrix (including exact primary/extension seed coverage), ledger replay,
hidden-event denylist, GPU-AUC schema, and explicit terminal evidence; missing
or unresolved assets fail closed. A repository-only mock bundle cannot
terminate: a receipt from the external read-only verifier is required, and a
registered result must contain one incumbent checkpoint per capped attempt.
The default verifier also requires the checkout containing the frozen source
commit to be clean:

```bash
python3 tools/verify_v3_research.py /opt/forge-study-bundle-v3
```

This repository-side verifier is not the external authority required for the
registered study. It cannot create the sealed holdout, certify licenses, or
replace the final unblinding authority. Until those externally frozen assets
exist, `FORGE_RESEARCH_FINISHED` remains false. The local
`V3_ENGINEERING_READY` predicate is intentionally separate: it covers the
public mock/replay/resource audit and may be true while registered research is
still blocked on external assets.

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

Controller development matrix (local problems only):

```bash
python3 tools/run_controller_development.py \
  --problem stringmax=projects/stringmax \
  --problem probe=projects/_probe_newproblem \
  --actions protocol/controller_development_actions.json \
  --out runs/controller-development \
  --generations 3 \
  --seed 0 --seed 1 --seed 2
```

This runs every registered action on every development problem through the
actual V3 loop, writes one ledger and trace file per arm, merges the
development traces, then freezes the primary controller and all registered
ablations under `policies/`. It then replays each frozen policy on every
development problem and records the selected-action sequence under
`policy_runs/`; those replay runs are never fed back into fitting. It never
reads holdout assets and always uses the mock adapter; the output is
controller-development evidence, not a scientific result bundle. Each replay
row also records the event/result file hashes and replay decision hashes.
The resulting `development_summary.json` includes problem-local,
seed-separated `development_comparison` cells for action arms and frozen-policy
replays. These remain mock-development diagnostics, not normalized holdout
metrics or scientific evidence.

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
