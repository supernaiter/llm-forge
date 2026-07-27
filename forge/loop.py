"""決定論的探索ループ。ここが主であり、LLMは内部の関数呼び出しにすぎない。
制御構造にLLMの「判断」は一切介在しない。全ての分岐はif文と数値比較。"""
from __future__ import annotations
import concurrent.futures as cf
import json, random, time
from .archive import Archive
from .dedup import DedupIndex, ExactDedup, simhash, hamming
from .llm import Budget, make_caller
from .operators import build_prompt, jitter_temperature, mutate_params, sample_parents
from .verify import extract_block, v0, v1, v2_reflect


def run(problem, cfg: dict, run_dir: str):
    t0 = time.time()
    rng = random.Random(cfg.get("seed", 0))
    budget = Budget(cfg.get("max_cheap_calls", 200), cfg.get("max_smart_calls", 3))
    # パラメータ化探索空間(param_space+render)を宣言した問題は、LLMに生コードを
    # 書かせず数値摂動のみで変異する(2026-07-06 mc_fusion診断: 崖状スコア+数値意味論が
    # 厳密な問題では生コード変異の生存率が実測ゼロだったことへの対応。forge本体の共通機構)。
    param_mode = hasattr(problem, "param_space") and hasattr(problem, "render")
    score_spread = cfg.get("parent_score_spread", False)
    cheap = make_caller("cheap") if not param_mode else None
    smart = make_caller("smart") if cfg.get("max_smart_calls", 3) > 0 else None
    # 島モデル(FunSearch中核機構)。独立した部分集団をN個並行に育て、定期的に
    # 弱い島を強い島の解で作り直す。1つの局所解に集団全体が引き寄せられるのを
    # 構造的に防ぐ(2026-07-26実測: 60世代440呼び出しを使って1度も改善しなかった
    # 走行があり、単一集団では抜け出せない張り付きが起きている)。
    # islands=1 で従来動作。JSONLは1本を共有し、行の "island" で島を分ける。
    n_islands = max(1, cfg.get("islands", 1))
    archive_path = f"{run_dir}/archive.jsonl"
    archives = [Archive(archive_path, cfg.get("archive_capacity", 50),
                        cfg.get("max_per_score", 0), island=i)
                for i in range(n_islands)]
    archive = archives[0]
    dedup = ExactDedup() if param_mode else DedupIndex(cfg.get("dedup_hamming", 3))
    guidance, alarm = "", False
    prev_guidance_hash = None
    stopped_by = "generations_complete"
    cheap_failed = 0
    dead_generations = 0  # 1件も応答が返らなかった世代の連続回数

    # 初期集団: seedはV0を通してから入れる（採点不能なseedは設定ミス）
    # 島は全て同じseedから出発し、以降は独立に育つ(dedupは全島で共有するので、
    # 同じ文面が別の島で重複して生き残ることはない)。
    if not any(a.items for a in archives):
        if param_mode:
            space = problem.param_space()
            seeds_params = problem.seed_params() if hasattr(problem, "seed_params") else \
                [{k: (lo + hi) / 2 for k, (lo, hi) in space.items()}]
            for p in seeds_params:
                text = problem.render(p)
                score, alive = v0(problem, text)
                assert alive, "paramシードがV0を通りません。問題定義を直してください。"
                dedup.is_novel(text)
                for a in archives:
                    a.add({"text": text, "params": p, "score": score, "gen": 0})
        else:
            for s in problem.seed():
                score, alive = v0(problem, s)
                assert alive, "seedがV0を通りません。問題定義を直してください。"
                dedup.is_novel(s)
                for a in archives:
                    a.add({"text": s, "score": score, "gen": 0})
    else:
        # 再開時: 既存解をdedup索引に登録しないと言い換えが素通りしV0を浪費する
        for a in archives:
            for it in a.items:
                dedup.is_novel(it["text"])

    generations_done = 0
    for gen in range(1, cfg.get("generations", 20) + 1):
        if not budget.can("cheap"):
            print(f"[gen {gen}] cheap予算枯渇。終了。")
            stopped_by = "budget_exhausted"
            break

        # --- 生成（並列・多様性注入） ---
        n = min(cfg.get("batch_size", 8), budget.max_cheap - budget.cheap_used)
        text_to_params = {}
        if param_mode:
            space = problem.param_space()
            outs = []
            for slot in range(n):
                home = archives[slot % n_islands]
                parents = sample_parents(home.items, cfg.get("parents", 3), rng, score_spread)
                parent = rng.choice(parents)["params"] if parents else \
                    {k: (lo + hi) / 2 for k, (lo, hi) in space.items()}
                new_params = mutate_params(space, parent, rng, alarm)
                text = problem.render(new_params).strip()  # extract_block()もstrip()するためキーを合わせる
                text_to_params[text] = new_params
                outs.append("```\n" + text + "\n```")
        else:
            prompts = []
            for slot in range(n):
                # 島ごとに親を引く。同じ世代の候補が別々の部分集団から派生する。
                home = archives[slot % n_islands]
                parents = sample_parents(home.items, cfg.get("parents", 3), rng, score_spread)
                prompts.append((build_prompt(problem, parents, guidance, rng, cfg.get("ssot", False)),
                                jitter_temperature(cfg.get("temperature", 0.8), alarm, rng)))
            # cheap_workers は LLM 呼び出しの同時発火数。V0採点の並列度(workers)とは
            # 別に持つ。無料枠の429がレート制限(単位時間あたりの総数)由来なのか
            # 同時実行数由来なのかを切り分けるとき、両方を1つのキーで動かすと
            # 壁時計の増加分が採点側の減速と混ざって帰属できなくなる。既定はworkers。
            cheap_workers = cfg.get("cheap_workers") or cfg.get("workers", 8)
            with cf.ThreadPoolExecutor(max_workers=cheap_workers) as ex:
                outs = list(ex.map(lambda pt: _safe(cheap, *pt), prompts))
        if param_mode:
            # param_modeはLLMを呼ばない。予算は生成候補数の上限として機能させる。
            budget.cheap_used += n
        else:
            # 応答が返らなかった呼び出し(429/接続断/空応答)は候補を1件も生まないので
            # 予算から除外する。数えてしまうと無料枠のレート制限がそのまま探索回数の
            # 目減りになる(2026-07-25 bench_obp実測: 480呼び出し中138回が失敗)。
            failed = sum(1 for out in outs if not out)
            cheap_failed += failed
            budget.cheap_used += n - failed
            if failed == n:
                dead_generations += 1
                if dead_generations >= cfg.get("max_dead_generations", 3):
                    print(f"[gen {gen}] cheap層が{dead_generations}世代連続で全滅。終了。")
                    stopped_by = "llm_unavailable"
                    break
            else:
                dead_generations = 0

        # --- dedup → V0 → V1 の順で安い方から棄却 ---
        novel, dup = [], 0
        cand_island: dict[str, int] = {}
        for slot, out in enumerate(outs):
            cand = extract_block(out)
            if not cand:
                continue
            if not dedup.is_novel(cand):
                dup += 1
                continue
            cand_island[cand] = slot % n_islands
            novel.append(cand)

        survivors = []
        for cand, score, alive in score_candidates(problem, novel, cfg.get("workers", 8)):
            if not alive:
                continue
            if not param_mode and not v1(problem, cand, cheap, budget):
                continue
            item = {"text": cand, "score": score, "gen": gen,
                    "island": cand_island.get(cand, 0)}
            if param_mode and cand in text_to_params:
                item["params"] = text_to_params[cand]
            survivors.append(item)
        alarm = (dup / max(1, n)) > cfg.get("dup_alarm_rate", 0.5)

        for item in survivors:
            archives[item["island"]].add(item)

        # --- 島の入れ替え(FunSearch型): 弱い島を強い島の解で作り直す ---
        migrate_every = cfg.get("migrate_every", 0)
        if n_islands > 1 and migrate_every and gen % migrate_every == 0:
            ranked = sorted(
                (a for a in archives if a.items),
                key=lambda a: -a.best["score"],
            )
            if len(ranked) > 1:
                strong = ranked[: max(1, len(ranked) // 2)]
                weak = ranked[max(1, len(ranked) // 2):]
                for a in weak:
                    donor = rng.choice(strong)
                    a.reset_to(dict(donor.best, gen=gen))
                print(f"[gen {gen}] 島の入れ替え: 弱い{len(weak)}島を作り直した")

        live = [a for a in archives if a.items]
        best = max((a.best for a in live), key=lambda c: c["score"])
        distinct = len({c["score"] for a in live for c in a.items})
        # scores= は親プール内の異なるスコア数。1〜数種類に潰れたら探索は閉ループに
        # 入っている(dup率は低いままなので、dup率だけ見ていても気づけない)。
        island_note = f" islands={n_islands}" if n_islands > 1 else ""
        print(f"[gen {gen}] batch={n} dup={dup} alive={len(survivors)} "
              f"best={best['score']:.4f} scores={distinct} alarm={alarm}{island_note} "
              f"budget={budget.cheap_used}/{budget.max_cheap}")
        generations_done = gen

        # --- V2: Nエポックに1回だけ賢いモデルに指針を買いに行く ---
        if smart and gen % cfg.get("reflect_every", 5) == 0 and live:
            top = sorted((c for a in live for c in a.items),
                         key=lambda c: -c["score"])[:3]
            guidance = v2_reflect(problem, top, smart, budget)
            if guidance:
                print(f"[gen {gen}] 審査員指針: {guidance[:100]}...")
                h = simhash(guidance)
                if prev_guidance_hash is not None and hamming(h, prev_guidance_hash) <= 3:
                    print(f"[gen {gen}] 審査員指針が収束。反省チャネルを停止。")
                    smart = None
                else:
                    prev_guidance_hash = h

    live = [a for a in archives if a.items]
    overall = max((a.best for a in live), key=lambda c: c["score"])
    print(f"\n=== BEST (score={overall['score']:.4f}) ===\n{overall['text']}")
    with open(f"{run_dir}/result.json", "w", encoding="utf-8") as f:
        json.dump({
            "archive_distinct_scores": len({c["score"] for a in live for c in a.items}),
            "best_score": overall["score"],
            "islands": n_islands,
            "cheap_failed": cheap_failed,
            "generations_done": generations_done,
            "cheap_used": budget.cheap_used,
            "smart_used": budget.smart_used,
            "wall_secs": time.time() - t0,
            "stopped_by": stopped_by,
        }, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return overall


def score_candidates(problem, candidates: list[str], workers: int):
    if workers <= 1 or len(candidates) <= 1:
        return [(cand, *v0(problem, cand)) for cand in candidates]

    max_workers = min(workers, len(candidates))
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(lambda cand: (cand, *v0(problem, cand)), candidates))


def _safe(fn, prompt, temp):
    try:
        return fn(prompt, temperature=temp)
    except Exception as e:
        print(f"[warn] call failed: {e}")
        return ""
