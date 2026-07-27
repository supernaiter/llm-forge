#!/bin/zsh
# forge本体の変更の効果を、同じ日・同じ条件で測るためのA/B走行。
#
# 変更をconfig keyでゲートしておき、そのkeyの値だけが違う2部のパックコピーを作って
# 両方を同じ日に走らせる。パック名が違うので run_benchmark.sh の1日1セッションロックは
# 腕ごとに独立する(2026-07-08 ssot検証で使った別名パックコピーの前例を踏襲)。
#
# 使い方:
#   zsh tools/run_ab.sh bench_obp max_per_score 0 3 3
#   zsh tools/run_ab.sh bench_obp max_per_score 0 3 2 --mock
#
# 出力: reports/ab_<pack>_<YYYYMMDD>.md（両腕の平均・差・シード別・多様性計器）
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

PACK="${1:?usage: run_ab.sh <pack> <config_key> <valueA> <valueB> [n_seeds] [--mock]}"
KEY="${2:?config key to vary}"
VAL_A="${3:?control value}"
VAL_B="${4:?treatment value}"
N_SEEDS="${5:-3}"
MOCK="${6:-}"

SRC="$PACK"
[[ -d "projects/$SRC" ]] && SRC="projects/$SRC"
[[ -f "$SRC/problem.py" ]] || { echo "no such problem pack: $SRC"; exit 2; }
PACK_NAME="$(basename "$SRC")"

WORK="${FORGE_AB_WORKDIR:-${TMPDIR:-/tmp}/forge_ab}"
DATE="$(date +%Y%m%d)"
# 腕名に実験名(config key)を含める。含めないと同じ日に2本目のA/Bを回そうとした時に
# run_benchmark.shの日次ロックが前のA/Bのものと衝突して即DENIEDになる(2026-07-25実測)。
ARM="${PACK_NAME}_${KEY}"
CTL="$WORK/${ARM}_ctl"
TRT="$WORK/${ARM}_trt"
rm -rf "$CTL" "$TRT"
mkdir -p "$WORK"
cp -R "$SRC" "$CTL"
cp -R "$SRC" "$TRT"
rm -rf "$CTL/__pycache__" "$TRT/__pycache__"

# config.jsonのkeyだけを差し替える。他の設定は元パックと同一に保つ。
python3 - "$CTL/config.json" "$KEY" "$VAL_A" <<'PY'
import json, sys
path, key, raw = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.loads(open(path, encoding="utf-8").read())
try:
    cfg[key] = json.loads(raw)
except json.JSONDecodeError:
    cfg[key] = raw
open(path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n")
PY
python3 - "$TRT/config.json" "$KEY" "$VAL_B" <<'PY'
import json, sys
path, key, raw = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.loads(open(path, encoding="utf-8").read())
try:
    cfg[key] = json.loads(raw)
except json.JSONDecodeError:
    cfg[key] = raw
open(path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n")
PY

echo "=== A/B: ${PACK_NAME} ${KEY} = ${VAL_A} (ctl) vs ${VAL_B} (trt), ${N_SEEDS}シード"
zsh tools/run_benchmark.sh "$CTL" "$N_SEEDS" ${MOCK:+$MOCK}
zsh tools/run_benchmark.sh "$TRT" "$N_SEEDS" ${MOCK:+$MOCK}

REPORTS="${FORGE_REPORTS_DIR:-reports}"
python3 tools/compare_reports.py \
  "$REPORTS/benchmark_${ARM}_ctl_${DATE}.json" \
  "$REPORTS/benchmark_${ARM}_trt_${DATE}.json" \
  --knob "$KEY" --value-a "$VAL_A" --value-b "$VAL_B" \
  --out "$REPORTS/ab_${ARM}_${DATE}.md"
