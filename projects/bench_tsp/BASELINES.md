# bench_tsp 基準値

TSP構築ヒューリスティック。node 0 から出発し、1ステップ1ノードずつ巡回路を構築する。
局所探索（2-opt等）は使えない。この設定で総巡回距離の最小化が目的。
スコア = **-(平均巡回距離)**（大きいほど良い）。

## データ

LLM4AD の `tsp_construct` と同一。`np.random.seed(2024)` を一度だけ設定し、
単位正方形の一様乱数座標を連続ストリームとして生成する。

| スケール | インスタンス数 | 都市数 | 用途 |
|---|---|---|---|
| full | 16 | 50 | 実走行 |
| mock | 4 | 20 | `--mock` 配管確認のみ |

## ローカル実測値（`baselines.json` と同値。tests/test_bench_packs.py が毎回再計算して照合）

### full スケール（16×50）

| 手法 | 平均巡回距離 | score |
|---|---|---|
| **貪欲最近傍**（シード） | 6.823969 | -6.823969 |
| 出発点距離ブレンド（シード） | 6.553317 | -6.553317 |
| （参考）貪欲最近傍 + 2-opt | 5.989776 | — |

### mock スケール（4×20）

| 手法 | 平均巡回距離 | score |
|---|---|---|
| 貪欲最近傍 | 4.444361 | -4.444361 |
| 出発点距離ブレンド | 4.183527 | -4.183527 |
| （参考）貪欲最近傍 + 2-opt | 3.909861 | — |

「+2-opt」は構築後に局所探索をかけた値で、この問題設定では**到達不能**。
構築だけでどこまでこの値に近づけるかを見るための上限側の目安として置いてある。

## 文献値について

**このパックには文献値を記録していない。**
EoH/AEL論文のTSP結果は論文側が独自に引いたインスタンスに対する値であり、
本パックが使う seed=2024 の引きとは別物なので、絶対値を直接比較できない。
文献値を書き込むなら、まず論文と同一のインスタンス集合を再現してからにすること。

参照: Fei Liu et al., "Algorithm Evolution using Large Language Model," arXiv:2311.15249 (2023);
Fei Liu et al., "Evolution of Heuristics," ICML 2024.

## 出典

Fei Liu, Rui Zhang, Zhuoliang Xie, Rui Sun, Kai Li, Xi Lin, Zhenkun Wang, Zhichao Lu,
and Qingfu Zhang, "LLM4AD: A Platform for Algorithm Design with Large Language Model,"
arXiv preprint arXiv:2412.17287 (2024). https://github.com/Optima-CityU/llm4ad

LLM4AD は研究目的での利用が許諾されている。本パックを用いた成果物には上記を引用すること。
