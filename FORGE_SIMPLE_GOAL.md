# Forge — Simple Performance Goal

## Goal

同じmock model、同じseed、同じ4-attempt予算で、
`TRANSFERABLE_COMPUTE_AWARE_CONTROLLER_V1` が `FIXED_DEV_BEST` より
実際に良い探索結果を出す。

## Scope

- 問題: `projects/stringmax`
- action: `protocol/controller_development_actions.json` の2 action
- 比較対象: `FIXED_DEV_BEST`
- seed: `0, 1, 2`
- attempt cap: `4`
- model: mock only
- 主指標: `auc_by_generation`
- 副指標: `best_score`

## Done criteria

次のすべてを満たしたら、この性能目標を完了とする。

1. controllerと`FIXED_DEV_BEST`の全runが4 attemptsを記録する。
2. controllerの平均`auc_by_generation`が`FIXED_DEV_BEST`より0.25以上高い。
3. controllerの`best_score`が3 seed中2 seed以上で`FIXED_DEV_BEST`以上になる。
4. replayのdecision hashとresult recomputation hashの不一致が0件である。
5. 同じコマンドを再実行して、同じ指標とselected action列を再現できる。

基準を上回れない場合は失敗として記録し、閾値やseedを緩めない。

## Non-goals

- `RESEARCH_V3_TERMINATION_CRITERION.md` の変更
- hidden holdout、外部baseline、本番verdictの実行
- mock結果を本番研究の優位性として主張すること
- 新しい監査条件や閾値を追加すること

## Reproduction command

```bash
FORGE_MOCK=1 uv run --system-certs --with numpy \
  python tools/run_controller_development.py \
  --problem stringmax=projects/stringmax \
  --actions protocol/controller_development_actions.json \
  --out <output-dir> --generations 3 --max-attempts 4 \
  --seed 0 --seed 1 --seed 2
```

## Current status

2026-08-14に達成。canonical mock matrix（`stringmax`、seed `0,1,2`、
attempt cap `4`）で、primary controller は全 seed で
`SMALL/1 → STRONG/2 → SMALL/1`を選択した。

- primary `auc_by_generation`: `8.3333, 8.3333, 8.6667`、平均 `8.4444`
- `FIXED_DEV_BEST`: `8.0, 7.5, 8.0`、平均 `7.8333`
- 平均差: `+0.6111`（基準 `+0.25`以上）
- `best_score` は 3/3 seed で baseline 以上（全て `9.0`対`8.0`）
- 全 12 policy replay が 4 attempts、独立 replay hash mismatch は `0`
- 同一コマンドの再実行で AUC、best score、attempt 数、selected action 列、
  decision hash が一致した。

再実行ごとに変わる `wall_secs` は観測 telemetry であり、decision identity と
性能判定からは除外している。
