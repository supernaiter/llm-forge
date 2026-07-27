"""変異オペレータ。LLMに残された唯一の生成的役割。
多様性は祈るものではなく注入するもの: 温度ジッタ × プロンプト摂動 × 親の多様サンプリング
× SSoT(cfg["ssot"]、Sakana AI arXiv:2510.21150、build_prompt側の既定はfalseだが
forge-init雛形・stringmax/binpackサンプルでは既定on)。"""
from __future__ import annotations
import random

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


def build_prompt(problem, parents: list[dict], guidance: str, rng: random.Random,
                  ssot: bool = False) -> str:
    shown = "\n\n".join(
        f"# Parent (score={p['score']:.4f})\n```\n{p['text']}\n```" for p in parents
    )
    g = f"\nGuidance from the judge: {guidance}\n" if guidance else ""
    if ssot:
        tail = f"{SSOT_SNIPPET}Output exactly one new candidate, as the final ```code block```. Do not write any explanation inside the block."
    else:
        tail = "Output exactly one new candidate in a ```code block```. No explanation."
    return (
        f"{problem.DESCRIPTION}\n\n{shown}\n{g}\n"
        f"Instruction: {rng.choice(PERTURBATIONS)}\n"
        f"{tail}"
    )


def sample_parents(archive: list[dict], k: int, rng: random.Random,
                    score_spread: bool = False) -> list[dict]:
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


def mutate_params(space: dict[str, tuple[float, float]], parent: dict, rng: random.Random,
                   alarm: bool) -> dict:
    """パラメータ化探索空間の変異。1変異=1(まれに2)パラメータの数値ジッタのみ。
    LLM呼び出し不要（決定論・無料）。崖状スコア関数では「関数まるごと書き換え」より
    「係数を少し動かす」方が生存率が高いという実測(2026-07-06 mc_fusion診断)に基づく。"""
    new = dict(parent)
    keys = list(space.keys())
    n_mutate = min(rng.choice([1, 1, 1, 2]) if not alarm else rng.choice([1, 2, 2]), len(keys))
    for k in rng.sample(keys, n_mutate):
        lo, hi = space[k]
        span = hi - lo
        scale = 0.4 if alarm else 0.15  # 重複率が高い(=局所に張り付き)なら振り幅を広げる
        delta = rng.uniform(-scale, scale) * span
        new[k] = min(hi, max(lo, parent[k] + delta))
    return new
