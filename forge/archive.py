"""解アーカイブ。JSONL追記のみ＝クラッシュ耐性・再開可能・gitがstateのsource of truth。"""
from __future__ import annotations
import json, os


class Archive:
    """生きているtop-Kは、次世代の親プールそのものである。

    `max_per_score` は同一スコアが占める席を上限で抑える。**既定は0(無効)。**

    2026-07-25 に既定3でA/B実測したところ3シード全敗した(平均 -2058.07 → -2088.80)。
    スコアの種類は狙い通り増えた(4〜10 → 17〜27)のに成績は落ちた。原因は質の下限が
    無かったこと: 優秀な同点複製を追い出した席に、-5000.0(全品目が別bin)級の
    壊滅的候補が座り、実験群の親プールの4〜5割を占めた。sample_parentsは
    プール全体からランダム1体を引くので、LLMに失敗作が手本として提示されていた。
    「スコアの多様性」は「アイデアの多様性」ではない。プールの構成に手を入れる場合は
    必ず質の下限とセットにすること。親の見せ方だけを多様化するなら
    operators.sample_parents の `parent_score_spread` を使え(副作用が小さい)。

    捨てるのは親プールとしての席だけで、JSONLには全候補が残る。
    """

    def __init__(self, path: str, capacity: int = 50, max_per_score: int = 0,
                 island: int = 0):
        self.path, self.capacity = path, capacity
        self.max_per_score = max_per_score
        self.island = island
        self.items: list[dict] = []
        if os.path.exists(path):
            good_bytes = 0
            truncate_to = None
            with open(path, "rb") as f:
                for raw in f:
                    if not raw.endswith(b"\n"):
                        # 書きかけ半端バイト(クラッシュ時の未完了write)。確定済み行は不可侵。
                        truncate_to = good_bytes
                        break
                    line = raw.decode("utf-8").strip()
                    if line:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            break
                        # 島モデルでは1本のJSONLを全島で共有する(追記のみ・再開可能という
                        # 性質を保つため)。読み込み時に自分の島の行だけ拾う。
                        if item.get("island", 0) != self.island:
                            continue
                        # 島の作り直し(reset_to)は追記型ログでは「以前の住民を消す」
                        # 操作にあたる。境界行を置き、再読込時はそこから後だけを拾う。
                        # これが無いと、再開した瞬間に移住前の集団が復活して
                        # 島モデルが無効化される。
                        if item.get("island_reset"):
                            self.items = []
                            continue
                        self.items.append(item)
                    good_bytes += len(raw)
            if truncate_to is not None:
                with open(path, "r+b") as f:
                    f.truncate(truncate_to)
            self._trim()

    def add(self, cand: dict):
        cand.setdefault("island", self.island)
        with open(self.path, "a") as f:
            f.write(json.dumps(cand, ensure_ascii=False) + "\n")
        self.items.append(cand)
        self._trim()

    def reset_to(self, cand: dict):
        """島を1体だけの状態に戻す(移住・リセット用)。JSONLの履歴は消さない。

        境界行(island_reset)を追記してから新しい住民を書く。再読込時はこの行で
        それまでの住民を捨てるので、中断・再開しても移住後の状態が復元される。
        """
        with open(self.path, "a") as f:
            f.write(json.dumps({"island": self.island, "island_reset": True,
                                "gen": cand.get("gen", 0)}, ensure_ascii=False) + "\n")
        self.items = []
        self.add(dict(cand, island=self.island))

    def _trim(self):
        self.items.sort(key=lambda c: (-c["score"], len(c["text"])))
        if self.max_per_score:
            kept: list[dict] = []
            per_score: dict[float, int] = {}
            for cand in self.items:
                score = cand["score"]
                if per_score.get(score, 0) >= self.max_per_score:
                    continue  # 同じ挙動の複製。親プールの席は渡さない(JSONLには残っている)
                per_score[score] = per_score.get(score, 0) + 1
                kept.append(cand)
            self.items = kept
        self.items = self.items[: self.capacity]

    @property
    def best(self):
        return self.items[0] if self.items else None

    @property
    def distinct_scores(self) -> int:
        """親プール内の異なるスコアの数。探索が潰れていないかの主計器。"""
        return len({c["score"] for c in self.items})
