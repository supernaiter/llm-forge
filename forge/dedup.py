"""SimHash重複排除。並列サンプリングの隠れた前提「エラー無相関」を守る門番。
世代内の重複率が閾値を超えたら多様性アラームを上げ、ループ側が温度を上げる。"""
from __future__ import annotations
import hashlib, re


def simhash(text: str, bits: int = 64) -> int:
    v = [0] * bits
    for tok in re.findall(r"\w+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    return sum(1 << i for i in range(bits) if v[i] > 0)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class DedupIndex:
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.seen: list[int] = []

    def is_novel(self, text: str) -> bool:
        h = simhash(text)
        for s in self.seen:
            if hamming(h, s) <= self.threshold:
                return False
        self.seen.append(h)
        return True


class ExactDedup:
    """完全一致のみを重複とみなす。param_mode向け: SimHashは近い数値
    (例 x=0.501 と x=0.499)をトークン列の近さから誤って重複判定しうる。"""

    def __init__(self, threshold: int = 3):
        self.seen: set[str] = set()

    def is_novel(self, text: str) -> bool:
        if text in self.seen:
            return False
        self.seen.add(text)
        return True
