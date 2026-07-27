"""_probe_newproblem: forge本体と cli.py を無改造のまま mock 実行できる最小 fixture."""


class Problem:
    DESCRIPTION = "Minimal probe to confirm forge's mock run passes."

    def seed(self):
        return ["probe fixture"]

    def score(self, cand: str):
        cand = cand.strip()
        if not cand or len(cand) > 120:
            return float("-inf"), False
        return float(len(cand)), True
