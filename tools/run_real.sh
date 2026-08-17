#!/bin/zsh
# legacy/product の実LLM API走行の入口。V3登録研究は frozen controller policy、
# run identity、外部 manifest を伴う別の protocol-v3 呼び出しで行う。
# パック毎に1日1走行を日付ロックファイルで機械強制する。
# 成果物置き場は FORGE_REAL_RUNS_DIR で上書き可(既定はリポ内runs/、後方互換)。
# 使い方: zsh tools/run_real.sh <projects/配下の問題名 | 問題パックのパス>
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

PROJ="${1:?usage: run_real.sh <projects/<name> | /path/to/problem_pack>}"
[[ -d "projects/$PROJ" ]] && PROJ="projects/$PROJ"
[[ -f "$PROJ/problem.py" ]] || { echo "no such problem pack: $PROJ"; exit 2; }
PACK_NAME="$(basename "$PROJ")"

if ! python3 tools/validate_problem_pack.py "$PROJ" --json /dev/null; then
  echo "DENIED: ${PACK_NAME}はproblem pack契約違反(tools/validate_problem_pack.py参照)"
  exit 4
fi

RUNS_DIR="${FORGE_REAL_RUNS_DIR:-runs}"
mkdir -p "$RUNS_DIR"
LOCK="$RUNS_DIR/.real_run_${PACK_NAME}_$(date +%Y%m%d).lock"
if [[ -e "$LOCK" ]]; then
  echo "DENIED: ${PACK_NAME}の本日分実API走行は消化済み ($LOCK)。明日まで待つかマスター承認を得ること。"
  exit 3
fi
touch "$LOCK"

if [[ "${FORGE_MOCK:-}" != "1" ]]; then
  [[ -f .env.local ]] || { echo "missing .env.local"; exit 5; }
  set -a; source .env.local; set +a
  # 任意の上書き(例: cheap層をローカルLLMプロキシへ)。キーを含めず接続先だけ変える用途
  [[ -f .env.override ]] && { set -a; source .env.override; set +a; }
fi
export FORGE_REAL_RUN_ALLOWED=1
RUN_DIR="$RUNS_DIR/${PACK_NAME}-$(date +%Y%m%d-%H%M%S)"
exec > >(tee "$RUN_DIR.log") 2>&1
exec python3 cli.py "$PROJ" --run-dir "$RUN_DIR"
