# Forge — Simple Performance Goal V3

New study version. Does not edit `FORGE_SIMPLE_GOAL.md` (V1, achieved)
or `FORGE_SIMPLE_GOAL_V2.md` (failed). This is mock development, not a
scientific result.

## Question

On the frozen V1 matrix, can the primary controller raise its own
absolute search quality without losing the V1 advantage over
`FIXED_DEV_BEST`?

These are two different questions. V1 answered only the relative one.
V2 tried to enlarge the relative margin to +1.0 and failed. V3 asks
for an absolute lift plus a relative non-regression guardrail.

## Frozen baseline (do not refit)

Same command as V1, measured 2026-08-15 after reverting the unregistered
performance-chase patches:

| Policy | `auc_by_generation` | `auc_by_candidate` | `best_score` |
| --- | --- | --- | --- |
| Primary | `8.3333, 8.3333, 8.6667` mean **8.4444** | `8.5, 8.0, 8.75` mean **8.4167** | `9, 9, 9` mean **9.0** |
| `FIXED_DEV_BEST` | `8.0, 7.5, 8.0` mean **7.8333** | `8.0, 7.5, 8.0` mean **7.8333** | `8, 8, 8` mean **8.0** |

Primary selected `1 → 2 → 1` on every seed. FIXED selected `2 → 2`.

## Scope

- Problem: `projects/stringmax`
- Actions: the two arms in `protocol/controller_development_actions.json`
- Seeds (judgment): `0, 1, 2`
- Seeds (confirmation, no further code change): `3, 4, 5`
- Attempt cap: `4`
- Generations: `3`
- Model: mock only

## Metrics

- **Primary:** mean `auc_by_generation` of
  `TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1`.
  Same metric as V1/V2 so the frozen baseline stays usable.
  It averages the generation best-so-far curve. Do not win by inventing
  empty generations; every run must consume exactly 4 search attempts.
- **Secondary floor:** mean `auc_by_candidate` of the primary.
  Lower-variance trajectory metric from `METRICS.md`. Used only as a
  no-regression floor, not as a second way to pass.
- **Terminal floor:** mean `best_score` of the primary.
- **Relative guardrail:** V1 gate against `FIXED_DEV_BEST`, unchanged.

`best_score` is not the primary metric. It is too coarse on this
4-attempt matrix (V1 is already `9, 9, 9`).

## Why 8.8333, not 8.6944

The generation curves are integer best-so-far values. The frozen
primary curves sum to 76 over 9 cells, mean 8.4444:

`[7,9,9], [7,9,9], [8,9,9]`

- Lift both gen-1 sevens to 8: sum 78, mean **8.6667**. One obvious
  improvement. Still below 8.8333.
- Pass requires sum >= 80, mean **8.8333**. That is +4 curve points,
  so the obvious first improvement is not enough. A second event is
  required: gen-1 reaching 9, or a later peak of 10 that sticks.

8.6944 (old V3) sat between 8.6667 and 8.8333 and could be passed by
rounding after a single lift. That is too soft.

8.8333 is not V2. V2 demanded a +1.0 *margin vs FIXED*. If FIXED stays
at 7.8333, 8.8333 would equal that margin; if FIXED also rises, V3 can
still pass with margin 0.25. Destroying the controller-vs-FIXED gap
still fails.

## Done criteria

Judgment seeds `0, 1, 2`. All items must hold. Do not relax after seeing
results.

1. Every primary and `FIXED_DEV_BEST` run records exactly 4 attempts.
2. Primary mean `auc_by_generation` >= **8.8333**
   (frozen curve sum 76 → 80).
3. Primary mean `auc_by_candidate` >= **8.6667**
   (frozen 8.4167 + 0.25). A trajectory lift, not a no-regression floor.
4. Primary mean `best_score` >= **9.0** (no terminal regression).
5. V1 relative gate still holds:
   mean `auc_by_generation` margin vs `FIXED_DEV_BEST` >= 0.25, and
   primary `best_score` >= FIXED on at least 2 of 3 seeds.
6. Replay decision-hash and result-recomputation-hash mismatches are 0.
7. Re-running the judgment command reproduces metrics and selected
   action sequences.
8. Confirmation: rerun the same binary with seeds `3, 4, 5` and no
   further edits. That run must satisfy items 1, 5, 6 and
   mean `best_score` >= 9.0. It is not scored against 8.8333 because
   that floor is defined only on seeds `0, 1, 2`.

## Ineligible passes

- Lowering FIXED instead of raising the primary.
- Making both policies identical (H1: elite-only parents, margin 0,
  mean best 10.333). Absolute scores went up; the relative guardrail
  failed. That run is not a V3 pass.
- Changing seeds, attempt cap, problem, actions, or the mock model.
- Using holdout data, or claiming a mock pass as research evidence.
- Treating H1's 10.333 / 9.083 as the target. Those numbers were seen
  after the fact and fail item 5.

## Non-goals

- Reopening V2's +1.0 relative margin
- Editing V1, V2, or `RESEARCH_V3_TERMINATION_CRITERION.md`
- Hidden holdout, external baselines, or production verdicts

## Reproduction

Judgment:

```bash
FORGE_MOCK=1 uv run --system-certs --with numpy \
  python tools/run_controller_development.py \
  --problem stringmax=projects/stringmax \
  --actions protocol/controller_development_actions.json \
  --out <output-dir> --generations 3 --max-attempts 4 \
  --seed 0 --seed 1 --seed 2
```

Confirmation: same command with `--seed 3 --seed 4 --seed 5`.

Judge only from `development_summary.json` policy-run metrics.

## Execution loop

Dynamic `/loop` until this file's status is `passed` or a real blocker.
Do not stop because a tick is inconvenient. Do not relax gates.

Each tick:

1. Read this file and the last `progress.md` V3 entry.
2. If status is `passed`, stop the loop.
3. Apply exactly one unused hypothesis. Do not repeat a failed change.
4. Run the judgment command. Score only from `development_summary.json`.
5. On fail: revert the hypothesis, log the numbers, mutate the next idea.
6. On judgment pass: run confirmation seeds `3,4,5` with no further edits.
   Confirmation fail is a fail; mutate. Confirmation pass writes `passed`.
7. Banned: editing V1/V2/criterion, changing seeds/budget/problem/model,
   using H1's 10.333 as a target, grinding V2's +1.0 margin.

Failed idea classes (do not repeat):

- V2H1 elite-only `sample_parents` (margin 0)
- V2H2 mid-horizon `+0.4` temperature (no lift)
- V2H3 expensive-first `2 → 1 → 1` (margin -0.1667)
- unregistered best-last prompt for every policy (primary AUC fell)
- V3H1 opening cheap scout → incumbent (lost the 9)
- V3H2 mid-slot incumbent only (no lift)
- V3H4 H3 + mid temp +0.7 (no extra lift)
- V3H5 H3 + opening scout (erased the seed-0 ten)
- V3H6 mid-slot sequential in-batch parent (seed1 lost the 9; 8.3333)
- V3H7 cheap-tail temperature +0.7 (erased seed0 gen3=10; 8.4444)
- V3H9 emit unmutated mid concat on the last slot (seed1 collapsed to 7)

## Current status

- State: `passed`
- Loop: `stopped`
- Passing code: V3H3 mid+tail incumbent, plus V3H10 mid-slot recombine
  of the incumbent with all generation-0 seed texts (H8 top-2 concat
  failed confirmation because a failed scout dropped `abc`).
- Judgment seeds 0,1,2: primary `auc_by_generation` **10.0**,
  `auc_by_candidate` 10.25, `best_score` 11.3333, FIXED 7.8333,
  margin **+2.1667**. Attempts 4. Sequences `1→2→1` vs `2→2`.
  Repeat reproduced metrics, sequences, and decision hashes.
- Confirmation seeds 3,4,5: primary `auc_by_generation` 10.2222,
  `best_score` 12.3333, FIXED 8.8333, margin **+1.3889**. Attempts 4.
  Primary best >= FIXED on 3/3 seeds. Replay mismatch mentions 0.
- This is a mock development pass, not research evidence.
