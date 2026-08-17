"""変異オペレータ。LLMに残された唯一の生成的役割。
多様性は祈るものではなく注入するもの: 温度ジッタ × プロンプト摂動 × 親の多様サンプリング
× SSoT(cfg["ssot"]、Sakana AI arXiv:2510.21150、build_prompt側の既定はfalseだが
forge-init雛形・stringmax/binpackサンプルでは既定on)。"""
from __future__ import annotations
import random
from collections.abc import Mapping

PERTURBATIONS = [
    "Try a structure completely different from prior examples.",
    "Keep only a fragment of the best solution and rebuild the rest from scratch.",
    "Combine elements from the two parents.",
    "Make the most simplified version possible.",
    "Introduce one bold change that pushes right up against the constraint.",
    "Make only a local micro-edit. Change exactly one spot.",
]

# SSoT (Sakana AI, arXiv:2510.21150) 準拠の多様性プロトコル。固定テンプレート
# (呼び出しごとに書き換えない) — panel.shの同型実装と同じ理由: 文字列生成だけで
# 加工・写像をさせないと選択と文字列が紐付かず「独立した視点を選べ」が口約束で終わる。
# seed・6方向性の列挙・計算過程はコードブロックの外に書かせる — extract_block()は
# 応答中「最後の```ブロック」だけを候補として拾うため、この指示に従えばdedup(SimHash)
# へ渡る前にseedが自然に脱落する(候補parse側の変更は不要)。
SSOT_SNIPPET = (
    "\n\n[Diversity protocol (SSoT, arXiv:2510.21150)]\n"
    "Before writing the candidate, do the following in order, and write this "
    "reasoning outside the code block:\n"
    "1. Generate one arbitrary alphanumeric string (about 16 characters), "
    "independent of any other context.\n"
    "2. Enumerate 6 independent approach directions for this problem "
    "(do not include simple rephrasings of the parent candidate; conventional "
    "approaches are fine to include).\n"
    "3. Sum the character codes of the string from step 1 and take that sum mod 6 "
    "(a remainder from 0 to 5).\n"
    "4. Mechanically fix the direction whose number matches that remainder as the "
    "main axis for this candidate, and build the candidate faithfully from that direction.\n"
    "5. Write the random string, the enumeration of 6 directions, and the calculation "
    "outside the final ```code block``` — the code block must contain only the "
    "finished candidate.\n"
)


_MUTATION_OPERATOR_INSTRUCTIONS = {
    "local": "Make one local micro-edit to a single existing decision point.",
    "micro": "Make one local micro-edit to a single existing decision point.",
    "structural": "Change the algorithmic structure while preserving the required interface.",
    "global": "Rebuild the algorithm globally around a materially different strategy.",
    "recombine": "Recombine complementary ideas from the supplied parents.",
    "simplify": "Simplify the candidate aggressively while retaining its strongest idea.",
}

_INCUMBENT_QUALITY_INSTRUCTION = (
    "\nScores are maximized: a numerically larger (less negative, when applicable) score is "
    "better. The highest-scoring parent is the incumbent. Preserve its required interface and "
    "validity, inspect its strongest idea before changing it, and make one defensible "
    "AST-level mutation that is plausibly an improvement. Prefer a simple robust change "
    "over a fragile redesign; do not emit a known-worse fallback or alter the evaluator.\n"
)


def build_prompt(problem, parents: list[dict], guidance: str, rng: random.Random,
                 ssot: bool = False, *, mutation_operator: str | None = None,
                 reflection_depth: int = 0,
                 controller_mechanism: str | None = None,
                 controller_state: Mapping[str, object] | None = None) -> str:
    shown = "\n\n".join(
        f"# Parent (score={p['score']:.4f})\n```\n{p['text']}\n```" for p in parents
    )
    g = f"\nGuidance from the judge: {guidance}\n" if guidance else ""
    if mutation_operator is None:
        instruction = rng.choice(PERTURBATIONS)
    else:
        operator = str(mutation_operator).strip()
        instruction = _MUTATION_OPERATOR_INSTRUCTIONS.get(
            operator.lower(),
            f"Use the registered mutation operator `{operator}` explicitly.",
        )
    operator_hint = (
        f"\nRegistered mutation operator: {str(mutation_operator).strip()}\n"
        if mutation_operator is not None else ""
    )
    reflection_hint = (
        f"\nUse search-side reflection depth {reflection_depth} before emitting the candidate: "
        "mentally simulate one representative decision and compare the mutated rule with "
        "the incumbent. Check downstream continuation (reusable residual capacity for "
        "allocation problems, onward/return cost for route choices, and the analogous "
        "next-state effect for other sequential problems). For route-choice interfaces, "
        "a bounded top-ranked frontier with neighborhood isolation and return-to-start "
        "cost is a useful robust pattern; use exhaustive bounded lookahead only when the "
        "remaining candidate set is small. Then emit one robust structural mutation that "
        "preserves all interface and validity invariants.\n"
        if isinstance(reflection_depth, int) and not isinstance(reflection_depth, bool)
        and reflection_depth > 0 else ""
    )
    controller_hint = ""
    if controller_mechanism == "TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V2":
        operator_name = str(mutation_operator or "").strip().lower()
        if operator_name in {"local", "micro"}:
            controller_hint = (
                "\nTransferable controller context: keep the highest-scoring incumbent as the "
                "anchor and make one substantive local refinement. Do not redesign the "
                "algorithm or add a new search layer; change one existing decision point, "
                "and when that point exposes a numeric trade-off, move it far enough to "
                "test a meaningful neighboring regime rather than making a cosmetic tweak; "
                "for a scalar coefficient, a roughly factor-of-two change is a useful single "
                "counterfactual. "
                "Preserve the interface and validity invariants, and avoid returning a "
                "duplicate of every supplied parent.\n"
            )
        elif operator_name == "structural":
            # Keep the structural generation contract identical to the fixed
            # structural arm.  The controller's transfer signal is already
            # expressed by action selection, parent routing, and compute
            # allocation; an extra prose hint changes the real model's
            # structural distribution without adding observable search-state
            # information.
            controller_hint = ""
        else:
            controller_hint = (
                "\nTransferable controller context: use the highest-scoring incumbent as the "
                "anchor, follow the registered mutation operator, and make one coherent "
                "algorithmic alternative. Combine at most one complementary idea from the "
                "other parent(s); preserve the interface and validity invariants, and keep "
                "a robust incumbent fallback when the alternative is uncertain.\n"
            )
    # The controller consumes the live state when selecting an action.  The
    # raw counters are intentionally not repeated in the generation prompt:
    # exposing a tiny, noisy state vector caused the real model to overfit to
    # the budget wording and emit conservative rewrites instead of using the
    # registered structural operator.
    state_hint = ""
    if ssot:
        tail = (
            f"{SSOT_SNIPPET}{_INCUMBENT_QUALITY_INSTRUCTION}"
            f"{controller_hint}{state_hint}"
            "Output exactly one new candidate, as the final ```code block```. "
            "Do not write any explanation inside the block."
        )
    else:
        tail = (
            f"{_INCUMBENT_QUALITY_INSTRUCTION}"
            f"{controller_hint}{state_hint}"
            "Output exactly one new candidate in a ```code block```. No explanation."
        )
    return (
        f"{problem.DESCRIPTION}\n\n{shown}\n{g}\n"
        f"Instruction: {instruction}\n"
        f"{operator_hint}{reflection_hint}"
        f"{tail}"
    )


def sample_parents(archive: list[dict], k: int, rng: random.Random,
                    score_spread: bool = False, *,
                    selection_policy: str | None = None) -> list[dict]:
    """上位偏重 + ランダム1体で多様性を担保（エリート近親交配の防止）。

    `score_spread=True` にすると、エリート枠を**スコアの異なる上位解**から取り、
    野生枠をプールの上位半分に限定する。狙いは2つ:

    1. 同じ挙動の解が上位を占めると、LLMには毎世代ほぼ同一の親しか見えず、
       同じものが返る閉ループに入る(2026-07-25 bench_obp実測: 親プールのスコアが
       1〜3種類に潰れた2シードは20世代160呼び出しで改善ゼロ)。
    2. かといってプールの構成自体をいじると質の下限が消える。同日のA/Bで
       Archive.max_per_score=3 を試したところ、空いた席に-5000.0級の壊滅的候補が
       座って親プールの4〜5割を占め、3シード全敗した。よってプールは触らず、
       **見せ方だけ**を多様化し、野生枠にも質の下限を置く。

    既定はFalse(従来動作)。効果を実測で確認してから既定を変えること。
    """
    if not archive:
        return []
    if selection_policy is not None:
        policy = str(selection_policy).strip().lower()
        if policy in {"uniform", "random"}:
            return rng.sample(archive, min(max(0, k), len(archive)))
        if policy in {"diverse", "score_spread"}:
            score_spread = True
        elif policy in {"elite", "top"}:
            score_spread = False
        else:
            raise ValueError(f"unsupported parent selection policy: {selection_policy}")
    n_elite = max(1, k - 1)
    if not score_spread:
        elite = archive[:n_elite]
        wild = [rng.choice(archive)] if len(archive) > len(elite) else []
        return elite + wild

    # archiveはスコア降順。上から見て、スコアが未出のものだけをエリート枠に入れる。
    picked_idx: list[int] = []
    seen_scores: set[float] = set()
    for i, cand in enumerate(archive):
        if len(picked_idx) >= n_elite:
            break
        if cand["score"] in seen_scores:
            continue
        seen_scores.add(cand["score"])
        picked_idx.append(i)
    # スコアの種類がn_elite未満なら上から詰めて席を埋める(親が減ると手本が痩せる)
    for i in range(len(archive)):
        if len(picked_idx) >= n_elite:
            break
        if i not in picked_idx:
            picked_idx.append(i)
    elite = [archive[i] for i in picked_idx]

    # 野生枠は上位半分から。プール末尾の失敗作を手本として見せない。
    pool = archive[: max(1, len(archive) // 2)]
    wild = [rng.choice(pool)] if len(archive) > len(elite) else []
    return elite + wild


def jitter_temperature(base: float, alarm: bool, rng: random.Random) -> float:
    t = base + rng.uniform(-0.2, 0.2)
    if alarm:  # 重複率が高い＝相関エラー発生中。強制的に振る。
        t += 0.4
    return max(0.1, min(1.5, t))


def mutate_params(
    space: dict[str, tuple[float, float]],
    parent: dict,
    rng: random.Random,
    alarm: bool,
    *,
    mutation_operator: str | None = None,
    parent_pool: list[dict] | None = None,
) -> dict:
    """Mutate a numeric candidate without invoking an LLM.

    ``mutation_operator`` is the same registered action field used by the
    controller's code-generation path.  Previously the parametric path
    ignored that field and always performed the legacy local jitter, meaning
    the controller's selected action and the executed search were different.
    The default remains the legacy behaviour for callers that do not provide
    an operator.

    ``parent_pool`` is used by ``recombine`` to perform a bounded per-field
    crossover before a small jitter.  All operators preserve the declared
    parameter bounds and never mutate the input dictionaries.
    """
    new = dict(parent)
    keys = list(space.keys())
    if not keys:
        return new
    forced_key = None
    forced_value = None

    operator = "local" if mutation_operator is None else str(mutation_operator).strip().lower()
    if operator in {"local", "micro"}:
        n_mutate = rng.choice([1, 1, 1, 2]) if not alarm else rng.choice([1, 2, 2])
        scale = 0.4 if alarm else 0.15
    elif operator in {"structural", "global"}:
        # Structural/global moves deliberately touch more coordinates and
        # cross a wider fraction of the declared search box.
        n_mutate = rng.choice([1, 2, 2, 3]) if not alarm else rng.choice([2, 2, 3])
        scale = 0.6 if operator == "global" else 0.4
    elif operator == "recombine":
        pool = [candidate for candidate in (parent_pool or []) if isinstance(candidate, dict)]
        if len(pool) < 2:
            # A single available parent cannot be recombined; retain useful
            # local-search semantics rather than silently doing nothing.
            n_mutate = 1
            scale = 0.15 if not alarm else 0.4
        else:
            # Guarantee that crossover has an observable effect whenever the
            # supplied pool contains a distinct parent.  Purely sampling the
            # current parent for every field would make ``recombine`` a no-op
            # for small action batches.
            distinct = [candidate for candidate in pool if candidate != parent]
            if distinct:
                key = rng.choice(keys)
                source = rng.choice(distinct)
                if key in source:
                    forced_key = key
                    forced_value = source[key]
                    new[key] = forced_value
            for key in keys:
                if key == forced_key:
                    continue
                source = rng.choice(pool)
                if key in source:
                    new[key] = source[key]
            n_mutate = rng.choice([1, 1, 2])
            scale = 0.1 if not alarm else 0.25
    elif operator == "simplify":
        # Pull selected coordinates toward the centre of their declared
        # range.  This is deterministic given the run RNG and is useful for
        # reducing overfit parameter extremes.
        n_mutate = rng.choice([1, 1, 2])
        for key in rng.sample(keys, min(n_mutate, len(keys))):
            lo, hi = space[key]
            midpoint = (lo + hi) / 2.0
            new[key] = parent[key] + (midpoint - parent[key]) * rng.uniform(0.35, 0.8)
        return {
            key: min(space[key][1], max(space[key][0], value))
            for key, value in new.items()
        }
    else:
        raise ValueError(f"unsupported mutation operator: {mutation_operator}")

    for key in rng.sample(keys, min(n_mutate, len(keys))):
        lo, hi = space[key]
        span = hi - lo
        delta = rng.uniform(-scale, scale) * span
        new[key] = min(hi, max(lo, new[key] + delta))
    if forced_key is not None:
        # Keep the guaranteed crossover visible even when the subsequent
        # jitter happens to clip a boundary value back to the current parent.
        lo, hi = space[forced_key]
        new[forced_key] = min(hi, max(lo, forced_value))
    return new
