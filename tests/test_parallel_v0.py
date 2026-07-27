import time

from forge.loop import score_candidates


class _SleepProblem:
    def __init__(self, delay: float):
        self.delay = delay

    def score(self, cand: str):
        time.sleep(self.delay)
        return float(len(cand)), True


def test_score_candidates_parallel_is_faster_than_sequential():
    problem = _SleepProblem(0.05)
    candidates = [f"cand-{i}" for i in range(8)]

    start = time.perf_counter()
    sequential = score_candidates(problem, candidates, workers=1)
    sequential_secs = time.perf_counter() - start

    start = time.perf_counter()
    parallel = score_candidates(problem, candidates, workers=8)
    parallel_secs = time.perf_counter() - start

    assert [cand for cand, *_ in sequential] == candidates
    assert [cand for cand, *_ in parallel] == candidates
    assert parallel_secs < sequential_secs * 0.5
