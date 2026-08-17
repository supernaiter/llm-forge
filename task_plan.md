# Task plan

## Goal

`FORGE_SIMPLE_GOAL_V3.md`: raise primary mean `auc_by_generation` from
the frozen 8.4444 to >= 8.8333 on seeds `0,1,2`, with
`auc_by_candidate` >= 8.6667, `best_score` >= 9.0, and the V1 relative
gate vs `FIXED_DEV_BEST` (>= 0.25). Confirm on seeds `3,4,5`.

## Current Phase

V3 loop armed. H3 (mid+tail incumbent) kept at 8.5556. Gate 8.8333
unmet. Next tick must lift gen-1 or gen-2 without reopening the scout.

## Active phases

- [completed] Write `FORGE_SIMPLE_GOAL_V2.md` as a new study version (do not
  edit V1).
- [completed] Add `.cursor/rules/forge-goal-loop.mdc` so later sessions
  re-read the contract and refuse threshold relaxation.
- [completed] Run the V2 reproduction command: baseline margin `+0.6111`.
- [completed] H1 elite-only parents: margin `+0.0000`. Reverted.
- [completed] H2 mid-horizon temperature: margin `+0.6111`. Reverted.
- [completed] H3 expensive-first pack: margin `-0.1667`. Reverted.
- [completed] Record V2 as failed without relaxing the gate.

## Historical V3 phases

## Goal (historical)

Design and preserve an executable Forge Research V3 goal prompt derived from
`RESEARCH_V3_TERMINATION_CRITERION.md`, with explicit repository mappings,
termination states, verification gates, blockers, and stop points.

## Phases

- [completed] Inspect the source criterion, existing goal prompt, and repository entry points.
- [completed] Reconcile the prompt with the criterion and current repository state.
- [completed] Validate the saved prompt and record the handoff evidence.
- [completed] Audit and harden registered run artifacts, native GPU telemetry,
  and baseline cutoff/conformance schema.
- [completed] Bind positive metric gate booleans to machine-readable numeric
  thresholds and independent verifier checks.
- [completed] Bind every positive gate's evidence field, comparison operator,
  and threshold through the protocol contract; reject alias drift before verdict.
- [completed] Bind replay-derived lineage link/cycle/hack-audit coverage to
  registered run artifacts and fail closed on incomplete structural evidence.
- [completed] Bind holdout family/coverage validation into the explicit
  integrity evidence predicate.
- [completed] Bind a finite, non-negative, identical evaluator-call budget
  limit across every registered run artifact and add a negative fixture.
- [completed] Extend the public mock audit to exercise the NATIVE_COMPUTE
  resource/replay path with explicit mock GPU telemetry.
- [completed] Exercise the actual CLI/loop NATIVE_COMPUTE mock path with an
  explicitly opt-in synthetic telemetry adapter and replay its result.
- [completed] Expose and assert all four golden terminal-state outputs in the
  public engineering report.
- [completed] Close the numpy file-backed hidden-data channel in the V3 AST /
  sandbox policy and add negative fixtures.
- [completed] Add dependency-independent sandbox denial evidence to the public
  engineering report.
- [completed] Close the ledger event vocabulary and reject non-finite or
  malformed attempt/evaluator payloads while preserving explicit failure
  sentinel semantics.
- [completed] Require an explicit hidden-content absence flag in the
  search-visible task manifest and preserve typed hidden-test clusters in
  hierarchical bootstrap resampling.
- [completed] Reject infinite benchmark evaluator scores and malformed Q
  statuses/counts in fail-closed verdict paths.
- [completed] Reject non-integer/boolean bootstrap replicate counts and RNG
  seeds before hierarchical resampling, with negative boundary tests.
- [completed] Reject bundle symlink escapes and resolved-path/inode aliases in
  the read-only verifier and registered run matrix.
- [completed] Wire controller parent-selection, mutation-operator, and
  reflection-depth choices into the V3 generation path with legacy compatibility
  and an optional pinned model-caller router; apply archive-sampling policy to
  search-side archive state with fail-closed unknown-policy handling; require
  callable model routing for non-mock V3 controller identities.
- [completed] Make the V3 parametric generator executable with controller
  provenance: skip unrelated LLM adapter initialization, record the observed
  `PARAM_MUTATION` identity, and mark the generation mode explicitly.
- [completed] Make V3 resume operational: continue from the next ledger
  generation, restore controller action/state records, and test unique
  generation-slot replay across a two-stage run.
- [completed] Exercise the developed Forge execution path on multiple local
  development problems and use their measured traces to freeze and compare
  the primary controller and all registered ablations, while keeping external
  scientific assets blocked.
- [completed] Record the visible generation-start incumbent and use it to
  compute first-generation development quality gains instead of forcing the
  first controller training signal to zero.
- [completed] Complete the deliberately small `FORGE_SIMPLE_GOAL.md` on
  `stringmax`: 2 actions × 3 seeds, four-attempt cap, finite metrics,
  paired replays, zero replay hash mismatches, and reproducible rerun.
- [completed] Beat `FIXED_DEV_BEST` with the transferable controller on the
  declared `stringmax` development goal (mean `auc_by_generation` margin
  >=0.25 under the same four-attempt budget); achieved with a measured margin
  of `+0.6111` and three of three seed-level best-score wins.
- [completed] Make the local mock development adapter deterministic under the
  declared run seed and keep evaluator wall-clock telemetry out of replay
  decision identity, while preserving telemetry in result/resource hashes.
- [completed] Add a replay-based development trace collector that converts
  search-side ledger evidence into `freeze_controller.py` input without
  exposing holdout data.
- [completed] Extend the collector to merge multiple development runs while
  retaining source run IDs and ledger hashes for each trace row.
- [blocked-external] Obtain externally frozen model, baseline, holdout, and verifier
  assets; keep the scientific terminal predicate fail-closed until then.

## Errors encountered

| Error | Attempt | Resolution |
| --- | --- | --- |
| `el        if` syntax error after elite-policy patch | 1 | Replaced the corrupted `elif` in `sample_parents`. |
| `pytest: command not found` | 1 | Use the repository's `uv run --with pytest` environment. |
| Bare `python3 -m pytest -q` reported 4 failures | 1 | Treat it as non-canonical; rerun the documented `uv run --system-certs --with numpy --with pytest` command with the workspace-clean test opt-out. Canonical result was `247 passed`. |
| Test insertion temporarily interrupted an existing test (`NameError: path`) | 1 | Moved the existing truncation assertions back into their original test and reran targeted tests. |
| Documentation patch context mismatch | 1 | Applied the state, decision-log, and progress updates as separate patches. |
| Registered run matrix previously named hashes without materialized artifacts | 1 | Require distinct safe paths and independently replay every row artifact. |
| Native GPU curve budget coordinates were not bound to measured ledger totals | 1 | Add observed GPU/model-forward fields and strict resource consistency checks. |
| Baseline cutoff/publication and post-freeze registry mutations were not schema-bound | 1 | Freeze cutoff, timestamps, metadata, and zero mutation counters. |
