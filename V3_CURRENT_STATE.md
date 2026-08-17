# Forge Research V3 — Current-State Report

Snapshot: 2026-08-10 (Asia/Tokyo)

This is an engineering snapshot, not a scientific result. The normative
termination contract remains [`RESEARCH_V3_TERMINATION_CRITERION.md`](RESEARCH_V3_TERMINATION_CRITERION.md), which is immutable.

## Source and verification evidence

- Pasted criterion source SHA-256: `3db77cc9938faf9676e5ba1286c0e33f2f30e3c29bd9ff59edce95abd3d56474`.
- Repository copy SHA-256: `92379ea91a18078bc02d6ab7eefc97e863829961e21e0ecf2eb698cf3567692a`.
- The repository copy equals the source plus one terminal POSIX LF byte.
- Full test command: `FORGE_SKIP_WORKSPACE_CLEAN_TEST=1 uv run --system-certs --with numpy --with pytest pytest -q`.
- Latest canonical full-suite result: `340 passed`.
- `compileall`: PASS; `git diff --check`: PASS.
- Engineering verifier: `mock_dry_run=true`, `replay_recomputes_attempts=true`,
  `resource_ledger_valid=true`, `resource_telemetry_complete=true`,
  `metric_engine_smoke=true`, `verdict_engine_smoke=true`,
  `traceability_valid=true`, `baseline_conformance=false`,
  `v3_engineering_ready=true`, `research_finished=false`.
- Canonical V3 CLI now labels legacy runs as `FORGE_LEGACY`, refuses non-mock V3
  execution without a frozen controller policy and complete run identity, and
  records V3 controller provenance when supplied. `tools/freeze_controller.py`
  emits a self-hashed development-only policy manifest.
- The read-only bundle verifier now requires a frozen `run_matrix.json` with
  exact primary/extension seed coverage, validates registered run identity and
  native GPU-AUC schema, and requires an externally bound
  `external_verifier_receipt.json` before any scientific terminal state can be
  considered finished. Repository-only/mock bundles therefore remain blocked.
- Each frozen run-matrix row now names distinct bundle-relative event/result
  artifacts. The verifier resolves every path inside the bundle, checks the
  declared hash, validates the result identity, replays every event stream,
  and rejects a missing, aliased, tampered, or hidden-feedback artifact.
- Registered run artifacts now require a finite, non-negative evaluator-call
  budget limit, and all registered rows must use exactly the same limit;
  missing or unequal limits are integrity failures. The study-verifier fixture
  suite includes a negative unequal-budget case.
- Event replay now recomputes parent-child link coverage, deterministic lineage
  cycle detection, and evaluator-hack-audit coverage. Registered artifacts
  must report all of these at 1.0 with zero lineage cycles.
- Native-compute results now carry measured GPU-seconds and model-forward time;
  the verifier binds both values and the GPU-curve endpoint to the generation
  resource ledger. The baseline registry now freezes its cutoff, publication
  predicate, source-observation timestamp, track/category metadata, and zero
  post-unblinding additions/deletions.
- The matrix also records the external primary-phase status
  (`positive|negative|extend`); extension rows are rejected unless the status
  is `extend`, and evidence seed claims must match the frozen matrix.
- Positive metric gate booleans are no longer trusted as standalone claims:
  the verifier requires finite preregistered means, CI bounds, rates, counts,
  and threshold inequalities for every claimed Q1–Q4/mechanism/replication
  gate. The protocol now carries both a compact threshold table and an
  expanded `positive_gate_contract` binding each evidence field to its
  comparison operator and threshold; protocol loading rejects alias/value
  drift before the verdict engine consumes it. Replication also requires the
  registered 100 independent replay runs, positive effect signs for all three
  model profiles, and zero decision/result-recomputation hash mismatches.
- `RESEARCH_INTEGRITY_READY` now explicitly requires the evidence flag
  `heldout_problem_family_requirements_pass`; task-manifest validation and the
  verdict API must agree before a bundle can be considered integrity-ready.
- The public engineering audit now runs both an independent `NATIVE_COMPUTE`
  ledger/replay fixture and the actual CLI/loop native path with explicit mock
  A100 allocation and model-forward telemetry; this is plumbing evidence only
  and does not certify a native study.
- Development trace artifacts are strict canonical JSONL: the Forge matrix
  writer and `tools/collect_controller_traces.py` emit exactly one record per
  line, reject non-finite quality gains, and preserve source-run provenance.
- Research metric inputs fail closed on boolean/non-finite hidden scores and
  invalid percentile bounds; these checks protect normalization and bootstrap
  recomputation without changing any preregistered threshold or metric.
- V3 attempt metadata now records the visible incumbent score at generation
  start. The development trace collector uses this seed/generation baseline,
  so first-generation `quality_gain` is measured rather than hard-coded to
  zero. A fresh 3-problem × 2-seed mock matrix produced non-zero gains for the
  STRONG action while retaining `scientific_evidence=false`.

## Current implementation facts

The initial project inventory in the goal prompt is now partly superseded by
the V3 implementation. The current facts are:

- `forge/loop.py` remains a deterministic Python search loop, but V3 now has a
  frozen `TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1` interface plus the three
  registered ablations. The controller is trained only from `split=dev` traces
  and cannot update from holdout feedback.
- Legacy mode still charges only successful cheap calls for its historical
  budget. V3 mode separately charges every generation slot as one attempt,
  including model failures, empty responses, duplicates, static rejections,
  evaluator failures, and timeouts.
- `archive.jsonl` is a candidate archive, not the complete attempt history.
  V3 `events.jsonl` is the append-only, hash-chained attempt/checkpoint ledger
  used for replay and budget accounting.
- `forge/research_metrics.py` implements attempt-AUC, final quality,
  GPU-fraction AUC, OOD, champion/oracle deltas, and paired hierarchical
  bootstrap. Hidden-test vectors and an externally frozen result matrix are
  still absent, so these are tested primitives rather than scientific results.
- The manifest layer now hashes protocol, model, task, evaluator, container,
  prompt/decoding, metrics, baseline, run-matrix, and result artifacts. It does
  not manufacture the external authority, sealed holdout, or baseline
  conformance evidence required by the registered study.
- The subprocess/AST sandbox is an explicit local gate and threat-model
  fixture, not a claim of an independently secured production boundary.
- Local `bench_obp`/`bench_tsp` packs can be executed at mock scale when their
  declared NumPy dependency is available; they do not satisfy V3 holdout
  breadth, distribution-shift, or sealed-hidden-test requirements.
- The read-only study verifier is an engineering-side verifier. Without the
  externally bound receipt and frozen assets it must remain
  `research_finished=false`.
- Its machine-readable report now exposes the four terminal-state golden
  outputs (`STRONG_POSITIVE`, `CLEAN_FALSIFICATION`, `INCONCLUSIVE`, and
  `BLOCKED_INTEGRITY_FAILURE`) instead of only a boolean smoke result.
- The V3 AST/sandbox gate now rejects numpy file-backed helpers (`load`,
  `memmap`, `fromfile`, `save`, and related imports/attributes) while retaining
  numerical `numpy.linalg` and `numpy.random` operations; this is an explicit
  defense-in-depth measure, not a replacement for external container isolation.
- The public engineering report executes a sandbox smoke that confirms safe
  numeric execution and explicit denial of `open` and `numpy.load`.
- The read-only verifier rejects top-level bundle symlinks and registered run
  artifact symlink/path/inode aliases before reading or hashing them; this
  preserves the bundle-containment and distinct-artifact invariants.
- Run-matrix `stat`/hash filesystem errors are converted to integrity failures
  instead of escaping as verifier exceptions.
- The canonical suite includes freeze-tool, metric-boundary, and controller
  trace non-finite-input coverage; the latest full run passed all `340` tests.
- Canonical V3 JSON loaders, including the read-only bundle verifier, reject
  non-standard numeric constants (`NaN`, `Infinity`, `-Infinity`) at parse
  time; protocol, baseline, traceability, controller, ledger, and replay paths
  share the strict semantics.
- Hidden-event denylist scanning uses the same strict decoder and reports
  malformed/non-finite lines explicitly before replay.
- Freeze tools for study manifests and controller policies use the same strict
  decoder for draft inputs and development traces.
- Protocol loading now freezes the complete preregistered thesis, development
  problem set, holdout minima, primary/extension seed IDs, baseline sets and
  cutoff, ablation set, bootstrap controls, metric names, and GPU fractions;
  malformed types and integer-looking floats fail closed.
- Result GPU-AUC validation is bound to the validated protocol contract, task
  manifests can be checked against the frozen development problem set, and a
  run matrix rejects extension rows when external `extend` authorization is
  absent.
- Latest canonical run after these checks passed all `340` tests; engineering
  readiness remains plumbing evidence only.
- Frozen model identity validation rejects whitespace-only and whitespace-padded
  floating aliases before any model asset can be treated as pinned.
- In V3 controller runs, registered parent-selection policies now affect parent
  sampling, and mutation-operator/reflection-depth choices are explicit in the
  generator prompt. A caller mapping can route a pinned generator-model
  identity to its frozen adapter. Registered archive-sampling policies now
  select only from search-side archive state (`round_robin`, `elite`,
  `score_spread`, or `random`); legacy prompt and sampling defaults remain
  unchanged, unknown policies fail closed, and non-mock V3 runs reject a
  missing callable adapter for the selected generator identity. When observed,
  generation resource telemetry must also match the controller-selected model
  identity; mismatches fail during ledger append/replay.
- Controller ablation behavior is now explicitly tested: `FIXED_DEV_BEST` selects
  one development-best action independent of holdout state/cost, while
  `COST_UNAWARE_CONTROLLER` ignores estimated generation cost but retains budget
  feasibility. Result bundles carry complete controller action/state records,
  and the verifier cross-checks action payloads against `attempt_started` events.
- The Forge execution path now treats parametric mutation as an explicit
  non-LLM generator (`PARAM_MUTATION`) and resumes V3 runs at the next unseen
  generation, restoring controller action/state provenance from the append-only
  ledger. A two-generation resume fixture verifies that generation-slot keys
  remain unique.
- `tools/collect_controller_traces.py` now derives development-only controller
  traces from search-side ledger checkpoints and measured generation cost; the
  output is consumable by `tools/freeze_controller.py` without opening a
  holdout pack. It accepts multiple development ledgers positionally and
  preserves each source run identity/hash in the trace rows.
- `forge/development.py` and `tools/run_controller_development.py` now execute
  a seed-bound action-by-problem development matrix, merge its ledgers, freeze
  the primary controller and all registered ablations, and emit problem-local,
  seed-separated comparison cells. Each frozen policy is then reloaded and
  replayed on every development problem; those selected-action sequences are
  kept separate from fitting traces. The mock adapter is deterministic for a
  declared seed, while observed wall-clock telemetry remains outside decision
  identity. This path has been exercised on the local `stringmax`,
  `_probe_newproblem`, `bench_obp`, and `bench_tsp` packs in mock mode.
- `forge/model_routes.py` and the CLI's `--controller-model-routes` option now
  bind every frozen controller generator identity to an explicit adapter tier;
  missing identities fail closed, and the route manifest ID/content hash are
  recorded in `manifest.json`. This is adapter routing provenance, not a
  substitute for externally frozen model weights, tokenizer, runtime, or
  endpoint assets.
- `forge/models.py` now provides strict model-manifest loading, and the CLI's
  optional `--model-manifest` verifies that every route hash matches the actual
  validated frozen file. Non-mock V3 CLI executions require this input along
  with the controller policy and route manifest; mock runs remain available for
  plumbing tests.
- The event ledger now closes its event vocabulary, rejects non-finite JSON
  payloads and malformed finished-attempt metadata, and keeps legacy `-inf`
  failure sentinels out of V3 artifacts as explicit missing scores.
- Search-visible task manifests require an explicit
  `hidden_content_in_search_bundle=false`; omitted or affirmative values are
  rejected rather than silently filtered.
- Hierarchical bootstrap preserves typed seed identities and resamples rows as
  explicit hidden-test clusters when the registered cluster key is present;
  replicate counts and RNG seeds are typed and fail closed before sampling.
- OBP/TSP evaluators reject positive/negative infinity as constraint failures,
  and the verdict engine rejects unknown Q statuses and boolean zero counts.

## Requirement mapping

| Area | Current evidence | Status | Remaining condition |
| --- | --- | --- | --- |
| Protocol/schema/verdict | `protocol/forge_research_v3.json`, `forge/protocol.py`, `forge/verdict.py`, tests | Implemented opt-in | External freeze still required |
| Attempt/event ledger | `forge/ledger.py`, `forge/loop.py`, `forge/replay.py` | Implemented opt-in | Native study artifact required; mixed/unknown tracks and floating model aliases now fail closed |
| Resource accounting | `forge/resources.py`, generation/evaluator separation, explicit missing fields, mock tokenizer telemetry, fail-closed V1/V2 evaluator failure accounting, non-finite score rejection, floating model-identity rejection | Implemented opt-in | Observed native GPU/model-forward telemetry required |
| Lineage and replay | AST/diff/parent digests; decision, result-recomputation, and resource hashes; replay requires one valid incumbent checkpoint per finished attempt | Implemented opt-in | Must be exercised on registered runs |
| Manifest freeze | `forge/manifest.py`, `tools/freeze_manifest.py`, study authority/holdout locator validation, result/evidence/event/run-matrix content hashes | Partial | Exact external model/task/evaluator/container/prompt assets and externally authorized sealed locator absent |
| Holdout barrier/sandbox | `forge/holdout.py`, `forge/sandbox.py`, verifier-owned unblinding capability, deterministic candidate hack audit, negative tests | Partial | Sealed external holdout and external security authority absent |
| Baseline registry | `forge/baselines.py`, `protocol/baseline_registry_v3.json`, study result/container identity cross-check | Partial / external blocker | Cutoff/publication/schema checks are fail-closed; mandatory peer/open native smoke and adapter conformance evidence remain absent |
| Controller/ablations | `forge/controller.py`, `forge/loop.py`, `forge/operators.py`, `forge/development.py`, `forge/model_routes.py`, `forge/models.py`, `tools/run_controller_development.py`, `FIXED_DEV_BEST`, `NO_TRANSFER_PRIOR`, `COST_UNAWARE_CONTROLLER`, result provenance checks | Development matrix and explicit CLI adapter/model-manifest routing executable; research use partial | Frozen external study training split and holdout runs not performed |
| Anytime/OOD/GPU metrics | `forge/research_metrics.py`, `forge/result_schema.py`, `forge/study_verifier.py` raw bootstrap input/vector recomputation, selected-incumbent AUC join, GPU-fraction schema and ledger consistency | Partial | Hidden-test result matrix and native GPU curves absent |
| Public read-only verifier | `forge/study_verifier.py`, `tools/verify_v3_research.py`, result/evidence/event/run-artifact hash and terminal-state checks | Implemented | External authority deployment absent |

## Terminal state

`V3_ENGINEERING_READY = true` and `FORGE_RESEARCH_FINISHED = false`.

The engineering predicate means that the public mock protocol, replay,
resource ledger, schema, traceability, and repository-side read-only verifier
operate end to end. It deliberately does not certify external baseline
eligibility, sealed holdout assets, native telemetry, or scientific evidence.

No external frozen study bundle, sealed holdout, eligible mandatory baseline set,
external verifier receipt, or final terminal verifier result exists in this
repository. These are blockers
for the registered research outcome, not evidence of a negative scientific
result. They must remain unresolved rather than being replaced with mock or
synthetic data.

The repository-side verifier now independently recomputes the registered
bootstrap vectors from frozen raw rows, checks the selected-incumbent AUC
against replay checkpoints, requires content hashes for events/result/evidence/
run-matrix and every registered run artifact, checks exact seed and run identity
coverage, validates GPU-AUC schema and native resource-ledger consistency, and
compares the saved terminal state with the evidence-derived verdict. These
checks strengthen engineering readiness but do not create external study
evidence; the external verifier receipt and external assets are still absent.
