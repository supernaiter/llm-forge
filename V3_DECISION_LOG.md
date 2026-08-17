# Forge Research V3 — Decision Log

## DL-001 — Normative criterion is immutable

The pasted termination criterion is preserved verbatim except for one terminal
POSIX LF byte required by the repository text convention. Any future source
hash change creates a new study version; the criterion is never edited to make
an observed result easier to satisfy.

## DL-002 — Missing telemetry is not imputed

Production/API/CLI adapters leave unavailable token, GPU, or model-forward
fields as `null` with an explicit `missing` list. The mock fixture reports
observed counts under the declared `MOCK_WHITESPACE_V1` tokenizer, but mock
telemetry is not native-study evidence.

## DL-003 — Baseline failure is not a zero score

An unresolved or non-conformant baseline remains in a blocked/ineligible state.
It is never converted into a zero score or counted as a Forge win.

## DL-004 — Hidden evaluation is verifier-owned

Search-side code receives no hidden instances, hidden scores, hidden paths, or
side-channel feedback. Final unblinding requires an external authority and is
not simulated inside the repository.

## DL-005 — Engineering and scientific completion are separate

Passing tests, public mock dry runs, development tasks, or a single seed do not
produce `FORGE_RESEARCH_FINISHED`. Only an external verifier returning
`STRONG_POSITIVE` or `CLEAN_FALSIFICATION` can terminate the registered study.

## DL-006 — Current external blockers

Mandatory native baseline smoke/conformance evidence, frozen model/task/
evaluator/container/prompt manifests, sealed holdout data, native resource
telemetry, registered primary/extension runs, and an external read-only final
verifier remain outstanding. No fabricated replacement is permitted.

## DL-007 — Attempt-indexed replay requires incumbent checkpoints

Every finished V3 generation attempt must have exactly one valid
`incumbent_selected` checkpoint containing its attempt sequence, candidate
SHA-256, and finite search-side score. Public replay and the frozen-bundle
verifier reject streams that omit, duplicate, or malformed these checkpoints;
this prevents an incomplete selected-incumbent curve from being mistaken for a
valid `AUC_ATTEMPT` result. Event appends are transactional so a rejected event
cannot leave a corrupt JSONL suffix behind.

## DL-008 — Non-finite candidate scores fail closed

V3 score adapters must return a finite numeric score for a live candidate and
must use a registered failure status for rejected candidates. Non-finite live
scores, unknown statuses, and inconsistent `(alive, status)` pairs are
classified as candidate failures rather than entering the incumbent curve.

## DL-009 — Engineering readiness is separate from research readiness

`V3_ENGINEERING_READY` is true when the repository-side protocol, public mock
dry run, replay, resource ledger, traceability, and read-only verifier pass.
External baseline eligibility, sealed holdout/model assets, native telemetry,
and external final authority are not local engineering prerequisites; they
remain explicit blockers for `FORGE_RESEARCH_FINISHED`.

The engineering verifier also runs a deterministic public smoke for the metric,
hierarchical bootstrap, and terminal-verdict primitives; this smoke is not
treated as holdout evidence.

## DL-010 — Unblinding capability is verifier-owned

`SealedHoldout` cannot mint a hidden-evaluation capability without the
`VerifierAuthority` instance bound when the holdout is constructed. Search-side
views contain no hidden content or count, and authority-less or wrong-authority
unblinding attempts are rejected and counted as violations. This explicit
capability API complements, but does not replace, external process isolation.

Hidden evaluator exceptions and non-finite scores are also integrity failures;
they cannot be converted into a numeric result or silently omitted from the
holdout evidence.

## DL-011 — Frozen study manifests commit external authority and holdout

Generic asset hashes are insufficient to certify a registered study. The study
manifest must also contain a resolved external authority identity and an
opaque sealed-holdout locator; missing or unresolved values produce
`BLOCKED_INTEGRITY_FAILURE` before any scientific verdict is considered.

## DL-012 — Bootstrap CI values are recomputed from raw vectors

A frozen result bundle must carry the preregistered 20,000-replicate seed,
hierarchy, oracle-reselection attestation, and raw vectors for the registered
delta statistics. The public verifier recomputes each percentile CI high and
rejects a bundle when a reported CI is changed without changing the underlying
raw evidence consistently.

## DL-013 — Baseline execution identity is registry-bound

The frozen result must record each eligible baseline's source commit and
container digest. The public verifier compares those values with the baseline
registry and `container_manifest`; any mismatch blocks the bundle rather than
allowing an identity-swapped comparison.

## DL-014 — Raw bootstrap evidence is independently recomputed

The frozen metrics summary must include raw hierarchical bootstrap inputs for
each registered delta statistic. The read-only verifier regenerates all 20,000
replicates with the preregistered seed and hierarchy, then compares the full
vectors and percentile highs. A reported CI cannot be repaired by changing only
the derived vector or summary field.

## DL-015 — Attempt AUC is joined to replay identity

An unblinded result must include one normalized hidden-test quality per selected
incumbent, in the exact order and candidate digest sequence recorded by the
event ledger. The verifier recomputes the mean AUC and rejects a curve that is
missing, reordered, or disconnected from replay checkpoints.

## DL-016 — Result, evidence, and terminal state are content-addressed

Frozen study manifests commit SHA-256 hashes for `events.jsonl`, `result.json`,
and `evidence.json`. The verifier also recomputes the terminal state from
evidence and requires the saved `result.json` state to match. Integrity counters
are strict integer zeros; boolean `false` is not accepted as numeric zero.

## DL-017 — A study bundle has exactly one frozen comparison track

`SAME_MODEL` and `NATIVE_COMPUTE` use different attempt caps and resource
denominators. A replay stream containing an unknown or mixed track is therefore
invalid rather than silently receiving the same-model cap. Result bundles must
also repeat the track and its cap exactly.

## DL-018 — Floating model aliases are rejected at the ledger boundary

Attempt-start and resource telemetry records reject `latest`, `default`,
`main`, `master`, `floating`, and `unpinned` model identities. A model manifest
must carry the exact revision; a runtime alias cannot become evidence merely
because it appears in a hash-chained event.

## DL-019 — Controller provenance is bound to development tasks

Controller result bundles must include a recognized mechanism ID, a pinned
policy hash, non-empty training problem IDs, and zero holdout update attempts.
The public verifier checks that every training ID belongs to the frozen
development task list; a holdout task cannot be relabeled as development in a
result report.

## DL-020 — Legacy execution is never V3 research evidence

The historical CLI path remains available for product compatibility, but its
manifest is now labeled `FORGE_LEGACY` with `research_eligible=false`. A
non-mock `--protocol-v3` invocation requires both a frozen controller policy
and a complete registered run identity. A public mock may exercise the ledger,
but it cannot silently become a scientific run.

## DL-021 — Seed and run completeness are independently attested

A study bundle must include a frozen `run_matrix.json`. The verifier checks the
exact primary seed set (101–112), optional authorized extension set (113–124),
unique run IDs, holdout problem/distribution/model/track identity, and the
current events/result hashes. Counts alone are insufficient because duplicated
favorable seeds must not satisfy the registration gate.

## DL-022 — GPU-AUC and external termination are explicit

NATIVE_COMPUTE results must carry all thirteen preregistered GPU-budget
fractions and a recomputable `AUC_GPU`; SAME_MODEL explicitly marks this metric
`not_applicable`. A repository-only bundle cannot terminate. Before
`research_finished=true`, an externally bound read-only verifier receipt and a
full capped incumbent curve are required. Missing receipt, partial attempt
curve, or unresolved authority remains `BLOCKED_INTEGRITY_FAILURE`.

## DL-023 — Every registered run must have a distinct materialized artifact

The run matrix is not allowed to stand as a list of repeated hashes. Each row
now names a distinct bundle-relative `events_path` and `result_path`; the
verifier checks lexical containment, file existence, exact row hash, result
identity, event-ledger replay, track, cap, resource hashes, and hidden-event
scan for every row. A missing, aliased, tampered, or content-inconsistent row
blocks the bundle before any scientific verdict is considered.

## DL-024 — Native GPU metrics are bound to observed ledger telemetry

For `NATIVE_COMPUTE`, `gpu_seconds` in the thirteen curve rows remains the
preregistered budget coordinate, while `observed_gpu_seconds` and the final
`native_gpu_seconds_observed` are measured values. The result also records
observed model-forward milliseconds. Both totals and the curve endpoint must
match the generation resource ledger; missing or imputed telemetry is not
accepted. `SAME_MODEL` cannot carry native telemetry fields.

## DL-025 — Baseline cutoff and eligibility metadata are frozen

The registry now requires the exact `2026-08-01T00:00:00Z` cutoff, explicit
publication-before-cutoff status, source-observation timestamps no later than
the cutoff, track/category metadata, and integer-zero post-unblinding
addition/deletion counters. These checks improve the local schema but do not
create the mandatory native smoke, adapter-conformance, license, or external
authority evidence; baseline conformance therefore remains false.

## DL-026 — Extension authorization is an external phase result

The frozen run matrix now records `primary_verifier_status` as exactly
`positive`, `negative`, or `extend`. Extension seed rows are permitted only
when that status is `extend`; evidence seed claims must match the matrix. This
prevents a local bundle from silently adding favorable extension seeds after a
primary result and preserves the criterion's one-shot primary/extension rule.

## DL-027 — Positive gate booleans require numeric attestations

`metrics_summary` cannot claim `same_model_superiority_ready`, final quality,
OOD, compute efficiency, mechanism validation, or replication merely by
setting a boolean. The verifier now requires the corresponding preregistered
means, confidence bounds, rates, counts, and strict inequalities to be finite
and passing. The machine-readable protocol records the positive-gate threshold
schema; missing or contradictory numeric evidence blocks the bundle.

## DL-028 — Positive-gate relations are protocol-bound

The compact preregistered threshold table used human-facing `*_min`/`*_max`
names and did not by itself bind every verdict field or comparison relation.
`protocol/forge_research_v3.json` now carries an expanded
`positive_gate_contract` for all 46 positive-gate requirements. Each rule names
the exact evidence field, operator (`ge`, `gt`, `le`, `eq`, or `bool`), and threshold;
alias keys in the compact table must have equal values. `forge/verdict.py`
loads this contract rather than maintaining a second literal table. Invalid
operators, missing fields, or threshold drift fail at protocol load time.

The contract also includes the criterion's replication evidence: at least 100
independent replay runs and no replay decision-hash mismatches. A result cannot
claim `replication_ready` while reporting only seed and model counts.

## DL-029 — Structural lineage coverage is replay-derived

AST and diff digests alone did not prove that every accepted candidate had a
complete parent link or that the ancestry graph was acyclic. `lineage_audit`
now rebuilds the candidate graph from the event ledger, reports link/cycle/hack
audit coverage, and deterministically rejects cycles during replay. Registered
run artifacts must bind those summary values to the result and report coverage
1.0 with zero lineage cycles; missing coverage remains a diagnostic failure in
development fixtures rather than being silently promoted to research evidence.

## DL-030 — Holdout structure is an integrity predicate

The task-manifest validator already rejects a narrow or overlapping holdout,
but the verdict evidence did not explicitly carry that result. The integrity
contract now requires `heldout_problem_family_requirements_pass == true`, so a
bundle cannot claim research integrity while omitting the 10-problem, family,
unseen-family, external-pack, or shift-coverage attestation.

## DL-031 — Registered evaluator budgets must be identical

The criterion requires equal evaluator budgets across compared runs. A result
that omits `resource_summary.budgets.evaluator.calls.limit`, reports a non-finite
or negative limit, or uses a different limit in another registered run cannot
support a fair comparison. The verifier now checks this field for every
materialized run artifact and fails closed on any missing/invalid/mismatched
limit; the fixture suite contains an unequal-budget negative test.

## DL-032 — Public audit must exercise both comparison tracks

The public engineering verifier previously exercised only the `SAME_MODEL`
mock run. That was insufficient to demonstrate the native resource path named
by the protocol. It now creates a separate tiny `NATIVE_COMPUTE` ledger with
explicit mock A100 allocation and model-forward telemetry, checks an
independent replay, and runs the actual CLI/loop path using the opt-in
`FORGE_MOCK_NATIVE_TELEMETRY=1` fixture adapter. These values are plumbing
observations only and are never treated as native scientific evidence.

## DL-033 — Golden terminal states are reported explicitly

A boolean verdict smoke was insufficient to show that all four protocol
terminal states remained distinct. The engineering report now binds named
outputs for `STRONG_POSITIVE`, `CLEAN_FALSIFICATION`, `INCONCLUSIVE`, and
`BLOCKED_INTEGRITY_FAILURE`, with a test asserting the exact mapping.

## DL-034 — Allowed numpy must not become a hidden-file channel

The V3 candidate policy permits `numpy` for benchmark arithmetic, but the
module also exposes file-backed readers/writers. A candidate could otherwise
reach hidden instances or scores without using `open` or an explicitly denied
module. The AST gate now rejects numpy file-I/O helpers and submodule imports,
plus generic `tofile`/related attributes, while retaining numerical linalg and
random operations. External container/process isolation remains mandatory.

## DL-035 — Public sandbox smoke is dependency-independent

The engineering audit now reports a safe numeric sandbox execution and explicit
denial of `open` and `numpy.load`. Its safe execution probe uses only stdlib
`math` because the documented public command runs under system Python; optional
numpy behavior is tested separately under the canonical dependency environment.

## DL-036 — Ledger schema is closed and finite

An append-only hash chain is not sufficient if replay silently ignores an
unknown event type or accepts `NaN`/`Infinity` payloads. The V3 ledger now
accepts only the four registered event types, rejects non-finite payload
numbers, validates finished-attempt hashes/scores/metadata, and requires
well-formed evaluator event identities. Failure sentinels from legacy scoring
are normalized to an explicit missing score before persistence; valid candidate
scores remain finite-only.

## DL-037 — Search-visible holdout metadata is explicit

Filtering hidden fields from a malformed task manifest could make an asset with
unknown or affirmative hidden-content state appear safe. `search_visible_manifest`
now requires `hidden_content_in_search_bundle` to be exactly `false`; omission
and `true` are integrity failures. This does not replace external sealed
holdout isolation.

## DL-038 — Bootstrap preserves registered cluster identity

The bootstrap implementation previously string-coerced hierarchy keys and
treated each raw row as an independent cluster. It now rejects ambiguous seed
types and resamples explicit `hidden_test_instance_cluster` groups as intact
paired units (with a compatibility fallback for older public `cluster` fixtures).

## DL-039 — Non-finite evaluator outputs never become valid candidates

The OBP and TSP benchmark adapters previously rejected NaN but could accept
positive or negative infinity. Both now use `math.isfinite`, classify all
non-finite returns as `constraint_violation`, and preserve fail-closed metric
inputs. The verdict engine likewise rejects unknown Q statuses and boolean
values masquerading as integer zero counts.

## DL-040 — Bootstrap controls are typed and fail closed

The registered bootstrap uses a fixed integer replicate count and seed. The
public metric helper previously accepted values such as `True` or `1.5` for
those controls, which could make a caller believe it had executed the
registered procedure while using a different control type. The helper now
rejects boolean/non-integer replicate counts and RNG seeds before sampling;
the boundary cases are covered by dedicated negative tests.

## DL-041 — Bundle paths cannot escape or alias the frozen study

The read-only verifier's top-level asset reads now reject bundle-root
symlinks and top-level asset symlinks before opening them. Registered run
artifacts likewise reject symlink paths and detect resolved-path or inode
aliases across matrix rows. A hash match alone is not enough when multiple
registered rows can point to one materialized event/result artifact; such
reuse is an integrity failure.

## DL-042 — Filesystem races fail closed in run-matrix validation

Registered artifact validation now converts `stat` or content-hash I/O errors
into `ProtocolError` rather than allowing a verifier exception to escape. A
materialized bundle that changes or becomes unreadable during verification is
therefore reported as an integrity failure, not mistaken for a valid or
inconclusive study.

## DL-043 — Model identity aliases are trimmed before validation

Frozen model manifests now reject whitespace-only identities and aliases with
surrounding whitespace (for example, `" latest "`). Pinning is evaluated on a
trimmed value for every model field, while the manifest still retains the
original exact bytes for content hashing.

## DL-044 — Controller action fields must affect the V3 search path

Recording a controller action without applying its choices would make the
mechanism observational rather than causal. V3 now maps registered parent
selection policies to deterministic parent sampling, injects the selected
mutation operator and reflection depth into the generation prompt, and accepts
an explicit pinned-model-to-caller mapping for frozen model adapters. Legacy
runs retain their prior random instruction and sampling behavior; unsupported
V3 parent policies fail explicitly instead of being silently ignored.

## DL-045 — Generator model identity routes through an explicit adapter map

The controller's `generator_model` is now executable when a V3 caller supplies
`controller_model_callers`: the pinned identity selects its corresponding
callable for generation, and a non-callable mapping entry is rejected. This
keeps model routing explicit and testable without inventing model manifests or
silently treating a model identity as a cosmetic label. The external frozen
model manifest remains required for scientific runs.

## DL-046 — Archive sampling is an executable search decision

Archive selection is now part of the V3 controller/search path rather than an
unrecorded implementation detail. The loop applies the registered
`uniform/round_robin`, `best/elite`, `diverse/score_spread`, and `random`
policies to search-side archive state, and rejects unknown policies. Archive
sampling never reads hidden-test scores; external frozen controller and model
assets remain required before this wiring can support a scientific run.

## DL-047 — Non-mock controller identities require callable adapter binding

Recording a pinned `generator_model` while silently invoking the default cheap
caller would make model provenance cosmetic and could invalidate same-model or
compute-aware comparisons. V3 now requires the controller model identity to be
present in `controller_model_callers` and mapped to a callable for non-mock
runs. Public mock runs may retain the default caller for plumbing tests; a
non-callable or missing production mapping fails closed.

## DL-048 — Observed generation model identity is bound to controller action

The attempt-start metadata records the controller's selected generator model,
while generation telemetry records the adapter's observed model identity. When
both are present, the append-only ledger now rejects a mismatch during replay
and live append. Missing telemetry is not coerced into a match; it remains an
explicit incomplete-resource condition for the registered-study gates.

## DL-049 — Bundle JSON rejects non-finite constants at parse time

Python's default JSON decoder accepts `NaN`, `Infinity`, and `-Infinity` even
though they are outside strict JSON. The read-only bundle verifier now rejects
these constants before asset validation, preventing a hashed manifest from
carrying non-finite values through a permissive parser. The rejection applies
to every bundle JSON asset and is covered by negative tests.

## DL-050 — Canonical V3 loaders share strict JSON semantics

The strict non-finite check is now shared by protocol, baseline registry,
traceability, controller-manifest, ledger, and replay loaders. This prevents a
repository-side preflight or replay path from accepting a JSON value that the
read-only bundle verifier would reject, while preserving the existing
fail-closed error classes.

## DL-051 — Hidden-event pre-scan uses the replay JSON boundary

The verifier's denylist scan now uses the same strict decoder as ledger
replay. A malformed or non-finite event line is reported explicitly during the
pre-scan instead of being silently skipped while replay diagnoses it later.
Replay remains the authoritative structural validator; the two paths now agree
on the JSON acceptance boundary.

## DL-052 — Freeze tools reject non-finite draft inputs

`freeze_manifest.py` and `freeze_controller.py` now use the canonical strict
JSON decoder for draft manifests, action lists, and development traces. A
non-finite value is rejected before self-hashing or controller fitting, so a
pre-freeze tool cannot emit an artifact with semantics that later verifiers
would reject.

## DL-053 — Protocol constants are validated as one frozen contract

Loading the protocol previously checked only a subset of the registered
holdout, seed, baseline, bootstrap, and metric values. A mutated protocol
could therefore remain structurally readable while changing a scientific
control. The loader now validates the complete preregistered values and
rejects malformed types, including integer-looking floats, before any result
or verdict path consumes them.

## DL-054 — Result GPU curves must use the validated protocol

The result schema had a duplicate local copy of the native GPU fractions and
cap. It now imports the canonical constants and, when a protocol is supplied,
checks that the result curve is bound to that protocol's validated budget and
fractions. Protocol/result drift is an integrity failure rather than a local
schema success.

## DL-055 — Task development identity is protocol-bound

Holdout breadth checks alone do not prove that a registered task used the
preregistered development set. The verifier now optionally binds task-manifest
development IDs and holdout minima to the frozen protocol, rejecting a task
manifest that silently substitutes a different development set.

## DL-056 — Extension rows require explicit external authorization

An extension seed row could otherwise appear in a matrix whose extension ID
list was empty, because only the declared list was checked at the final
coverage step. Matrix validation now rejects any observed extension row unless
the matrix carries the externally authorized `extend` status and exact
extension seed list.

## DL-057 — Ablation names are executable behavioral contracts

An ablation label is not evidence that the intended mechanism was removed.
`FIXED_DEV_BEST` now selects one development-quality champion and returns that
action independently of holdout search state or estimated cost.
`COST_UNAWARE_CONTROLLER` removes estimated-generation-cost utility while
retaining the remaining-budget feasibility guard. Dedicated behavior tests
would fail if either implementation silently inherited the primary
cost-aware/state-dependent policy.

## DL-058 — GPU fractions and model identities use one frozen boundary

GPU-AUC validation now consumes the canonical protocol cap and fraction tuple;
duplicate, non-monotonic, non-finite, out-of-range, or protocol-drifting
fractions are rejected. Model manifests and controller/result provenance also
reject generic or floating aliases such as `small`, `medium`, `strong`,
`latest`, and `default`, including padded variants. A passing local schema check
therefore cannot hide a different resource contract or an unresolved model.

## DL-059 — Controller action/state provenance is replay-bound

Controller provenance is part of the registered result, not a free-form log.
Each generation records a complete action and search state; the verifier
requires the action/state fields, rejects unresolved model identities, and
compares result action payloads with the corresponding ledger
`attempt_started` metadata. The comparison reads the decision replay stream,
where those start events actually live, so an unrelated integrity error (for
example, unequal evaluator budgets) cannot be masked by a false empty-trace
diagnostic.

## DL-060 — Parametric and mock generators have explicit identity semantics

Parametric mutation is a Forge generator but does not invoke an LLM. Its V3
attempts therefore record `model=PARAM_MUTATION` and
`generation_mode=parametric`; controller-selected model names remain action
provenance and are never presented as observed model telemetry. Likewise,
mock adapters may expose `MOCK` telemetry only when `mock_execution=true` is
recorded in the start metadata. A production run still requires exact
controller/adapter identity equality. The same execution path now resumes at
the next unseen ledger generation and restores controller action/state records.

## DL-061 — Controller fitting consumes replayed development evidence

The controller freeze tool previously accepted hand-authored traces but did
not provide a Forge-owned path from an actual development run to those
traces. `collect_controller_traces.py` now joins each generation's recorded
controller action with its search-side incumbent checkpoint and observed
generation wall time. It emits only `split=dev` rows and rejects missing
actions, checkpoints, or non-finite telemetry; hidden-test assets are never
opened. This makes controller development executable without turning a
holdout result into training data.

## DL-062 — Transfer traces preserve per-problem provenance

Transferable controller fitting must combine development problems without
losing which run produced a row. The collector therefore accepts repeated
`--events`/`--problem-id` pairs, validates each ledger independently, and
stores the source run ID and event-ledger hash in every emitted trace. The
freeze tool may ignore these extra fields when fitting, but an auditor can
still recover the exact development evidence behind each row.

## DL-063 — Frozen development policies are replayed without refitting

Freezing manifests alone does not show that a policy can be loaded and used
by the real Forge loop. `run_controller_development.py` now executes every
registered action arm, fits the policies from the merged development traces,
then reloads each frozen manifest and replays it on every selected development
problem. Replay rows preserve the selected-action sequence, event/result
hashes, and decision/result-recomputation hashes, while remaining outside the
fit trace file. This proves the development control path without allowing
post-freeze behavior to update the policy or exposing holdout data.

## DL-064 — Controller trace state is retained

The search state that justified a controller action is part of transfer
provenance. The development trace collector now preserves the recorded
`controller_state` per generation and rejects a non-object state, while
retaining compatibility with explicitly legacy action-only ledgers. Missing
state is not silently invented; current V3 loop runs always record the full
state schema.

## DL-065 — Mock development randomness is seed-bound

The mock adapter previously constructed an unseeded `random.Random()` for
every caller, so repeated development runs with the same declared seed could
fit different candidate traces. Mock cheap/smart callers now receive the run
seed, while legacy one-argument caller shims remain supported. This is a
development reproducibility guarantee only; it does not turn mock output into
production-model evidence.

## DL-066 — Replay decisions exclude evaluator wall-clock telemetry

Evaluator wall time is retained in result/resource replay for audit, but it is
not a search decision. `replay_decision_records` now removes both generation
and evaluator resource payloads before hashing. Repeated local development
replays therefore compare action sequences and decision hashes without
mistaking measurement noise for a controller decision change.

## DL-067 — Development comparisons stay problem-local

The development runner now emits `development_comparison` cells grouped by
action (and by frozen mechanism) × problem, with scores listed separately by
seed. It does not pool score scales across problems or expose these aggregates
as normalized holdout metrics. This makes action-space tuning auditable while
preventing a mock diagnostic summary from becoming a scientific verdict.

## DL-068 — CLI model routing is explicit and hash-bound

The Python API already accepted a `controller_model_callers` mapping, but the
CLI had no explicit way to bind a frozen `generator_model` identity to an
adapter. `--controller-model-routes` now loads a strict manifest requiring
every policy identity, an adapter tier, adapter ID, and external model-manifest
hash. Non-mock V3 CLI runs fail closed without it; the route manifest ID/hash
are persisted and checked on resume. This records routing provenance without
claiming that the repository has frozen external model assets.

## DL-069 — Route hashes must bind to a validated model file

A route entry containing a 64-character hash is not sufficient evidence that
the referenced model contract exists. `forge/models.py` now strictly loads a
fully pinned model manifest, and `validate_routes_against_model_manifest`
requires every route hash to equal the actual file digest and every routed tier
to exist in that manifest. Non-mock V3 CLI runs require `--model-manifest`; the
hash is retained in the run manifest and checked during resume.

## DL-070 — Development traces are strict JSONL artifacts

The development matrix writer was appending a second newline to
`canonical_json()`, producing blank records between otherwise valid traces.
The collector also used permissive `json.dumps`, which could emit `Infinity`
when a finite ledger score difference overflowed. Both paths now write
canonical strict JSONL, reject non-finite `quality_gain`, and are covered by a
CLI round-trip and an overflow negative fixture. This changes neither the
controller fit objective nor any research metric; it makes development
evidence consumable by strict replay/freeze tooling.

## DL-071 — Metric boundaries reject non-numeric hidden inputs

Hidden candidate scores and percentile bounds are now validated through the
same finite-number contract used by normalized quality and bootstrap values.
Boolean scores, non-finite vector members, strings, and out-of-range or
non-numeric percentile bounds fail with `MetricError` before they can enter an
AUC or confidence interval. The registered formulas and thresholds remain
unchanged; this is a fail-closed input hardening change.

## DL-072 — Development quality gain is anchored to the visible seed

The controller trace collector previously assigned `quality_gain=0` to every
first-generation row because the initial incumbent was present only in the
archive, not in the event stream. V3 `attempt_started` metadata now records
the generation-start visible incumbent score, and the collector computes the
first generation's gain against that fixed seed while retaining a compatibility
fallback for older ledgers. A fresh local matrix now exposes non-zero action
gains and policy differences; this is development diagnostics only and does
not use hidden-test scores or change the registered holdout metric.
