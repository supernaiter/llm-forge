
A Machine-Auditable Termination Criterion for Forge as a Strong Algorithm-Discovery Paper
What “strong paper” means in the 2026 landscape
The most important conclusion from the literature review is that “Forge beats FunSearch” is no longer an adequate research target.

FunSearch established that LLM-generated programs plus evolutionary selection could make genuine algorithmic and mathematical discoveries. But EoH subsequently reported outperforming FunSearch on three combinatorial-optimization benchmarks and appeared at ICML 2024; ReEvo introduced reflective evolution across five algorithmic types and six combinatorial-optimization problems at NeurIPS 2024; MCTS-AHD replaced conventional population evolution with Monte Carlo tree search at ICML 2025; and PartEvo introduced niching and reported superiority to EoH and FunSearch at NeurIPS 2025.

The bar moved further in 2026. ShinkaEvolve, accepted at ICLR 2026, combines exploration/exploitation-aware parent selection, code-novelty rejection sampling, and bandit-based LLM ensemble selection; it reports a new circle-packing result after only 150 samples and has a public implementation. EoH-S, published at AAAI 2026, evolves complementary sets of heuristics and reports improvements across bin packing, TSP, and CVRP.

More importantly for Forge, several of the mechanisms we had previously considered novel have now been occupied. EvoX explicitly evolves the search strategy itself rather than merely evolving candidate programs, and reports results across nearly 200 optimization tasks; its SkyDiscover implementation provides a common framework for comparisons with OpenEvolve, ShinkaEvolve, and GEPA. Therefore, “Forge improves its own parent/mutation/search policy” by itself is no longer a sufficiently distinct thesis.

Likewise, adaptive LLM selection alone is weakened as a novelty claim by ShinkaEvolve's bandit-based ensemble selection; adaptive offspring count is explicitly investigated by TurboEvolve; persistent natural-language strategy populations are the core of SeaEvo; and SMCEvolve already gives program evolution a Sequential Monte Carlo interpretation with adaptive resampling, mutation mixtures, convergence control, and a finite-sample complexity analysis.

Even “the harness matters” is now an explicit research topic. Vesper's 2026 harness-engineering study examines token-budget allocation, evaluator exploitation, coding-agent execution, and parallel isolation. Its circle-packing experiments report that spending more reasoning budget on fewer candidates can outperform maximizing candidate count under a fixed token budget. That directly means the attractive Forge hypothesis of “small cheap model × huge number of mutations” must be demonstrated, not assumed.

There are two other developments that materially change the evaluation design. RAISE reports that existing automatic heuristic-design methods can degrade by as much as 19-fold under distribution shift on its studied problems, so an in-distribution benchmark alone is no longer convincing evidence of general algorithm discovery. Separately, EvoTrace/EvoReplay finds that apparent evolutionary gains may come from parameter tuning, recombination, evaluator overfitting, or recycling old edits rather than genuinely new algorithmic structure; it reports byte-identical reintroduction of previously deleted lines at substantial frequency in its trace corpus.

Finally, the frontier has moved beyond frozen-model evolutionary search. TTT-Discover performs reinforcement-learning updates to the model at test time and reports new results in mathematics, kernels, algorithms, and biology using an open model. This should be treated as an external frontier reference, not necessarily a primary Forge baseline, because test-time weight updates constitute a different computational intervention from frozen-model program search.

So the 2026 paper-worthy question should not be:

text
Copy
Does Forge beat FunSearch?
It should be:

text
Copy
Under strictly matched information and resource budgets,
does Forge produce better verified algorithms than
modern open program-discovery systems,

on problem families and distributions that were not used
to design Forge,

and can the advantage be causally attributed to a
pre-registered Forge mechanism rather than
a stronger LLM, more compute, evaluator exploitation,
or benchmark overfitting?
That is the standard I recommend encoding into Codex's termination predicate.

The scientific thesis Forge should preregister
The current literature makes several weak Forge theses unsuitable as the primary novelty claim:

text
Copy
"we use islands"                       -> insufficient
"we use multiple LLMs"                 -> insufficient
"we adaptively choose LLMs"            -> ShinkaEvolve
"we adapt offspring count"             -> TurboEvolve
"we retain natural-language strategy"  -> SeaEvo
"we evolve the search strategy"        -> EvoX
"we use principled resampling"          -> SMCEvolve
"we use coding agents as mutations"     -> Vesper / CORAL / AVO direction
ShinkaEvolve, EvoX, TurboEvolve, SeaEvo, SMCEvolve, CORAL, and Vesper collectively cover much of this design space already.

Recommended primary claim
The most defensible Forge-specific scientific thesis is instead:

A search controller learned from prior discovery tasks can transfer to unseen algorithm-discovery problem families and allocate model, operator, parent, and reasoning compute more efficiently than both fixed search policies and per-task adaptive evolutionary systems.

Call the registered mechanism, provisionally:

text
Copy
TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1
The conceptual distinction is important.

EvoX evolves a search strategy during the target optimization process. Forge's proposed controller is learned/tuned only on development problems, frozen before final holdout, and then has to transfer its decision rule to previously unseen problem families. EvoX therefore becomes a strong baseline rather than something Forge merely imitates.

The controller can observe search state such as:

text
Copy
remaining_budget
improvement_slope
time_since_last_improvement
archive_behavioral_entropy
archive_score_dispersion
candidate_invalid_rate
duplicate_rate
parent_lineage_depth
recent_operator_success
recent_model_success
estimated_generation_cost
and choose an action such as:

text
Copy
generator_model
parent_selection_policy
mutation_operator
number_of_offspring
reflection_depth
archive_sampling_policy
but its parameters cannot change after the final protocol freeze.

That creates a substantially cleaner scientific question:

text
Copy
Can search knowledge itself transfer between
algorithm-discovery problems?
A particularly valuable secondary claim would exploit the user's A100×8 setting:

text
Copy
Forge + small/local model + transferable controller
>
strong baseline + substantially larger model
under identical A100-GPU-second budgets.

That would support a stronger conclusion than “our search algorithm scores better”:

Search-system quality can substitute for model scale in automated algorithm discovery.

AlphaEvolve itself uses an ensemble in which a faster model supplies breadth and a stronger model supplies depth, so model-routing efficiency is scientifically relevant to current discovery systems.

However, this small-versus-large result should be a secondary high-impact claim, not allowed to substitute for the same-model comparison. Otherwise reviewers can legitimately argue that Forge's advantage came from model choice rather than search.

A second promising novelty direction, should transferable control prove too weak, is counterfactual replay-based credit assignment: automatically ablate/replay changes in high-performing descendants to determine which edits actually caused improvement, then feed only causally supported changes into future search. This directly addresses the mechanism ambiguity identified by EvoTrace/EvoReplay, which argues that best-score trajectories alone do not reveal whether genuine algorithmic structure was discovered.

The protocol below does not require that backup mechanism. It assumes the transferable compute-aware controller is the registered primary mechanism.

Frozen benchmark, model, and baseline protocol
The current Forge completion proposal should be changed in one important respect: research readiness and product-engineering readiness should not be the same predicate.

A certified sandbox, resumable execution, deterministic evaluator, immutable ledgers, and exact replay are necessary to make the experiment trustworthy. But details such as having nine CLI commands or hitting a particular CLI latency are product properties, not evidence that the research hypothesis is true. Making publication readiness depend on unrelated product milestones weakens the scientific specification.

Use:

text
Copy
RESEARCH_INTEGRITY_READY
STRONG_METHOD_PAPER_READY
CLEAN_REGISTERED_FALSIFICATION_READY

FORGE_RESEARCH_FINISHED =
    RESEARCH_INTEGRITY_READY
    AND (
        STRONG_METHOD_PAPER_READY
        OR CLEAN_REGISTERED_FALSIFICATION_READY
    )
and keep:

text
Copy
FORGE_PRODUCT_RELEASE_READY
as a separate engineering predicate.

Frozen problem sets
I recommend increasing the final breadth slightly beyond the previous eight-problem proposal.

text
Copy
P_DEV = {
  obp_dev_v1,
  tsp_dev_v1,
  jssp_dev_v1,
  capset_dev_v1
}

P_HOLDOUT = {
  h01, h02, h03, h04, h05,
  h06, h07, h08, h09, h10
}
The exact ten problems must be resolved into signed manifests before final evaluation.

text
Copy
HOLDOUT_STRUCTURE_VALID =

  heldout_problem_count == 10

  AND heldout_distinct_problem_families >= 8

  AND heldout_families_absent_from_P_DEV >= 5

  AND heldout_external_repository_packs >= 5

  AND min_search_instance_clusters_per_problem >= 50

  AND min_test_instance_clusters_per_problem >= 100

  AND min_hidden_test_instances_per_problem >= 500
Having transformed, synthetic, or private-instance tasks is valuable because LLM benchmark performance can otherwise reflect memorized problem structure. LLM-SRBench was explicitly designed around transformed and synthetic problems to distinguish scientific reasoning from trivial memorization, illustrating why hidden/generated holdouts are important for LLM-based discovery claims.

For every applicable holdout problem, evaluate more than one distribution:

text
Copy
D[p] subset_of {
  iid_heldout,
  size_shift,
  distribution_shift
}
with:

text
Copy
problems_with_size_shift >= 6
problems_with_distribution_shift >= 6
The search algorithm never receives hidden test scores from any of these distributions. RAISE's 2026 results make this more than cosmetic: robustness to distribution shift is now a concrete failure mode for LLM-designed heuristics.

The distinction should be:

text
Copy
search instances:
    visible to the method during candidate evaluation

hidden test instances:
    never visible to candidate generator
    never visible to search controller
    never used for parent selection
    never used for early stopping
    never returned during holdout
The problem specification obviously becomes visible when Forge performs its final search. What remains hidden is the final test set and its scores. This is cleaner than saying the entire problem source is permanently hidden.

Frozen models
Use three model tiers:

text
Copy
M = {
  SMALL,
  MEDIUM,
  STRONG
}
but these are aliases only during protocol drafting.

Before any final run:

text
Copy
MODEL_MANIFEST_VALID =

  exact_weight_revision_frozen == true
  AND tokenizer_revision_frozen == true
  AND chat_template_sha256_frozen == true
  AND quantization_profile_frozen == true
  AND inference_runtime_digest_frozen == true
  AND sampling_profile_frozen == true
No "latest", "default", model-family alias, floating Hugging Face revision, or API alias is acceptable.

Modern baseline registry
The baseline set should be substantially stronger than FunSearch alone.

The peer-reviewed mandatory core should include at least:

text
Copy
B_PEER = {
  FunSearch,
  EoH,
  ReEvo,
  MCTS_AHD,
  PartEvo,
  ShinkaEvolve,
  EoH_S
}
EoH, ReEvo, MCTS-AHD, PartEvo, ShinkaEvolve, and EoH-S all represent meaningful steps beyond the original FunSearch-era baseline and have appeared at ICML, NeurIPS, ICLR, or AAAI.

Add the strongest runnable open frontier systems available at protocol freeze:

text
Copy
B_OPEN = {
  OpenEvolve,
  CodeEvolve,
  EvoX,
  SMCEvolve
}
OpenEvolve has a public general evolutionary coding implementation; CodeEvolve provides an open evolutionary coding agent; EvoX is available through SkyDiscover, which also supplies a unified benchmarking interface across more than 200 optimization tasks; and SMCEvolve's paper links a public implementation.

CORAL can be added to the agentic-search track if its task interface can be matched without materially changing its mechanism; its public system uses persistent shared state, isolated worktrees, a grader daemon, multi-agent collaboration, and supports Codex as a runtime.

RAISE should be a mandatory robustness comparator on the compatible combinatorial subset, rather than being forced onto unrelated mathematical or systems tasks.

TurboEvolve, SeaEvo, Vesper, and other fresh preprints should enter the mandatory set only if there is a runnable, licensable implementation before the fixed baseline cutoff. Their existence should nevertheless influence Forge's novelty claim because they already establish adaptive batch sizing, persistent strategy spaces, and harness engineering as active contributions.

AlphaEvolve should be an external reference, not a mandatory matched-compute baseline, unless a faithful executable implementation becomes available. The official public material describes the system and its results, whereas public systems such as OpenEvolve explicitly position themselves as implementations of the AlphaEvolve-style paradigm.

The same issue exists even for FunSearch: DeepMind's official repository explicitly says that it includes the evolutionary pipeline but not the language models, untrusted-code sandbox, or distributed infrastructure used in the original system. A “FunSearch baseline” therefore needs a frozen adapter and must not be represented as bit-for-bit reproduction of the original infrastructure.

The executable baseline rule should therefore be machine-defined:

text
Copy
BASELINE_ELIGIBLE(b) =

  public_before_baseline_cutoff(b)

  AND source_commit_resolved(b)

  AND license_allows_evaluation(b)

  AND native_smoke_tests_pass(b)

  AND forge_adapter_conformance_pass(b)

  AND no_material_algorithm_change_required(b)
Then:

text
Copy
B_PRIMARY =
  B_PEER
  union
  all eligible methods from B_OPEN
and freeze:

text
Copy
baseline_cutoff_utc = 2026-08-01T00:00:00Z

baseline_registry_sha256 = <frozen>

post_unblinding_baseline_additions == 0
post_unblinding_baseline_deletions == 0
Two comparison tracks
A single comparison protocol cannot answer both “is Forge's search algorithm better?” and “is Forge's whole system more compute-efficient?”

Use two tracks.

text
Copy
TRACK = {
  SAME_MODEL,
  NATIVE_COMPUTE
}
In SAME_MODEL, every system uses exactly the same frozen model.

text
Copy
same model
same starting program
same task information
same search instances
same candidate-evaluation cap
same candidate-attempt cap
But do not force identical prompts, temperature, parent-selection settings, or reflection strategies. Those are parts of the algorithm. Forcing ShinkaEvolve, EoH, EvoX, etc. to use Forge's prompting protocol would cripple their intended mechanisms.

Instead:

text
Copy
method_specific_configuration_allowed == true

AND configuration_source in {
  official_default,
  official_paper_configuration,
  development_selected_configuration
}

AND configuration_frozen_before_holdout == true
In NATIVE_COMPUTE, all systems receive the same frozen model pool and the same physical compute budget. They may choose models however their own method permits.

text
Copy
same_model_pool == true

same_A100_GPU_second_budget == true

same_evaluator_budget == true

same_task_information == true
This gives Forge's cheap-model routing idea a fair test against ShinkaEvolve-style ensemble selection and other adaptive systems instead of artificially disabling them.

Metrics, budgets, and statistical decision rules
Generation budget
For the same-model track:

text
Copy
K = 512
Each of these consumes one attempt:

text
Copy
valid candidate
invalid syntax
empty model response
duplicate candidate
AST rejection
constraint violation
runtime error
timeout
evaluation hack
candidate rejected by sandbox
That is essential. Otherwise a method can look artificially sample-efficient simply because failed generations disappear from the x-axis.

Use hard caps:

text
Copy
MAX_ATTEMPTS = 512

MAX_INPUT_TOKENS = 4_194_304

MAX_OUTPUT_TOKENS = 524_288

MAX_SEARCH_EVALUATIONS = 512
When any hard cap is exhausted:

text
Copy
no_more_generation == true

remaining_anytime_checkpoints =
    carry_forward(last_valid_incumbent)
The native-compute track should instead be denominated in actual hardware use:

text
Copy
NATIVE_A100_GPU_SECOND_CAP = 3600
MAX_NATIVE_ATTEMPTS = 2048
where:

text
Copy
A100_GPU_SECONDS =

  sum_over_model_calls(
    allocated_A100_count
    * model_forward_wall_seconds
  )
This lets a method spend one GPU for an hour, eight GPUs for 7.5 minutes, or another equivalent allocation, while consuming the same generation compute.

Evaluator compute should be separately recorded and held equal because otherwise a method could shift its cost from generation into an expensive evaluator.

The earlier full-factorial proposal is already a large experiment. A grid of ten holdout problems, three model profiles, twelve seeds, ten total systems, and 512 attempts corresponds to 1,843,200 candidate attempts; at an illustrative 400 output tokens per attempt, that is about 737 million generated output tokens before extension seeds. This is why an explicit budget ledger matters.

Hidden-test anytime score
Do not make final best score the primary endpoint.

For every attempt (k):

text
Copy
selected[x,p,m,s,k] =

  candidate that method x would have selected
  as incumbent after exactly k attempts

  using search-side information only
After the complete blinded run finishes, the verifier evaluates each distinct incumbent on hidden test instances.

For a problem (p), distribution (d), and score (y), normalize against two anchors fixed before holdout:

text
Copy
NQ[p,d,y] =

  (y - seed_reference[p,d])

  /
  (fixed_reference[p,d] - seed_reference[p,d])
with:

text
Copy
clipping == false
reference_derived_from_final_Forge_results == false
Therefore:

text
Copy
NQ = 0
means performance of the fixed starting seed, while:

text
Copy
NQ = 1
means the pre-registered independent reference.

Then:

text
Copy
AUC_ATTEMPT[x,p,m,s,d] =

  mean_{k=1..512}(
    NQ[
      p,
      d,
      hidden_test_score(selected[x,p,m,s,k])
    ]
  )
and:

text
Copy
FINAL[x,p,m,s,d] =

  NQ[
    p,
    d,
    hidden_test_score(selected[x,p,m,s,512])
  ]
For native compute, define the same best-so-far curve at fixed GPU-budget fractions:

text
Copy
G = {
  0.05, 0.10, 0.15, 0.20, 0.25,
  0.30, 0.40, 0.50, 0.60, 0.70,
  0.80, 0.90, 1.00
}
text
Copy
AUC_GPU[x,p,s,d] =

  mean_{g in G}(
    hidden_test_normalized_incumbent_quality
    at g * NATIVE_A100_GPU_SECOND_CAP
  )
Input and output token curves should remain secondary metrics. Vesper's token-budget study specifically demonstrates why budget allocation can materially change discovery quality, so total compute should not be reduced to “number of successful candidates.”

Two notions of “best baseline”
There is a subtle but important improvement over the previous predicate.

Define a single baseline champion selected only on development problems:

text
Copy
B_CHAMPION =

  argmax_{b in B_PRIMARY}
    DEV_MEAN_AUC_ATTEMPT[b]
Freeze that identity before holdout.

Then define:

text
Copy
delta_CHAMPION[p,m,s,d] =

  AUC_ATTEMPT[FORGE,p,m,s,d]
  - AUC_ATTEMPT[B_CHAMPION,p,m,s,d]
Also define the harder cellwise oracle composite:

text
Copy
delta_ORACLE[p,m,s,d] =

  AUC_ATTEMPT[FORGE,p,m,s,d]

  - max(
      AUC_ATTEMPT[b,p,m,s,d]
      for b in B_PRIMARY
    )
The first asks:

Does Forge beat the strongest single competing system chosen before the final test?

The second asks something more conservative:

Does Forge beat an imaginary oracle that is allowed to retrospectively pick whichever baseline happened to be strongest in each individual cell?

Requiring Forge to beat both is much more convincing than either alone.

Distribution robustness
Define:

text
Copy
delta_OOD[p,m,s,d] =
  delta_ORACLE[p,m,s,d]

for d in {
  size_shift,
  distribution_shift
}
and:

text
Copy
OOD_DROP[x,p,m,s] =

  AUC_ATTEMPT[x,p,m,s,iid_heldout]
  - mean_{d in OOD[p]}(
      AUC_ATTEMPT[x,p,m,s,d]
    )
Forge does not need zero OOD degradation. It needs to retain its advantage over competing systems when the evaluator distribution changes.

Mechanism ablations
For the proposed transferable compute-aware controller:

text
Copy
A = {
  FIXED_DEV_BEST,
  NO_TRANSFER_PRIOR,
  COST_UNAWARE_CONTROLLER
}
where:

FIXED_DEV_BEST uses the best fixed model/operator/search configuration selected on development tasks.

NO_TRANSFER_PRIOR has the same architecture and runtime state but removes cross-task learned controller information.

COST_UNAWARE_CONTROLLER can observe quality but receives no GPU/token cost information.

Define:

text
Copy
delta_FIXED =
  AUC_ATTEMPT[FULL] - AUC_ATTEMPT[FIXED_DEV_BEST]

delta_TRANSFER =
  AUC_ATTEMPT[FULL] - AUC_ATTEMPT[NO_TRANSFER_PRIOR]

delta_COST =
  AUC_GPU[FULL] - AUC_GPU[COST_UNAWARE_CONTROLLER]
This is much stronger than an arbitrary “remove component X” ablation because each comparison maps directly onto one part of the scientific thesis.

Structural-discovery diagnostic
Because modern evidence shows that evolutionary coding scores can rise through tuning, recycling, and evaluator overfit rather than new structure, Forge should record a diagnostic edit taxonomy and deterministic line/AST ancestry.

This need not be a primary success criterion, because classifying “genuine novelty” perfectly is difficult. But require:

text
Copy
trace_parent_child_links_complete == true

AND candidate_AST_hash_coverage == 1.0

AND accepted_candidate_diff_coverage == 1.0

AND deterministic_cycle_detection_coverage == 1.0

AND evaluator_hack_audit_coverage == 1.0
and report:

text
Copy
structural_edit_fraction
parameter_only_edit_fraction
exact_reintroduced_edit_fraction
evaluation_hack_attempt_rate
Vesper independently identifies evaluator exploitation as a serious harness concern and incorporates explicit hack detection, reinforcing the need for this audit trail.

Confidence intervals
Use a fixed paired hierarchical bootstrap:

text
Copy
BOOTSTRAP_REPLICATES = 20_000
BOOTSTRAP_SEED = 2026080901
CI = two_sided_percentile_95
Resample hierarchically:

text
Copy
problem_family
  -> problem
     -> seed
        -> hidden_test_instance_cluster
while preserving pairings between Forge and every baseline.

Crucially, for delta_ORACLE, the best baseline is recalculated inside each bootstrap replicate:

text
Copy
bootstrap_delta_ORACLE[r] =

  Forge_statistic[r]

  - max(
      baseline_statistic[b,r]
      for b in B_PRIMARY
    )
Do not select the empirical best baseline once and then pretend that its identity has no uncertainty.

For worst-model, worst-family, and worst-distribution claims, bootstrap the minimum statistic itself:

text
Copy
T_model[r] =
  min_m mean_{p,s,d}(delta_ORACLE[r,m,p,s,d])

T_family[r] =
  min_family mean_{cells in family}(delta_ORACLE[r])

T_shift[r] =
  min_d mean_{p,m,s}(delta_ORACLE[r,p,m,s,d])
This is preferable to testing many individual effects and then selectively quoting the successful ones.

Deterministic strong-positive predicate
The numeric thresholds below are proposed preregistration thresholds, not canonical numbers established by the literature. Their purpose is to define a nontrivial practical effect before seeing holdout data.

0.030 means three percentage points of the fixed seed-to-reference performance headroom, not three raw objective units.

First define the protocol gate.

text
Copy
RESEARCH_INTEGRITY_READY =

  exact_source_commit_frozen == true

  AND protocol_manifest_sha256_frozen == true

  AND baseline_registry_sha256_frozen == true

  AND model_manifests_sha256_frozen == true

  AND task_manifests_sha256_frozen == true

  AND evaluator_manifests_sha256_frozen == true

  AND container_image_digests_frozen == true

  AND prompt_and_decoding_profiles_frozen == true

  AND P_DEV_overlap_with_P_HOLDOUT == 0

  AND heldout_problem_family_requirements_pass == true

  AND search_test_instance_overlap == 0

  AND hidden_test_hash_access_by_search == 0

  AND hidden_test_score_feedback_events == 0

  AND candidate_generator_hidden_test_access == 0

  AND post_unblinding_core_changes == 0

  AND post_unblinding_prompt_changes == 0

  AND post_unblinding_model_changes == 0

  AND post_unblinding_baseline_changes == 0

  AND post_unblinding_metric_changes == 0

  AND post_unblinding_threshold_changes == 0

  AND post_unblinding_ablation_changes == 0

  AND invalid_or_missing_primary_runs == 0

  AND budget_violation_count == 0

  AND evaluator_nondeterminism_events == 0

  AND evaluator_hack_false_accept_count == 0

  AND test_data_mutation_count == 0

  AND cross_run_state_leakage_count == 0
Then same-model search superiority:

text
Copy
SAME_MODEL_SUPERIORITY_READY =

  min_m mean_{p,s,d}(
    delta_ORACLE[p,m,s,d]
  ) >= 0.025

  AND min_family mean_{cells in family}(
    delta_ORACLE
  ) >= 0.020

  AND min_d mean_{p,m,s}(
    delta_ORACLE[p,m,s,d]
  ) >= 0.020

  AND overall_delta_ORACLE_mean
      >= 0.030

  AND overall_delta_ORACLE_95CI_low
      >= 0.015

  AND overall_delta_CHAMPION_mean
      >= 0.050

  AND overall_delta_CHAMPION_95CI_low
      >= 0.030

  AND min_model_delta_ORACLE_95CI_low
      >= 0.005

  AND min_family_delta_ORACLE_95CI_low
      >= 0.000

  AND severe_regression_cell_rate
      <= 0.050

  AND min_cell_delta_ORACLE
      >= -0.100

  AND heldout_problem_win_rate_vs_ORACLE
      >= 0.750

  AND cell_win_rate_vs_ORACLE
      >= 0.650
The use of a small allowed worst-cell regression is deliberate. Requiring every stochastic run on every model/problem/seed/distribution combination to improve is statistically brittle and turns one unlucky generation sequence into a total veto. The stronger and more meaningful constraints are low severe-regression frequency, positive worst-family behavior, broad problem-level wins, and a positive paired confidence interval.

Final-quality evidence:

text
Copy
FINAL_QUALITY_READY =

  overall_delta_ORACLE_FINAL_mean
      >= 0.015

  AND overall_delta_ORACLE_FINAL_95CI_low
      >= 0.005

  AND min_m mean_{p,s,d}(
        delta_ORACLE_FINAL[p,m,s,d]
      ) >= 0.010

  AND final_problem_win_rate_vs_ORACLE
      >= 0.700
OOD evidence:

text
Copy
OOD_GENERALIZATION_READY =

  problems_with_distribution_shift >= 6

  AND problems_with_size_shift >= 6

  AND overall_delta_OOD_mean
      >= 0.020

  AND overall_delta_OOD_95CI_low
      >= 0.010

  AND worst_OOD_family_mean_delta
      >= 0.000

  AND OOD_problem_win_rate_vs_ORACLE
      >= 0.650

  AND OOD_severe_regression_rate
      <= 0.100
Native-compute evidence:

text
Copy
COMPUTE_EFFICIENCY_READY =

  same_model_pool_for_all_methods == true

  AND same_A100_GPU_second_cap == true

  AND same_evaluator_budget == true

  AND overall_delta_GPU_ORACLE_mean
      >= 0.030

  AND overall_delta_GPU_ORACLE_95CI_low
      >= 0.015

  AND min_family mean(
        delta_GPU_ORACLE
      ) >= 0.015

  AND native_compute_problem_win_rate_vs_ORACLE
      >= 0.700

  AND native_compute_severe_regression_rate
      <= 0.100
Mechanism evidence:

text
Copy
PRIMARY_MECHANISM_VALIDATED =

  mean(delta_FIXED) >= 0.015

  AND delta_FIXED_95CI_low > 0

  AND mean(delta_TRANSFER) >= 0.010

  AND delta_TRANSFER_95CI_low > 0

  AND mean(delta_COST) >= 0.010

  AND delta_COST_95CI_low > 0

  AND min_unseen_family mean(delta_FIXED)
      >= 0.000

  AND controller_parameter_updates_on_holdout == 0

  AND controller_training_holdout_access_count == 0
The controller has therefore demonstrated three things independently:

text
Copy
FULL > best fixed search configuration
FULL > same controller without cross-task transfer
FULL > controller that ignores compute cost
rather than merely winning as an opaque bundle.

Add a replication requirement:

text
Copy
REPLICATION_READY =

  primary_seed_count == 12

  AND independent_model_profiles >= 3

  AND strongest_model_effect_sign > 0

  AND medium_model_effect_sign > 0

  AND small_model_effect_sign > 0

  AND independent_replay_runs >= 100

  AND replay_decision_hash_mismatches == 0

  AND result_recomputation_hash_mismatches == 0
Here “replay” should mean replaying recorded model responses and evaluator outputs, not demanding bitwise-identical fresh GPU sampling. Modern GPU LLM generation need not be treated as a deterministic primitive; what must be deterministic is that a frozen event stream yields exactly the same search decisions and paper verdict.

Then:

text
Copy
STRONG_METHOD_PAPER_READY =

  RESEARCH_INTEGRITY_READY

  AND SAME_MODEL_SUPERIORITY_READY

  AND FINAL_QUALITY_READY

  AND OOD_GENERALIZATION_READY

  AND COMPUTE_EFFICIENCY_READY

  AND PRIMARY_MECHANISM_VALIDATED

  AND REPLICATION_READY

  AND baseline_adapter_conformance_failures == 0

  AND benchmark_identifier_branches_in_Forge == 0

  AND holdout_specific_prompt_count == 0

  AND hidden_answer_literal_count == 0

  AND invalid_or_missing_primary_runs == 0
That is the main predicate I would use.

High-impact secondary claim
Add a separate, stricter claim for the A100×8 / cheap-model hypothesis:

text
Copy
SMALL_BEATS_LARGE_READY =

  mean_{p,s,d}(
    AUC_GPU[
      FORGE,
      SMALL,
      p,s,d
    ]

    -

    max(
      AUC_GPU[
        b,
        STRONG,
        p,s,d
      ]
      for b in B_PRIMARY
    )
  ) >= 0.020

  AND corresponding_95CI_low >= 0.000

  AND problem_win_rate >= 0.600

  AND same_A100_GPU_second_budget == true

  AND same_task_information == true

  AND evaluator_budget_equal == true
This is not required for STRONG_METHOD_PAPER_READY.

But if true, it materially upgrades the story:

text
Copy
strong search + small model
>
weaker search + strong model
and directly tests whether search architecture can substitute for model scale.

Discovery-result bonus
A genuinely new best-known algorithm or mathematical construction remains unusually valuable. AlphaEvolve's significance comes partly from verified improvements in matrix multiplication, mathematical lower bounds, and production systems, rather than merely from aggregate benchmark wins.

Therefore separately record:

text
Copy
BREAKTHROUGH_DISCOVERY_READY =

  independently_verified_new_best_known_results >= 2

  AND distinct_problem_families_with_new_BKS >= 2

  AND prefreeze_BKS_manifest_valid == true

  AND no_manual_candidate_edit_after_generation == true

  AND independent_verifier_reproduction_rate == 1.0
I would treat this as a major paper-strength bonus, not as a replacement for broad baseline evaluation.

Clean negative, inconclusive outcomes, and anti-gaming
A major virtue of the user's DSPO/rescue examples is that Codex cannot optimize forever until the desired conclusion appears. Forge should preserve that principle.

The first research question is:

text
Copy
Q1:
Does Forge exceed the modern cellwise baseline oracle
by a practically meaningful normalized anytime margin?
Set the practical target:

text
Copy
Q1_MPE = 0.030
Then:

text
Copy
Q1_STATUS =

  strong_positive
    if SAME_MODEL_SUPERIORITY_READY

  clean_negative
    if overall_delta_ORACLE_95CI_high < 0.030

  inconclusive
    otherwise
The compute-efficiency hypothesis is:

text
Copy
Q2_STATUS =

  strong_positive
    if COMPUTE_EFFICIENCY_READY

  clean_negative
    if overall_delta_GPU_ORACLE_95CI_high < 0.030

  inconclusive
    otherwise
The mechanism hypothesis is:

text
Copy
Q3_STATUS =

  strong_positive
    if PRIMARY_MECHANISM_VALIDATED

  clean_negative
    if (
      delta_FIXED_95CI_high < 0.015
      OR
      delta_TRANSFER_95CI_high < 0.010
      OR
      delta_COST_95CI_high < 0.010
    )

  inconclusive
    otherwise
The robustness hypothesis is:

text
Copy
Q4_STATUS =

  strong_positive
    if OOD_GENERALIZATION_READY

  clean_negative
    if overall_delta_OOD_95CI_high < 0.020

  inconclusive
    otherwise
Do not call a nonsignificant result negative:

text
Copy
CI_low <= threshold
AND
CI_high >= threshold
means:

text
Copy
inconclusive
not failure and not success.

Pre-registered extension rule
Use:

text
Copy
S_PRIMARY = {
  101,102,103,104,105,106,
  107,108,109,110,111,112
}

S_EXTENSION = {
  113,114,115,116,117,118,
  119,120,121,122,123,124
}
After the twelve primary seeds, an external verifier may return only:

text
Copy
positive
negative
extend
If any required primary effect remains inconclusive:

text
Copy
run S_EXTENSION
No detailed hidden scores, baseline identities, per-problem weaknesses, or CI values are disclosed to Codex before extension finishes.

After twenty-four seeds:

text
Copy
no additional seed extension permitted
This prevents sequentially adding seeds until a desired significance threshold is crossed.

Clean registered falsification
text
Copy
CLEAN_REGISTERED_FALSIFICATION_READY =

  RESEARCH_INTEGRITY_READY

  AND primary_and_required_extension_complete == true

  AND (
       Q1_STATUS == clean_negative

       OR Q2_STATUS == clean_negative

       OR Q3_STATUS == clean_negative

       OR Q4_STATUS == clean_negative
  )

  AND no_required_Q_status_is_inconclusive

  AND post_unblinding_changes == 0

  AND invalid_or_missing_primary_runs == 0
This says:

The preregistered strong Forge thesis has been cleanly falsified.

It does not mean every component of Forge is useless. For example:

text
Copy
Q1 positive
Q2 positive
Q3 clean_negative
would mean Forge empirically wins, but the proposed transferable-controller explanation is not supported.

That is still a scientifically useful outcome, but it cannot be marketed as evidence for the preregistered mechanism without beginning a new study version.

Thus:

text
Copy
REGISTERED_THESIS_STATUS =

  strong_positive
    if STRONG_METHOD_PAPER_READY

  clean_falsification
    if CLEAN_REGISTERED_FALSIFICATION_READY

  inconclusive
    otherwise
and:

text
Copy
FORGE_RESEARCH_FINISHED =

  REGISTERED_THESIS_STATUS
  in {
    strong_positive,
    clean_falsification
  }
Anti-p-hacking rule
After final holdout starts:

text
Copy
any change to:
  Forge core search logic
  controller
  prompts
  model manifests
  model routing rules
  baseline set
  baseline adapter
  metric
  normalization anchors
  bootstrap procedure
  success threshold
  ablation
  holdout task
  holdout distribution
  seed set

=> terminates study version
The new code may be tested, but under:

text
Copy
STUDY_VERSION = V_next
with a completely new sealed holdout protocol.

Evaluation-hack rule
Vesper explicitly identifies evaluator exploitation as a real problem in coding-agent algorithm discovery, and EvoTrace distinguishes genuine algorithmic changes from evaluator overfitting.

Therefore:

text
Copy
EVALUATION_INTEGRITY_READY =

  evaluator_source_unmodifiable_by_candidate == true

  AND hidden_test_unreadable_by_candidate == true

  AND candidate_network_access == false

  AND candidate_parent_process_access == false

  AND candidate_environment_secret_access == false

  AND candidate_write_scope == scratch_only

  AND score_file_access == false

  AND evaluator_process_introspection == false

  AND accepted_evaluation_hack_count == 0

  AND hidden_test_side_channel_count == 0
Any rejected hack still counts as a generation attempt.

Baseline failure rule
A broken baseline cannot count as a Forge victory.

text
Copy
if baseline_native_conformance_pass == false:
    FINAL_VERDICT = BLOCKED
not:

text
Copy
baseline_score = 0
This is especially important for FunSearch because its official public repository intentionally omits several infrastructure components, and for any new paper whose implementation requires an adapter.

The final Codex termination contract
The resulting contract should look like this.

text
Copy
FORGE_RESEARCH_V3 = {

  PRIMARY_THESIS:
    transferable_compute_aware_search_controller_v1,

  P_DEV:
    4 frozen development problems,

  P_HOLDOUT:
    10 frozen problems
    from >= 8 families,

  REQUIRED_UNSEEN_FAMILIES:
    >= 5,

  TEST_DISTRIBUTIONS:
    iid
    + size shift
    + distribution shift where applicable,

  MODELS:
    SMALL
    MEDIUM
    STRONG,

  PRIMARY_SEEDS:
    12,

  EXTENSION_SEEDS:
    12,

  SAME_MODEL_ATTEMPT_BUDGET:
    512,

  NATIVE_COMPUTE_BUDGET:
    3600 A100-GPU-seconds,

  BASELINES:
    frozen modern peer-reviewed
    + eligible open frontier registry,

  PRIMARY_METRIC:
    hidden-test normalized
    anytime AUC by generation attempt,

  COMPUTE_METRIC:
    hidden-test normalized
    anytime AUC by A100-GPU-seconds,

  FINAL_METRIC:
    normalized hidden-test final incumbent,

  ROBUSTNESS:
    unseen instance distributions,

  STATISTICS:
    paired hierarchical bootstrap,
    20,000 replicates,
    fixed seed,

  ABLATIONS:
    fixed_dev_best,
    no_transfer_prior,
    cost_unaware_controller
}
The final positive condition is:

text
Copy
STRONG_PAPER_READY =

  RESEARCH_INTEGRITY_READY

  AND SAME_MODEL_SUPERIORITY_READY

  AND FINAL_QUALITY_READY

  AND OOD_GENERALIZATION_READY

  AND COMPUTE_EFFICIENCY_READY

  AND PRIMARY_MECHANISM_VALIDATED

  AND REPLICATION_READY
and the research stopping condition is:

text
Copy
FORGE_RESEARCH_FINISHED =

  STRONG_PAPER_READY

  OR

  CLEAN_REGISTERED_FALSIFICATION_READY
with:

text
Copy
anything_else == NOT_FINISHED
The corresponding Codex authority should be physically external to the repository:

text
Copy
/workspace/forge
    editable by Codex

/opt/forge-protocol-v3
    read-only frozen protocol

/opt/forge-baselines-v3
    read-only baseline commits/adapters

/opt/forge-holdout-v3
    sealed holdout tasks/test distributions

/opt/forge-verifier-v3
    read-only verdict implementation
and Codex should see only:

text
Copy
forge-verifier public
forge-verifier freeze
forge-verifier primary
forge-verifier extension
forge-verifier final
Before unblinding, primary should return only:

json
Copy
{"status": "positive"}
or:

json
Copy
{"status": "negative"}
or:

json
Copy
{"status": "extend"}
not diagnostic information that enables adaptation to the holdout.

The final machine-readable verdict should have exactly these terminal states:

text
Copy
STRONG_POSITIVE
CLEAN_FALSIFICATION
INCONCLUSIVE
BLOCKED_INTEGRITY_FAILURE
and only the first two terminate the registered research experiment.

The strongest improvement over the previous Forge V2 condition is therefore not merely raising the numerical threshold. It is changing what must be demonstrated:

text
Copy
V2-style claim:
Forge beats a collection of search baselines.

V3 strong-paper claim:
Forge's pre-registered search mechanism
transfers to unseen problem families,
beats a modern cellwise-oracle baseline,
survives distribution shift,
wins under both sample and physical-compute budgets,
works across model scales,
and its advantage disappears in exactly the
ablations predicted by the hypothesis.
That distinction matters because the 2026 frontier now contains adaptive search-strategy evolution in EvoX, sample-efficient multi-model evolution in ShinkaEvolve, principled SMC evolution in SMCEvolve, strategy-space evolution in SeaEvo, adaptive offspring scheduling in TurboEvolve, robustness-aware evolution in RAISE, agentic harness research in Vesper/CORAL, and test-time model training in TTT-Discover.

No deterministic predicate can guarantee acceptance at ICML, NeurIPS, or ICLR, because novelty, clarity, reviewer judgment, and significance are not reducible to a fixed numerical threshold. But if this predicate is frozen before the final holdout and Forge actually passes it, the empirical claim would be substantially stronger than “we beat FunSearch”: it would establish broad, matched-budget, model-controlled, OOD-tested, mechanism-validated superiority against the modern open algorithm-discovery frontier. Given how quickly that frontier has advanced from FunSearch through EoH, ReEvo, PartEvo, ShinkaEvolve, EvoX and the 2026 systems above, that is the level of evidence I would now require before calling Forge a genuinely strong algorithm-discovery research result.
