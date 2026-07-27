"""SSoT(Sakana AI, arXiv:2510.21150)多様性プロトコルの配線検証(2026-07-08追加)。
既定offで既存プロンプトを変えないこと、onでseed/推論過程が候補本文に混入せず
extract_block(=最後の```ブロックのみ抽出)で自然にstripされることを確認する。"""
from __future__ import annotations

import random

from forge.operators import SSOT_SNIPPET, build_prompt
from forge.verify import extract_block


class _Problem:
    DESCRIPTION = "テスト問題"


def test_build_prompt_default_omits_ssot():
    p = build_prompt(_Problem(), [], "", random.Random(1))
    assert SSOT_SNIPPET not in p


def test_build_prompt_ssot_true_includes_snippet():
    p = build_prompt(_Problem(), [], "", random.Random(1), ssot=True)
    assert SSOT_SNIPPET in p


def test_extract_block_strips_ssot_reasoning():
    fake_output = (
        "1. seed: a3f9k2m8x0q1z7b4\n"
        "2. 6方向性: 定石重視/データ駆動/コスト最小/リスク回避/大胆な再構成/局所微修正\n"
        "3. mod 6 = 3 (リスク回避)\n"
        "4. リスク回避の観点から候補を作る。\n\n"
        "```\ncandidate-text-only\n```\n"
    )
    cand = extract_block(fake_output)
    assert cand == "candidate-text-only"
    assert "seed" not in cand and "方向性" not in cand
