#!/usr/bin/env python3
"""forge CLI: python cli.py <problem_dir> [--mock]

problem_dir は projects/<name> でも、外部リポの問題パック(絶対パス)でもよい。
"""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path

from forge.manifest import freeze_manifest, verify_manifest_unchanged
from forge.protocol import load_protocol, protocol_hash
from forge.controller import load_controller_manifest
from forge.llm import make_caller
from forge.model_routes import (
    ControllerModelRoutes,
    build_controller_model_callers,
    load_controller_model_routes,
    validate_routes_against_model_manifest,
)

REAL_RUN_ENV = "FORGE_REAL_RUN_ALLOWED"


def enforce_real_run_gate():
    if os.environ.get("FORGE_MOCK") == "1":
        return
    if os.environ.get(REAL_RUN_ENV) == "1":
        return
    print("DENIED: 非mock実行は tools/run_real.sh 経由で起動してください", file=sys.stderr)
    raise SystemExit(2)


def _git_source_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNPINNED"
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else "UNPINNED"


def main():
    parser = argparse.ArgumentParser(description="Run a forge problem pack.")
    parser.add_argument("problem_dir")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--protocol-v3", action="store_true",
                        help="enable V3 attempt ledger/replay instrumentation")
    parser.add_argument(
        "--controller-policy",
        help="path to a frozen V3 controller policy manifest",
    )
    parser.add_argument(
        "--controller-model-routes",
        help="path to a frozen controller model-identity to adapter-tier route manifest",
    )
    parser.add_argument(
        "--model-manifest",
        help="path to the validated frozen V3 model manifest referenced by routes",
    )
    parser.add_argument(
        "--run-identity",
        help="JSON object containing the frozen registered V3 run identity",
    )
    parser.add_argument("--run-dir", help="write run artifacts to this directory")
    parser.add_argument("--seed", type=int,
                        help="override config.json の seed（多シード測定用。既定はconfigの値）")
    args = parser.parse_args()

    proj = args.problem_dir.rstrip("/")
    if args.mock:
        os.environ["FORGE_MOCK"] = "1"
    enforce_real_run_gate()
    controller = None
    if args.controller_policy and not args.protocol_v3:
        print("DENIED: --controller-policy requires --protocol-v3", file=sys.stderr)
        raise SystemExit(2)
    if args.controller_model_routes and not args.protocol_v3:
        print("DENIED: --controller-model-routes requires --protocol-v3", file=sys.stderr)
        raise SystemExit(2)
    if args.model_manifest and not args.protocol_v3:
        print("DENIED: --model-manifest requires --protocol-v3", file=sys.stderr)
        raise SystemExit(2)
    if args.controller_policy:
        try:
            controller = load_controller_manifest(args.controller_policy)
        except (OSError, ValueError) as exc:
            print(f"DENIED: invalid controller policy: {exc}", file=sys.stderr)
            raise SystemExit(2)
    if args.controller_model_routes and controller is None:
        print(
            "DENIED: --controller-model-routes requires --controller-policy",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.model_manifest and controller is None:
        print("DENIED: --model-manifest requires --controller-policy", file=sys.stderr)
        raise SystemExit(2)
    if args.model_manifest and not args.controller_model_routes:
        print(
            "DENIED: --model-manifest requires --controller-model-routes",
            file=sys.stderr,
        )
        raise SystemExit(2)
    # A non-mock V3 run is a registered-study execution path, not merely an
    # instrumentation toggle.  Refuse to call it research-ready without the
    # frozen controller identity; public mock dry-runs remain available.
    if args.protocol_v3 and not args.mock and controller is None:
        print(
            "DENIED: non-mock V3 execution requires --controller-policy "
            "from a frozen development-only policy",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.protocol_v3 and not args.mock and not args.controller_model_routes:
        print(
            "DENIED: non-mock V3 execution requires --controller-model-routes",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if args.protocol_v3 and not args.mock and not args.model_manifest:
        print(
            "DENIED: non-mock V3 execution requires --model-manifest",
            file=sys.stderr,
        )
        raise SystemExit(2)
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
    if args.run_identity:
        try:
            identity = json.loads(Path(args.run_identity).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"DENIED: invalid run identity: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(identity, dict):
            print("DENIED: run identity must be a JSON object", file=sys.stderr)
            raise SystemExit(2)
        allowed_identity_fields = {
            "study_id", "study_version", "run_id", "method_id", "problem_id",
            "problem_family", "distribution", "model_tier", "seed", "seed_role",
        }
        unknown_identity_fields = set(identity) - allowed_identity_fields
        if unknown_identity_fields:
            print(
                "DENIED: unknown run identity fields: "
                + ", ".join(sorted(unknown_identity_fields)),
                file=sys.stderr,
            )
            raise SystemExit(2)
        cfg.update(identity)
    # --seed は config.json を書き換えずに乱数シードだけ差し替える。多シード測定
    # (tools/run_benchmark.sh)がconfigのsha256を保ったまま独立ランを並べるために必要。
    if args.seed is not None:
        if "seed" in cfg and args.run_identity and cfg.get("seed") != args.seed:
            print("DENIED: --seed conflicts with run identity seed", file=sys.stderr)
            raise SystemExit(2)
        cfg["seed"] = args.seed
    controller_routes: ControllerModelRoutes | None = None
    model_manifest_hash: str | None = None
    if args.controller_model_routes:
        try:
            controller_routes = load_controller_model_routes(
                args.controller_model_routes,
                controller=controller,
            )
            if args.model_manifest:
                model_manifest_hash = validate_routes_against_model_manifest(
                    controller_routes,
                    args.model_manifest,
                )
            cfg["controller_model_callers"] = build_controller_model_callers(
                controller_routes,
                caller_factory=make_caller,
                seed=int(cfg.get("seed", 0)),
            )
        except (OSError, ValueError, TypeError) as exc:
            print(f"DENIED: invalid controller model routes: {exc}", file=sys.stderr)
            raise SystemExit(2)
    if args.protocol_v3 and not args.mock:
        required_identity = {
            "study_id", "study_version", "run_id", "method_id", "problem_id",
            "problem_family", "distribution", "model_tier", "seed", "seed_role",
        }
        if not required_identity.issubset(cfg):
            print(
                "DENIED: non-mock V3 execution requires complete --run-identity",
                file=sys.stderr,
            )
            raise SystemExit(2)
    if args.protocol_v3:
        # Problem packs use this explicit switch to select the V3 restricted
        # candidate sandbox.  forge.loop.run scopes/restores it as well for
        # direct API callers; setting it here covers pack-side setup and keeps
        # CLI and API execution paths equivalent.
        os.environ["FORGE_PROTOCOL_V3"] = "1"
        cfg["protocol_v3"] = True
        cfg.setdefault("max_attempts", cfg.get("max_cheap_calls", 200))
        v3_protocol = load_protocol()
        v3_budgets = v3_protocol.get("budgets", {})
        cfg.setdefault("max_evaluator_calls", v3_budgets.get("max_search_evaluations"))
        cfg.setdefault("resource_budgets", {
            "generation": {
                "records": cfg["max_attempts"],
                "input_tokens": v3_budgets.get("max_input_tokens"),
                "output_tokens": v3_budgets.get("max_output_tokens"),
            },
            "evaluator": {
                "calls": v3_budgets.get("max_search_evaluations"),
            },
        })
    run_dir = args.run_dir or f"runs/{os.path.basename(proj)}-{time.strftime('%Y%m%d-%H%M%S')}"
    os.makedirs(run_dir, exist_ok=True)
    events_path = Path(run_dir) / "events.jsonl"
    if events_path.exists() and not args.protocol_v3:
        print("DENIED: V3 event ledger exists; resume with --protocol-v3", file=sys.stderr)
        raise SystemExit(2)
    manifest = {
        "archive_path": f"{run_dir}/archive.jsonl",
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "mock": os.environ.get("FORGE_MOCK") == "1",
        "project": proj,
        "run_dir": run_dir,
        "seed": cfg.get("seed", 0),
        "execution_mode": "V3" if args.protocol_v3 else "LEGACY",
        "protocol_v3": bool(args.protocol_v3),
        "protocol_id": "FORGE_RESEARCH_V3" if args.protocol_v3 else "FORGE_LEGACY",
        "protocol_sha256": protocol_hash() if args.protocol_v3 else None,
        "source_commit": _git_source_commit(),
        "research_eligible": bool(
            args.protocol_v3
            and not args.mock
            and controller is not None
            and all(field in cfg for field in (
                "study_id", "study_version", "run_id", "method_id",
                "problem_id", "problem_family", "distribution", "model_tier",
                "seed", "seed_role",
            ))
        ),
        "controller_policy_sha256": (
            controller.policy_sha256 if controller is not None else None
        ),
        "controller_policy_manifest_sha256": (
            hashlib.sha256(Path(args.controller_policy).read_bytes()).hexdigest()
            if args.controller_policy else None
        ),
        "controller_model_routes_manifest_id": (
            controller_routes.manifest_id if controller_routes is not None else None
        ),
        "controller_model_routes_sha256": (
            controller_routes.sha256 if controller_routes is not None else None
        ),
        "model_manifest_sha256": model_manifest_hash,
    }
    manifest_path = Path(run_dir) / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"DENIED: invalid run manifest: {manifest_path}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if existing.get("frozen") is True:
            try:
                for key in (
                    "project", "run_dir", "config_sha256", "protocol_sha256",
                    "controller_model_routes_manifest_id",
                    "controller_model_routes_sha256",
                    "model_manifest_sha256",
                ):
                    if existing.get(key) != manifest.get(key):
                        raise ValueError(f"frozen manifest identity changed: {key}")
                verify_manifest_unchanged(existing, existing)
            except ValueError as exc:
                print(f"DENIED: frozen manifest is invalid: {exc}", file=sys.stderr)
                raise SystemExit(2)
        # A resume must retain the exact manifest that defined the run.  The
        # legacy fields are intentionally kept stable for existing callers.
        manifest = existing
    else:
        if cfg.get("freeze_manifest"):
            try:
                manifest = freeze_manifest(manifest)
            except ValueError as exc:
                print(f"DENIED: cannot freeze incomplete manifest: {exc}", file=sys.stderr)
                raise SystemExit(2)
        with manifest_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
    from forge.loop import run
    run(mod.Problem(), cfg, run_dir, controller=controller)

if __name__ == "__main__":
    main()
