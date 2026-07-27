#!/usr/bin/env python3
"""cheap層のモデル候補を、フル走行せずにふるい落とす。

cheap層は発明者ではなく変異オペレータである。実測(bench_obp 2,400候補)では
V0生存率57〜74%、生存候補のうちbest-fitを超えたのは6.7%しかない。つまり求められるのは
賢さではなく「動くコードを、親と少し違う形で、たくさん」書けることである。

よって小さいモデルでも、生存率の低下をサンプル数の増加が上回れば勝てる。
どこに崖があるかはモデルごとに違うので、ここで測る。フル走行(3シード3時間)は要らない。

測る指標:
  extract_rate  ```ブロックを取り出せた割合(書式に従えているか)
  alive_rate    V0(決定論採点)を通った割合 = 動くコードだったか
  distinct      生存候補の挙動指紋が何種類に散ったか = 多様性
  hit_rate      基準ヒューリスティック(seed)を超えた割合
  throughput    1分あたりの生存候補数 ← 最終的にこれが効く

使い方:
  python3 tools/screen_models.py --pack projects/bench_obp \
      --base-url http://localhost:8013/v1 --model qwen3-coder-30b-par \
      --samples 32 --concurrency 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.operators import build_prompt  # noqa: E402
from forge.verify import extract_block  # noqa: E402


def load_pack(pack: Path):
    pack_dir = str(pack.resolve())
    if pack_dir not in sys.path:
        sys.path.insert(0, pack_dir)
    sys.modules.pop("problem", None)
    import problem  # noqa: PLC0415
    return problem


def seed_parents(problem_mod, k: int = 3) -> list[dict[str, Any]]:
    """シードを親として使う。走行開始直後と同じ条件でモデルを比べるため。"""
    problem = problem_mod.Problem()
    parents = []
    for text in problem.seed():
        score, alive = problem.score(text)
        if alive:
            parents.append({"text": text, "score": score})
    parents.sort(key=lambda p: -p["score"])
    return (parents * k)[:k]


def chat(base_url: str, api_key: str, model: str, prompt: str,
         temperature: float, timeout: int) -> str:
    body = {
        "model": model, "temperature": temperature, "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def behaviour_fingerprint(problem_mod, source: str) -> str | None:
    """候補を実行して挙動の指紋を取る。文面が違っても挙動が同じなら同一とみなす。

    テキストの重複排除(SimHash)は書き換えを素通しするため、多様性の指標にならない
    (2026-07-25実測: 生存プールの挙動重複率76.7%)。
    """
    probe = getattr(problem_mod, "behaviour_probe", None)
    if not callable(probe):
        return None
    try:
        trace = probe(source)
    except Exception:
        return None
    return hashlib.blake2b(repr(trace).encode(), digest_size=8).hexdigest()


def screen(pack: Path, endpoints: list[dict[str, str]], samples: int,
           concurrency: int, temperature: float, timeout: int,
           ssot: bool = True) -> dict[str, Any]:
    """endpointsが2件以上ならforge本体のPOOLと同じ規則で毎回起点を回す。"""
    problem_mod = load_pack(pack)
    problem = problem_mod.Problem()
    parents = seed_parents(problem_mod)
    baseline = max(p["score"] for p in parents)
    rng = random.Random(0)
    prompts = [build_prompt(problem, parents, "", rng, ssot) for _ in range(samples)]

    t0 = time.time()
    outs: list[tuple[str, str | None]] = []
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = []
        for i, p in enumerate(prompts):
            ep = endpoints[i % len(endpoints)]
            futs.append((ep["model"], ex.submit(
                chat, ep["base_url"], ep.get("api_key", "none"), ep["model"],
                p, temperature, timeout)))
        for model_name, f in futs:
            try:
                outs.append((model_name, f.result()))
            except Exception:
                outs.append((model_name, None))
    gen_secs = time.time() - t0

    failed = sum(1 for _, o in outs if o is None)
    # 応答は返ったが```ブロックを取り出せなかった件数を、API失敗と分けて数える。
    # 一緒にすると「モデルが書式に従えない」と「サーバが落ちている/詰まっている」を
    # 取り違える(2026-07-27: 7サーバ同時稼働でホストが逼迫した状態の測定を
    # モデルの実力として報告してしまった)。
    no_block = sum(1 for _, o in outs if o and not extract_block(o).strip())
    samples_raw = [o for _, o in outs if o]
    cands = [(m, extract_block(o)) for m, o in outs if o]
    cands = [(m, c) for m, c in cands if c.strip()]

    t1 = time.time()
    alive, fps, hits = [], set(), 0
    alive_by_model: dict[str, int] = {}
    for model_name, c in cands:
        try:
            score, ok = problem.score(c)
        except Exception:
            continue
        if not ok:
            continue
        alive.append(score)
        alive_by_model[model_name] = alive_by_model.get(model_name, 0) + 1
        if score > baseline:
            hits += 1
        fp = behaviour_fingerprint(problem_mod, c)
        if fp:
            fps.add(fp)
    eval_secs = time.time() - t1

    n = max(1, samples)
    label = endpoints[0]["model"] if len(endpoints) == 1 else \
        "pool(" + "+".join(e["model"] for e in endpoints) + ")"
    return {
        "model": label,
        "ssot": ssot,
        "endpoints": [e["model"] for e in endpoints],
        "alive_by_model": alive_by_model,
        "samples": samples,
        "concurrency": concurrency,
        "api_failed": failed,
        "no_code_block": no_block,
        "sample_output": (samples_raw[0][:400] if samples_raw else None),
        "extracted": len(cands),
        "extract_rate": len(cands) / n,
        "alive": len(alive),
        "alive_rate": len(alive) / n,
        "distinct_behaviours": len(fps) if fps else None,
        "baseline_score": baseline,
        "hits_over_baseline": hits,
        "best_score": max(alive) if alive else None,
        "gen_secs": round(gen_secs, 1),
        "eval_secs": round(eval_secs, 1),
        "alive_per_min": round(len(alive) / (gen_secs / 60), 2) if gen_secs else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Screen cheap-tier model candidates.")
    ap.add_argument("--pack", default="projects/bench_obp")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key", default="none")
    ap.add_argument("--model")
    ap.add_argument("--pool", help='JSON配列。例: \'[{"base_url":"...","model":"a"},...]\'。'
                                   "2件以上ならforge本体のPOOLと同じ規則で毎回起点を回す")
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--no-ssot", action="store_true",
                    help="SSoT多様性プロトコルを外す。論文(arXiv:2510.21150)は8B以下の"
                         "モデルでは戦略を実行できず性能が落ちると明記しているため、"
                         "小型モデルではon/offを実測して決めること")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--out", help="append the verdict as one JSON line to this file")
    args = ap.parse_args()

    pack = Path(args.pack)
    if not pack.is_absolute():
        pack = ROOT / pack
    if args.pool:
        endpoints = json.loads(args.pool)
    elif args.base_url and args.model:
        endpoints = [{"base_url": args.base_url, "api_key": args.api_key, "model": args.model}]
    else:
        ap.error("--pool か (--base-url と --model) のどちらかを指定してください")
    verdict = screen(pack, endpoints, args.samples,
                     args.concurrency, args.temperature, args.timeout,
                     ssot=not args.no_ssot)
    line = json.dumps(verdict, ensure_ascii=False)
    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)
    return 0 if verdict["alive"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
