# Findings

- The pasted source is preserved as `RESEARCH_V3_TERMINATION_CRITERION.md`.
- The repository already contains `FORGE_RESEARCH_V3_GOAL_PROMPT.md`, which separates engineering readiness from scientific completion and maps the criterion to V3 modules/tests.
- External frozen assets, mandatory baseline conformance, sealed holdout manifests, and an independent read-only verifier remain blockers for `FORGE_RESEARCH_FINISHED`.
- The source criterion must remain immutable; the goal prompt may direct implementation but must not relax its thresholds or terminal states.
- The goal prompt now explicitly maps normative `RESEARCH_INTEGRITY_READY` and the two scientific gates to repository-side `V3_ENGINEERING_READY`, and fixes their conjunction for `FORGE_RESEARCH_FINISHED`.
- Validation passed: criterion-copy immutability check, required-term audit, `compileall`, `git diff --check`, and full suite (`234 passed`).
- TYPE D hardening is now implemented: frozen study manifests hash events/result/evidence; raw bootstrap vectors are regenerated from raw hierarchical inputs; selected-incumbent AUC is joined to replay checkpoint identities; saved terminal state is compared with an independently derived verdict; baseline execution names/source commits/container digests must match exactly; integrity counters require integer zero.
- After hardening, targeted verifier/verdict/manifest tests passed (`26 passed`) and the full suite passed (`239 passed`). External blockers remain unchanged.
- The canonical repository test command is `FORGE_SKIP_WORKSPACE_CLEAN_TEST=1 uv
  run --system-certs --with numpy --with pytest pytest -q`; it currently passes
  `247` tests. A bare system-Python pytest invocation is not authoritative and
  reported four environment/clean-workspace diagnostic failures.
- The goal prompt now includes this canonical execution contract and requires
  evidence type (engineering/mock/external) to be recorded before any state or
  verdict update.
- CLI audit found that historical runs were labeled `FORGE_RESEARCH_V3` even
  when V3 was not enabled; this is now fail-closed metadata (`FORGE_LEGACY`).
  Non-mock V3 requires a frozen controller policy plus registered identity.
- A one-attempt synthetic bundle could previously satisfy the evidence gates;
  the verifier now requires frozen run-matrix seed coverage, exact identity and
  artifact hashes, full capped incumbent curves for receipt-bearing runs, and
  an external verifier receipt before scientific termination.
- Native GPU-AUC is now an explicit result schema with all 13 preregistered
  fractions; SAME_MODEL must state `not_applicable` rather than leave the field
  absent.
- Final hardening validation: `258 passed`, compileall, JSON validation, and
  `git diff --check` pass; external authority blockers remain unchanged.

- Registered run artifacts are now materialized per matrix row. The verifier
  rejects missing, aliased, tampered, path-escaping, identity-inconsistent, or
  hidden-feedback event/result artifacts before verdict calculation.
- Native GPU-AUC now distinguishes preregistered budget coordinates from
  observed GPU-seconds and model-forward time, and binds both totals to the
  append-only resource ledger.
- Baseline validation now freezes the cutoff/publication predicate,
  source-observation timestamps, track/category metadata, and zero
  post-unblinding registry changes. Mandatory conformance evidence remains an
  external blocker.
- Latest targeted validation: `40 passed`; canonical full suite: `265 passed`;
  compileall, protocol JSON validation, and `git diff --check` pass.
- Extension authorization is now bound to a frozen external primary-phase
  status (`positive|negative|extend`), and evidence seed IDs are cross-checked
  against the matrix rather than trusted independently.
- A positive gate boolean without its preregistered numeric attestations was a
  potential verifier weakness. `validate_metric_gate_claims` now checks all
  Q1–Q4, mechanism, and replication thresholds; a fixture with a forged
  `same_model_superiority_ready=true` is blocked.
- Latest canonical validation after this hardening: `268 passed`; compileall,
  protocol/traceability validation, criterion immutability, and engineering
  verifier all pass.

- Current canonical validation: `292 passed`; the study-verifier subset is
  `29 passed`, and model/metric boundary suites pass. Compileall, protocol and
  traceability JSON validation, `git diff --check`, and criterion-copy
  immutability pass.
- Bootstrap control inputs now reject boolean/non-integer replicate counts and
  RNG seeds. Frozen model manifests reject whitespace-only and padded floating
  aliases.
- The read-only verifier rejects top-level asset symlinks, run-matrix symlink
  and resolved-path/inode aliases, and filesystem stat/hash races as integrity
  failures. These checks strengthen RI-003/REG-001 but do not create external
  study evidence.
- A fresh scan of the repository and Codex attachments found no external
  frozen model manifest, sealed holdout bundle, mandatory baseline conformance
  evidence, external verifier deployment, or `external_verifier_receipt.json`.
  Therefore `V3_ENGINEERING_READY=true` and `FORGE_RESEARCH_FINISHED=false`.
- Latest canonical validation after controller/archive/model-routing wiring:
  `297 passed`;
  targeted archive/controller coverage is `3 passed`, compileall, protocol and
  traceability JSON validation, `git diff --check`, criterion-copy immutability,
  and the engineering verifier all pass. Archive selection is search-side only
  and does not create external scientific evidence.
- Traceability now names the executable controller/operator/loop paths and their
  archive/parent-selection tests; this is repository correspondence evidence,
  not external holdout evidence.
- Model provenance audit found and closed a non-mock fallback: a controller
  identity without a callable adapter is now rejected instead of silently using
  the cheap caller. Mock compatibility is explicit and negative-tested.
- The ledger now binds observed generation model identity to the controller
  action metadata, preventing a mismatched adapter response from being treated
  as the pinned model; absent telemetry remains explicitly incomplete.
- Final validation after resource-identity binding: canonical suite `298
  passed`; engineering verifier, compileall, JSON validation, diff check, and
  criterion-copy immutability all pass.
- Bundle verifier JSON parsing was hardened against Python's permissive
  non-standard numeric constants; all three constants are rejected before any
  frozen asset can be considered valid.
- Final validation after strict JSON parsing: canonical suite `301 passed`;
  engineering verifier, compileall, JSON validation, diff check, and criterion
  copy immutability all pass.
- Strict JSON semantics are shared across canonical protocol/registry/
  traceability/controller/ledger/replay loaders, avoiding divergent acceptance
  of non-standard numeric constants.
- Hidden-event denylist scanning now reports strict-JSON violations instead of
  silently skipping malformed/non-finite lines before replay.
- Freeze tools now share strict JSON parsing, preventing pre-freeze draft
  manifests or development traces from accepting non-finite constants.
- Final validation after hidden-event parser unification: canonical suite `303
  passed`; engineering verifier, compileall, JSON validation, diff check, and
  criterion-copy immutability all pass.
- Final validation after strict-loader unification: canonical suite `302
  passed`; engineering verifier, compileall, JSON validation, diff check, and
  criterion-copy immutability all pass.
- Latest validation after freeze-tool strict-input hardening: canonical suite
  `305 passed`; engineering verifier, compileall, JSON validation, diff check,
  and criterion-copy hash verification all pass. This is engineering evidence
  only; no external frozen study assets or verifier receipt are present.
- Protocol, result-schema, task-manifest, and run-matrix hardening closed
  repository-side drift paths; canonical suite now passes `314` tests. The
  engineering verifier remains `v3_engineering_ready=true` and
  `research_finished=false` because external frozen assets and receipt are
  still absent.
- Ablation behavior is now causally distinguished in tests: `FIXED_DEV_BEST`
  is state/cost independent after development fitting, and
  `COST_UNAWARE_CONTROLLER` ignores estimated generation cost while preserving
  budget feasibility. Registered results include complete controller
  action/state provenance, and the verifier binds action payloads to ledger
  `attempt_started` records.

## FORGE_SIMPLE_GOAL_V2

- Cursor has no first-class Codex-style Goal Mode. Persistence is the V2
  contract file plus `.cursor/rules/forge-goal-loop.mdc`.
- V1 already passed at margin `+0.6111`. V2 raises the gate to `+1.0` as a
  new study version; V1 is frozen.
- `auc_by_generation` is the mean of the generation best-so-far curve. Early
  high scores raise the metric more than a late peak with the same terminal
  `best_score`.
- Current primary packing is `1 → 2 → 1` (cheap scout, expensive middle,
  cheap tail). FIXED_DEV_BEST holds one development-best arm constant.
- First V2 hypothesis if the current margin stays ~0.61: front-load the
  expensive arm when leftover budget can still finish with cheap arms
  (`2 → 1 → 1`), so generation-1 best-so-far can reach 9 instead of ~8.
- After three measured hypotheses, V2 is a recorded failure. Structural
  reason: `auc_by_generation` needs a generation-1 score of 9 on enough
  seeds, but that 9 is an interaction of `SMALL` then `STRONG`. Improving
  the cheap scout enough to hit 9 early also makes `FIXED_DEV_BEST` adopt
  SMALL. Front-loading STRONG never hits 9 and loses the mix. The V1
  margin `+0.6111` is the best measured point on this frozen matrix.
- V3 splits the question V2 mixed together. Absolute lift is vs the
  frozen primary mean 8.4444, not vs a +1.0 relative margin. Relative
  non-regression stays at V1's +0.25. A run that raises `best_score` to
  10.333 while collapsing the margin to 0 is not a pass.
- V3H6 sequential mid-slot (second offspring mutates the first) dropped
  seed 1 from `[7,9,9]` to `[7,8,8]`. Parallel mid-slot draws are load-
  bearing; do not serialize them.
- V3H7 tail temperature +0.7 erased seed 0's gen-3 ten. That ten is a
  base-temperature SMALL draw; more edits reroll it. Opening and mid
  are different MockLLM instances, so tail-only changes cannot lift
  gen-1/gen-2. All-gen-3-tens from H3 is still only 79.
- V3H8 mid-slot concat of the top two archive strings passes judgment
  (9.3333, margin +1.50) because `hello world`+`abc` already has 10
  unique letters. Confirmation failed: FIXED on seeds 3,4,5 is 8.8333
  and seed 3 primary mid only hit 8. Concat is a stringmax-seed artifact,
  not a general search result. Mock pass is still not research evidence.
- V3H9 scoring the raw concat on the last mid slot dropped seed 1 to
  best 7. After a failed opening, top-2 are both 7-unique strings; the
  unmutated concat adds no letter. The H8 ten was a mock edit of that
  concat. Do not replace a mutated mid draw with the crossover text.
- V3H10 passed the written V3 gates by concatenating the incumbent
  with the generation-0 seeds (`hello world` + `abc`) before the
  expensive mid mutation. H8's top-2 concat dropped `abc` after a
  failed scout; that is why confirmation seed 3 stalled at 8. This is
  still a stringmax-seed artifact under MockLLM, not a scientific
  controller result.
