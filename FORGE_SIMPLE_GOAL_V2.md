# Forge — Simple Performance Goal V2

This is a new study version. It does not edit or supersede
`FORGE_SIMPLE_GOAL.md` (V1, margin >= 0.25, achieved 2026-08-14).

## Goal

With the same mock model, the same seeds, and the same 4-attempt budget,
`TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1` beats `FIXED_DEV_BEST` by at
least 1.0 mean `auc_by_generation`.

## Scope

- Problem: `projects/stringmax`
- Actions: the two arms in `protocol/controller_development_actions.json`
- Comparator: `FIXED_DEV_BEST`
- Seeds: `0, 1, 2`
- Attempt cap: `4`
- Generations: `3`
- Model: mock only
- Primary metric: `auc_by_generation`
- Secondary metric: `best_score`

## Done criteria

All of the following must hold. Do not relax any item after seeing results.

1. Every controller and `FIXED_DEV_BEST` run records exactly 4 attempts.
2. Mean controller `auc_by_generation` exceeds mean `FIXED_DEV_BEST` by
   **1.0 or more**.
3. Controller `best_score` is at least `FIXED_DEV_BEST` on 2 of 3 seeds.
4. Replay decision-hash and result-recomputation-hash mismatches are 0.
5. Re-running the same command reproduces the metrics and selected-action
   sequences.

If the margin is below 1.0, record a failure. Do not change the threshold,
seeds, attempt cap, problem, or model to manufacture a pass.

## Non-goals

- Editing `FORGE_SIMPLE_GOAL.md` or `RESEARCH_V3_TERMINATION_CRITERION.md`
- Hidden holdout, external baselines, or production verdicts
- Claiming mock results as scientific superiority
- Adding new audit gates or relaxing V1 evidence

## Reproduction command

```bash
FORGE_MOCK=1 uv run --system-certs --with numpy \
  python tools/run_controller_development.py \
  --problem stringmax=projects/stringmax \
  --actions protocol/controller_development_actions.json \
  --out <output-dir> --generations 3 --max-attempts 4 \
  --seed 0 --seed 1 --seed 2
```

Judge only from that command's `development_summary.json` policy-run
metrics. Self-report is not evidence.

## Current status

- State: `failed`
- Best measured margin remains the V1 packing `1 → 2 → 1`:
  primary `8.4444` vs `FIXED_DEV_BEST` `7.8333`, margin `+0.6111`.
  All six runs used 4 attempts. This does **not** satisfy the V2 gate
  of `+1.0`.
- Three distinct hypotheses were measured and reverted. The threshold,
  seeds, attempt cap, problem, and model were not relaxed.

| Hypothesis | Change | Mean margin | Outcome |
| --- | --- | --- | --- |
| Baseline / V1 packing | `1 → 2 → 1` | `+0.6111` | below gate |
| H1 elite-only parents | `elite` returns incumbent only | `+0.0000` | FIXED flipped to SMALL; both identical |
| H2 mid-horizon temperature | `+0.4` temp on remaining=3 expensive slot | `+0.6111` | scores unchanged at 9 |
| H3 expensive-first pack | `2 → 1 → 1` | `-0.1667` | lost the SMALL-then-STRONG 9 |

The 9-point incumbent appears only from the mixed `1 → 2` sequence.
Neither registered arm reaches 9 in generation 1. Raising the cheap
arm's parent quality makes SMALL win development quality, so
`FIXED_DEV_BEST` copies it and the margin collapses to 0. Front-loading
the expensive arm removes the mix and loses to the baseline.
