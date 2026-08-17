"""forge.metrics の検証。

指標はarchive.jsonlから計算するだけなので無料・決定論的。だから1つに絞らず
全部計算して並べる。1指標では検出できない効果が別の指標では検出できる
(2026-07-27実測: max_per_scoreのA/Bは best_score では判定不能だったが、
hit_rate では有意差が出た)。

ここで固定するのは「計算が正しいこと」と「検出力を必ず添えること」の2点。
"""
import json
import math

from forge.metrics import best_so_far, compare, load_rows, run_metrics, summarise


def write_archive(tmp_path, rows, name="archive.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_best_so_far_is_monotone():
    assert best_so_far([1.0, 3.0, 2.0, 5.0]) == [1.0, 3.0, 3.0, 5.0]
    assert best_so_far([1.0, 3.0], start=2.0) == [2.0, 3.0]


def test_load_rows_skips_island_reset_markers(tmp_path):
    path = write_archive(tmp_path, [
        {"text": "a", "score": 1.0, "gen": 1},
        {"island": 0, "island_reset": True, "gen": 2},
        {"text": "b", "score": 2.0, "gen": 3},
    ])
    assert [r["text"] for r in load_rows(path)] == ["a", "b"]


def test_load_rows_tolerates_broken_lines(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text('{"text":"a","score":1.0,"gen":1}\nnot json\n{"score":"x","gen":2}\n',
                    encoding="utf-8")
    assert len(load_rows(path)) == 1


def test_core_metrics(tmp_path):
    path = write_archive(tmp_path, [
        {"text": "seed", "score": -100.0, "gen": 0},
        {"text": "a", "score": -120.0, "gen": 1},
        {"text": "b", "score": -90.0, "gen": 2},
        {"text": "c", "score": -95.0, "gen": 3},
        {"text": "d", "score": -80.0, "gen": 4},
    ])
    m = run_metrics(path, lower_bound=70.0)

    assert m["alive_candidates"] == 4
    assert m["best_score"] == -80.0
    assert m["baseline_score"] == -100.0
    assert m["beats_baseline"] is True
    assert m["gain_over_baseline"] == 20.0
    # best-so-far は -100, -100, -90, -90, -80 → 候補軸の平均
    assert m["auc_by_candidate"] == (-100.0 - 90.0 - 90.0 - 80.0) / 4
    assert m["final_best_gen"] == 4
    assert m["candidates_to_beat_baseline"] == 2, "2件目で初めて基準を超える"
    assert m["largest_single_jump"] == 10.0
    assert math.isclose(m["best_excess_over_lb_pct"], (80.0 - 70.0) / 70.0 * 100)


def test_never_beating_the_baseline_is_none_not_zero(tmp_path):
    """「超えられなかった」を0で埋めると平均が嘘になる。"""
    path = write_archive(tmp_path, [
        {"text": "seed", "score": -100.0, "gen": 0},
        {"text": "a", "score": -150.0, "gen": 1},
    ])
    m = run_metrics(path)
    assert m["beats_baseline"] is False
    assert m["candidates_to_beat_baseline"] is None


def test_caps_align_runs(tmp_path):
    rows = [{"text": "seed", "score": -100.0, "gen": 0}]
    rows += [{"text": f"c{i}", "score": -100.0 + i, "gen": i} for i in range(1, 11)]
    path = write_archive(tmp_path, rows)

    assert run_metrics(path)["best_score"] == -90.0
    assert run_metrics(path, gen_cap=3)["best_score"] == -97.0
    assert run_metrics(path, candidate_cap=2)["alive_candidates"] == 2


def test_bands_report_where_the_gains_happened(tmp_path):
    rows = [{"text": "seed", "score": -100.0, "gen": 0}]
    rows += [{"text": "flat", "score": -100.0, "gen": g} for g in range(1, 3)]
    rows += [{"text": "jump", "score": -50.0, "gen": 4}]
    path = write_archive(tmp_path, rows)

    bands = run_metrics(path, band=2)["bands"]
    assert bands[0]["updates"] == 0 and bands[0]["points_gained"] == 0.0
    assert bands[1]["updates"] == 1 and bands[1]["points_gained"] == 50.0


def test_islands_are_counted(tmp_path):
    path = write_archive(tmp_path, [
        {"text": "seed", "score": -100.0, "gen": 0, "island": 0},
        {"text": "a", "score": -90.0, "gen": 1, "island": 0},
        {"text": "b", "score": -80.0, "gen": 1, "island": 2},
    ])
    assert run_metrics(path)["islands_used"] == 2


def test_efficiency_metrics_need_result_json(tmp_path):
    path = write_archive(tmp_path, [
        {"text": "seed", "score": -100.0, "gen": 0},
        {"text": "a", "score": -90.0, "gen": 1},
    ])
    plain = run_metrics(path)
    assert "cheap_used" not in plain

    withres = run_metrics(path, result={"cheap_used": 10, "cheap_failed": 5, "wall_secs": 60.0})
    assert withres["cheap_failure_rate"] == 5 / 15
    assert withres["alive_per_call"] == 0.1
    assert withres["gain_per_call"] == 1.0


def test_v3_efficiency_metrics_use_attempts_as_the_complete_denominator(tmp_path):
    path = write_archive(tmp_path, [
        {"text": "seed", "score": -100.0, "gen": 0},
        {"text": "a", "score": -90.0, "gen": 1},
    ])
    metrics = run_metrics(
        path,
        result={
            "attempt_count": 10,
            "cheap_used": 10,
            "cheap_failed": 3,
            "wall_secs": 1.0,
        },
    )
    assert metrics["generation_calls"] == 10
    assert metrics["cheap_failure_rate"] == 0.3
    assert metrics["alive_per_call"] == 0.1


def test_v3_efficiency_metrics_are_kept_when_no_candidate_survives(tmp_path):
    path = write_archive(tmp_path, [{"text": "seed", "score": 1.0, "gen": 0}])
    metrics = run_metrics(
        path,
        result={
            "attempt_count": 6,
            "cheap_used": 6,
            "cheap_failed": 6,
            "wall_secs": 1.0,
        },
    )
    assert metrics["alive_candidates"] == 0
    assert metrics["generation_calls"] == 6
    assert metrics["cheap_failure_rate"] == 1.0
    assert metrics["alive_per_call"] == 0.0


def test_summarise_always_reports_detection_power():
    """平均と標準偏差だけ出すと「差が無い」と「測れていない」を混同する。"""
    s = summarise([1.0, 2.0, 3.0])
    assert s["n"] == 3 and s["mean"] == 2.0
    assert s["mde_at_n"]["3"] > s["mde_at_n"]["10"], "nが増えればMDEは下がる"
    assert math.isclose(s["mde_at_n"]["3"], 2.9 * s["stdev"] / math.sqrt(3) * math.sqrt(2))


def test_summarise_handles_missing_values():
    assert summarise([None, None])["n"] == 0
    assert summarise([1.0, None, 3.0])["n"] == 2


def test_compare_marks_a_difference_inside_the_noise_as_undecided():
    ctl = [{"best_score": s} for s in (-100.0, -80.0, -120.0)]
    trt = [{"best_score": s} for s in (-95.0, -85.0, -115.0)]
    r = compare(ctl, trt, keys=["best_score"])["best_score"]
    assert math.isclose(r["diff"], 5.0 / 3), "平均の差(-100 vs -98.33)"
    assert r["decisive"] is False, "ばらつきより小さい差を有意と呼んではいけない"


def test_compare_marks_a_large_clean_difference_as_decisive():
    ctl = [{"best_score": s} for s in (-100.0, -101.0, -99.0)]
    trt = [{"best_score": s} for s in (-50.0, -51.0, -49.0)]
    r = compare(ctl, trt, keys=["best_score"])["best_score"]
    assert r["diff"] == 50.0
    assert r["decisive"] is True
