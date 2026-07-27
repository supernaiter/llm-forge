#!/bin/zsh
# 確立ベンチマークの多シード走行の入口。同一パックをシード違いでN回走らせ、
# 1回の走行では見えないばらつきごとforgeの実力を測る。
#
# 使い方:
#   zsh tools/run_benchmark.sh bench_obp 3           # 実LLM。1日1セッションの日付ロックあり
#   zsh tools/run_benchmark.sh bench_obp 2 --mock    # 配管確認。ロック対象外
#
# 成果物: runs/bench/<pack>/<YYYYMMDD>/seed<i>/{archive.jsonl,result.json,manifest.json}
# 集計:   python3 tools/report_benchmark.py <pack> → reports/benchmark_<pack>_<date>.{md,json}
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

PROJ="${1:?usage: run_benchmark.sh <pack> [n_seeds] [--mock]}"
N_SEEDS="${2:-3}"
MOCK="${3:-}"
[[ -d "projects/$PROJ" ]] && PROJ="projects/$PROJ"
[[ -f "$PROJ/problem.py" ]] || { echo "no such problem pack: $PROJ"; exit 2; }
PACK_NAME="$(basename "$PROJ")"

if [[ "$N_SEEDS" != <-> ]] || (( N_SEEDS < 1 )); then
  echo "DENIED: n_seedsは1以上の整数で指定してください (given: $N_SEEDS)"; exit 2
fi

if ! python3 tools/validate_problem_pack.py "$PROJ" --json /dev/null; then
  echo "DENIED: ${PACK_NAME}はproblem pack契約違反(tools/validate_problem_pack.py参照)"
  exit 4
fi

RUNS_DIR="${FORGE_REAL_RUNS_DIR:-runs}"
DATE="$(date +%Y%m%d)"
BENCH_DIR="$RUNS_DIR/bench/$PACK_NAME/$DATE"
mkdir -p "$BENCH_DIR"

if [[ "$MOCK" == "--mock" ]]; then
  export FORGE_MOCK=1
else
  # 実API走行はrun_real.shと同じ思想で1パック1日1セッションに機械的に制限する。
  LOCK="$RUNS_DIR/bench/.bench_${PACK_NAME}_${DATE}.lock"
  if [[ -e "$LOCK" ]]; then
    echo "DENIED: ${PACK_NAME}の本日分ベンチ走行は消化済み ($LOCK)。明日まで待つかマスター承認を得ること。"
    exit 3
  fi
  touch "$LOCK"
  [[ -f .env.local ]] || { echo "missing .env.local"; exit 5; }
  set -a; source .env.local; set +a
  [[ -f .env.override ]] && { set -a; source .env.override; set +a; }
fi
export FORGE_REAL_RUN_ALLOWED=1

# 1シードの失敗でセッション全体を落とさない。長時間走行はディスクI/Oエラーや
# APIの一時停止で落ちうるが、そこで打ち切ると生き残ったシードまで測れなくなる
# (2026-07-26実測: 外付けAPFSの一時I/Oエラーでseed0が死に、set -eでseed1/2が未実行に終わった)。
# 失敗したシードは result.json を残さないので、report_benchmark.py が「失敗」行として出す。
FAILED_SEEDS=()
for (( i = 0; i < N_SEEDS; i++ )); do
  RUN_DIR="$BENCH_DIR/seed$i"
  echo "=== [$PACK_NAME] seed=$i -> $RUN_DIR"
  if ! python3 cli.py "$PROJ" --run-dir "$RUN_DIR" --seed "$i" 2>&1 | tee "$RUN_DIR.log"; then
    echo "WARN: seed=$i が異常終了した。残りのシードは続行する。"
    FAILED_SEEDS+=("$i")
  fi
done

if (( ${#FAILED_SEEDS[@]} > 0 )); then
  echo "WARN: 異常終了したシード: ${FAILED_SEEDS[*]}"
fi

python3 tools/report_benchmark.py "$PACK_NAME" --date "$DATE" \
  --runs-dir "$RUNS_DIR" --reports-dir "${FORGE_REPORTS_DIR:-reports}"
