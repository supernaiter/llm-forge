"""検証カスケード。原則: 上の階層で殺せるものを下の階層に流したら負け。
  V0: 決定論的採点（Problem.score）。必須。無料。ここで99%を棄却する。
  V1: 安価LLM judge。Problemが judge_prompt を定義した場合のみ。形式化不能な目的関数の近似。
  V2: 賢いモデルの反省。生き残った上位kにのみ、Nエポックに1回。出力は「次世代への指針」テキスト。
V0が書けない問題はこのハーネスに載せるべきではない（前提が崩れている）。"""
from __future__ import annotations
import re
from .llm import Budget


def extract_block(text: str) -> str:
    m = re.findall(r"```(?:\w*)\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def v0(problem, candidate: str):
    """(score: float, alive: bool) を返す。例外＝死。
    ただし fatal=True 印付き例外(計測器自体の故障)は再送出してランを止める。
    候補死とインフラ故障を混同すると、壊れたクリップがCER 1.0のまま
    全世代を片肺で走る沈黙故障になる。"""
    try:
        return problem.score(candidate)
    except Exception as e:
        if getattr(e, "fatal", False):
            raise
        return float("-inf"), False


def v1(problem, candidate: str, cheap, budget: Budget) -> bool:
    if not hasattr(problem, "judge_prompt") or not budget.can("cheap"):
        return True
    budget.spend("cheap")
    out = cheap(problem.judge_prompt(candidate), temperature=0.0)
    last = out.strip().splitlines()[-1].upper() if out.strip() else ""
    return "PASS" in last and "FAIL" not in last


def v2_reflect(problem, top: list[dict], smart, budget: Budget) -> str:
    """賢いモデルは発明者ではなく審査員。上位解を見せて戦略的指針だけ買う。"""
    if not budget.can("smart"):
        return ""
    budget.spend("smart")
    listing = "\n\n".join(f"[score={c['score']:.4f}]\n{c['text']}" for c in top)
    prompt = (
        f"You are the judge of a search process. Here are the current top solutions.\n{listing}\n\n"
        f"Problem: {problem.DESCRIPTION}\n"
        "In 3 lines or fewer, state the weaknesses shared by the top solutions and the "
        "mutation direction to try next. Do not write a solution yourself."
    )
    return smart(prompt, temperature=0.3)
