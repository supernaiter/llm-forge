# bench_obp 基準値

Online Bin Packing。品目が1つずつ到着し、その場で容量100のbinへ入れる。使用bin数の最小化が目的。
スコア = **-(平均使用bin数)**（大きいほど良い）。

## データ

LLM4AD の `online_bin_packing` と同一。`np.random.seed(2024)` を一度だけ設定し、
`round(clip(weibull(3) * 45, 1, 100))` を連続ストリームとして生成する。

| スケール | インスタンス数 | 品目数 | 用途 |
|---|---|---|---|
| full | 5 | 5000 | 実走行・論文値との比較 |
| mock | 2 | 500 | `--mock` 配管確認のみ（数値は論文値と比較不可） |

## ローカル実測値（`baselines.json` と同値。tests/test_bench_packs.py が毎回再計算して照合）

### full スケール

| 手法 | 平均bin数 | score | L1下界超過 |
|---|---|---|---|
| L1下界 `ceil(Σitems/100)` | 2011.4 | — | 0% |
| **best fit**（シード） | 2091.8 | -2091.8 | 3.9972% |
| first fit（シード） | 2099.4 | -2099.4 | 4.3751% |

### mock スケール

| 手法 | 平均bin数 | score | L1下界超過 |
|---|---|---|---|
| L1下界 | 202.5 | — | 0% |
| best fit | 214.0 | -214.0 | 5.6790% |
| first fit | 216.0 | -216.0 | 6.6667% |

## 文献参照値（未検証・方向感の目安）

FunSearch（Nature 625, 2024）が同じ Weibull 5k 設定で報告した L1下界超過率。
GPT-3.5/GPT-4級モデルでの数値であり、こちらで再現したものではない。

| 手法 | L1下界超過 |
|---|---|
| FunSearch発見ヒューリスティック | 0.68% |
| best fit | 4.02% |
| first fit | 4.42% |

ローカル実測の best fit 3.9972% / first fit 4.3751% は上記の報告値とほぼ一致しており、
データ生成と評価手順が論文設定を再現できていることの傍証になる。

## 出典

Fei Liu, Rui Zhang, Zhuoliang Xie, Rui Sun, Kai Li, Xi Lin, Zhenkun Wang, Zhichao Lu,
and Qingfu Zhang, "LLM4AD: A Platform for Algorithm Design with Large Language Model,"
arXiv preprint arXiv:2412.17287 (2024). https://github.com/Optima-CityU/llm4ad

LLM4AD は研究目的での利用が許諾されている。本パックを用いた成果物には上記を引用すること。
