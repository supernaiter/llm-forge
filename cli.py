#!/usr/bin/env python3
"""forge CLI: python cli.py <problem_dir> [--mock]

problem_dir は projects/<name> でも、外部リポの問題パック(絶対パス)でもよい。
"""
import argparse, hashlib, json, os, sys, time

REAL_RUN_ENV = "FORGE_REAL_RUN_ALLOWED"


def enforce_real_run_gate():
    if os.environ.get("FORGE_MOCK") == "1":
        return
    if os.environ.get(REAL_RUN_ENV) == "1":
        return
    print("DENIED: 非mock実行は tools/run_real.sh 経由で起動してください", file=sys.stderr)
    raise SystemExit(2)


def main():
    parser = argparse.ArgumentParser(description="Run a forge problem pack.")
    parser.add_argument("problem_dir")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--run-dir", help="write run artifacts to this directory")
    parser.add_argument("--seed", type=int,
                        help="override config.json の seed（多シード測定用。既定はconfigの値）")
    args = parser.parse_args()

    proj = args.problem_dir.rstrip("/")
    if args.mock:
        os.environ["FORGE_MOCK"] = "1"
    enforce_real_run_gate()
    # sys.path経由の通常import(spec_from_file_locationのみだとspawn子プロセスが
    # 「problem」をfresh importできずModuleNotFoundErrorになる。multiprocessing
    # spawnは起動時のsys.pathを子に引き継ぐため、ここでprojをsys.pathへ入れる)
    if proj not in sys.path:
        sys.path.insert(0, proj)
    cfg_path = f"{proj}/config.json"
    if not os.path.isfile(cfg_path):
        print(f"DENIED: missing config.json: {cfg_path}", file=sys.stderr)
        raise SystemExit(2)
    import problem as mod
    config_bytes = open(cfg_path, "rb").read()
    cfg = json.loads(config_bytes.decode("utf-8"))
    # --seed は config.json を書き換えずに乱数シードだけ差し替える。多シード測定
    # (tools/run_benchmark.sh)がconfigのsha256を保ったまま独立ランを並べるために必要。
    if args.seed is not None:
        cfg["seed"] = args.seed
    run_dir = args.run_dir or f"runs/{os.path.basename(proj)}-{time.strftime('%Y%m%d-%H%M%S')}"
    os.makedirs(run_dir, exist_ok=True)
    manifest = {
        "archive_path": f"{run_dir}/archive.jsonl",
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "mock": os.environ.get("FORGE_MOCK") == "1",
        "project": proj,
        "run_dir": run_dir,
        "seed": cfg.get("seed", 0),
    }
    with open(f"{run_dir}/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    from forge.loop import run
    run(mod.Problem(), cfg, run_dir)

if __name__ == "__main__":
    main()
