"""stringmax: ハーネス配管の無料検証用。
決定論的スコア = 文字の多様性 × 長さペナルティ。MockLLMのランダム変異でも改善が観測できる。
これはAPIキーなしで `python cli.py projects/stringmax --mock` を回すためだけの問題。"""


class Problem:
    DESCRIPTION = "Invent a string, at most 40 characters, that contains as many distinct letters of the 26-letter alphabet as possible with no duplicates."

    def seed(self):
        return ["hello world", "abc"]

    def score(self, cand: str):
        cand = cand.strip()
        if not cand or len(cand) > 200:
            return float("-inf"), False
        uniq = len(set(c for c in cand.lower() if c.isalpha()))
        penalty = max(0, len(cand) - 40) * 0.5
        return uniq - penalty, True
