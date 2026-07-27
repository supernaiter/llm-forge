"""LLM抽象化層。cheap（変異オペレータ用）と smart（最終審査用）の2層のみ。
OpenAI互換エンドポイントなら何でも刺さる（各種OSSモデル、Anthropic互換proxy等）。
環境変数:
  FORGE_CHEAP_BASE_URL / FORGE_CHEAP_API_KEY / FORGE_CHEAP_MODEL
  FORGE_SMART_BASE_URL / FORGE_SMART_API_KEY / FORGE_SMART_MODEL
  FORGE_{CHEAP,SMART}_THINKING=disabled でreasoningモデルの思考ブロックを抑制
    （某reasoningモデル実測: 有効時は平均47.7秒/40%が```ブロック抽出失敗、
    disabled指定で平均1.1秒/失敗率0%に改善。他社互換エンドポイントでは未指定=省略）
  FORGE_{CHEAP,SMART}_FALLBACKS=JSON配列 で複数エンドポイントを優先順に機械的に総当たり
    （例: 無料枠の429/接続断で次候補へ即切替。各要素は{base_url,api_key,model,thinking,timeout}。
    指定時はFORGE_{CHEAP,SMART}_{BASE_URL,API_KEY,MODEL,THINKING,TIMEOUT}より優先）
  FORGE_{CHEAP,SMART}_TIMEOUT=秒数（既定30）でHTTPタイムアウトを調整
    （既定120秒だと外れ値1件がworkers並列全体を長時間ブロックする実測あり。
    正常系は実測6〜22秒で完了するため30秒で十分カバーしつつ早めに次候補へ切替できる）
  FORGE_{CHEAP,SMART}_POOL=JSON配列 で呼び出しごとに別のモデルへ散らす（多様性オペレータ）
    （FALLBACKSが「失敗時の代替」なのに対し、POOLは毎回起点を回して負荷と癖を分散する。
    温度ジッタ・プロンプト摂動・親サンプリング・SSoTはいずれも同じモデルの中で散らす
    手法なので、モデル族の書き癖という相関だけは残る。異なるモデルを混ぜるとそこを切れる。
    要素の形はFALLBACKSと同じ。指定時は他のFORGE_{P}_*より優先）
  FORGE_{CHEAP,SMART}_CLI=JSON配列 でサブスク契約のCLIをサブプロセス呼び出しする
    （Claude Max / ChatGPT Pro の枠は従量課金APIキーでは通らずCLI経由のみ。
    要素 "{prompt}" がプロンプト本文に置換される。指定時は他のFORGE_{P}_*より優先。
    例: FORGE_SMART_CLI='["claude","-p","--model","sonnet","{prompt}"]'
        FORGE_SMART_CLI='["codex","exec","--skip-git-repo-check","-m","gpt-5.5","{prompt}"]'
    smart層は1走行数回しか呼ばないのでHTTPより遅くても契約枠で賄えるほうが得。
    cheap層(1走行480回)には使うな — CLI起動コストが探索回数を直撃する）
  FORGE_MOCK=1 でネットワーク不要のMockLLM（ハーネス自体の検証用）
"""
from __future__ import annotations
import itertools, json, os, random, re, subprocess, time, urllib.error, urllib.request


class Budget:
    """予算はファーストクラス。超過したらループが止まる。"""
    def __init__(self, max_cheap_calls: int, max_smart_calls: int):
        self.max_cheap, self.max_smart = max_cheap_calls, max_smart_calls
        self.cheap_used = self.smart_used = 0

    def can(self, tier: str) -> bool:
        return (self.cheap_used < self.max_cheap) if tier == "cheap" else (self.smart_used < self.max_smart)

    def spend(self, tier: str):
        if tier == "cheap": self.cheap_used += 1
        else: self.smart_used += 1


def _chat(base_url: str, api_key: str, model: str, prompt: str, temperature: float,
          thinking: str = "", timeout: int = 30) -> str:
    body = {
        "model": model, "temperature": temperature, "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if thinking:
        body["thinking"] = {"type": thinking}
    temp_fixed = False
    retry = 0
    max_retries = 3
    while True:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            # 一部モデルはtemperatureを拒否する(Sonnet 5等)。外して1回だけ即時再送。
            if not temp_fixed and e.code == 400 and "temperature" in msg and "temperature" in body:
                body.pop("temperature")
                temp_fixed = True
                continue
            # 一過性エラー(429/5xx)は指数バックオフを挟んで再送。温度特例とは別枠で数える。
            if e.code in (429, 500, 502, 503) and retry < max_retries:
                retry += 1
                time.sleep(2 ** retry)
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg[:200]}") from None
        except (urllib.error.URLError, OSError) as e:
            # r.read()中のread timeoutは生のTimeoutError(OSErrorのサブクラスだが
            # URLErrorのサブクラスではない)で漏れてくる。ここで拾わないとリトライも
            # フォールバックも効かず、ハングしたように見える例外がそのまま伝播する。
            if retry < max_retries:
                retry += 1
                time.sleep(2 ** retry)
                continue
            raise RuntimeError(f"connection error: {e}") from None


def _cli_chat(argv: list[str], prompt: str, timeout: int) -> str:
    """サブスク契約のCLI(claude / codex)をサブプロセスで呼び、標準出力を応答として返す。

    Claude Max や ChatGPT Pro の枠は従量課金APIキーでは使えず、CLI経由でしか通らない。
    smart層は1走行数回しか呼ばないので、HTTPより遅くても契約枠で賄えるほうが得になる
    (2026-07-27実測: `claude -p` 11秒 / `codex exec -m gpt-5.5` 17秒。
    どちらもbench_obpの上位解に対して的確な変異方向を返した)。

    argvの要素 "{prompt}" はプロンプト本文に置換する。含まれない場合は末尾に足す。
    温度は渡さない(CLIに一般的な指定口が無いため)。smart層は温度0.3固定運用でよい。
    """
    argv = [prompt if a == "{prompt}" else a for a in argv]
    if not any(prompt == a for a in argv):
        argv = argv + [prompt]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"cli timeout after {timeout}s: {argv[0]}") from None
    except OSError as e:
        raise RuntimeError(f"cli launch failed: {e}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"cli exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"cli returned empty output: {argv[0]}")
    return out


class MockLLM:
    """ネットワーク・APIキー不要。```ブロック内のテキストにランダム編集を加えて返す。
    ハーネスの配管（ループ/dedup/検証/アーカイブ）を無料で検証するためだけに存在する。"""
    def __call__(self, prompt: str, temperature: float) -> str:
        m = re.findall(r"```(?:\w*)\n(.*?)```", prompt, re.S)
        seed = list(m[-1] if m else "invent")
        rng = random.Random()
        for _ in range(max(1, int(temperature * 4))):
            op = rng.choice("idsr")
            i = rng.randrange(len(seed) + (op == "i"))
            if op == "i": seed.insert(i, rng.choice("abcdefghijklmnopqrstuvwxyz \n"))
            elif op == "d" and len(seed) > 1: seed.pop(min(i, len(seed) - 1))
            elif op == "s" and seed: seed[min(i, len(seed) - 1)] = rng.choice("abcdefghijklmnopqrstuvwxyz ")
            elif op == "r" and len(seed) > 3:
                j = rng.randrange(len(seed)); i2 = min(i, len(seed) - 1)
                seed[i2], seed[j] = seed[j], seed[i2]
        return "```\n" + "".join(seed) + "\n```"


def make_caller(tier: str):
    if os.environ.get("FORGE_MOCK") == "1":
        return MockLLM()
    P = tier.upper()
    pool = os.environ.get(f"FORGE_{P}_POOL")
    fallbacks = os.environ.get(f"FORGE_{P}_FALLBACKS")
    cli = os.environ.get(f"FORGE_{P}_CLI")
    if pool:
        # 例: FORGE_CHEAP_POOL='[{"base_url":"http://localhost:8021/v1","model":"qwen25c-1.5b"},
        #                        {"base_url":"http://localhost:8023/v1","model":"qwen3-4b"}]'
        candidates = json.loads(pool)
    elif cli:
        # サブスク契約のCLI経由。JSON配列(argv)で渡す。要素 "{prompt}" が本文に置換される。
        # 例: FORGE_SMART_CLI='["claude","-p","--model","sonnet","{prompt}"]'
        #     FORGE_SMART_CLI='["codex","exec","--skip-git-repo-check","-m","gpt-5.5","{prompt}"]'
        candidates = [{"cli": json.loads(cli),
                       "timeout": int(os.environ.get(f"FORGE_{P}_TIMEOUT", "180"))}]
    elif fallbacks:
        candidates = json.loads(fallbacks)
    else:
        base = os.environ.get(f"FORGE_{P}_BASE_URL")
        model = os.environ.get(f"FORGE_{P}_MODEL")
        if not (base and model):
            raise SystemExit(
                f"FORGE_{P}_BASE_URL / FORGE_{P}_MODEL または FORGE_{P}_FALLBACKS を設定するか"
                " FORGE_MOCK=1 で実行してください"
            )
        candidates = [{
            "base_url": base,
            "api_key": os.environ.get(f"FORGE_{P}_API_KEY", ""),
            "model": model,
            "thinking": os.environ.get(f"FORGE_{P}_THINKING", ""),
            "timeout": int(os.environ.get(f"FORGE_{P}_TIMEOUT", "30")),
        }]

    # POOLは「呼び出しごとに別のモデルへ散らす」ための多様性オペレータ。
    # FALLBACKSが失敗時の代替(先頭から順に試す)なのに対し、こちらは毎回起点を回す。
    # 温度ジッタ・プロンプト摂動・親サンプリング・SSoTはいずれも同じモデルの中で
    # 散らす手法なので、モデル族の書き癖という相関は残ったままだった。
    # 異なるモデルを混ぜると、その相関自体を切れる(実測: 同一プロンプト32件に対する
    # 挙動の種類は Qwen2.5-Coder-1.5B が9、Qwen3-4B が7、Qwen3-Coder-30B が8で、
    # モデルごとに寄る先が違う)。
    rotate = itertools.count() if pool else None

    def caller(prompt: str, temperature: float) -> str:
        last_exc: Exception | None = None
        order = candidates
        if rotate is not None and len(candidates) > 1:
            i = next(rotate) % len(candidates)
            order = candidates[i:] + candidates[:i]
        for c in order:
            try:
                if c.get("cli"):
                    return _cli_chat(c["cli"], prompt, c.get("timeout", 180))
                return _chat(c["base_url"], c.get("api_key", ""), c["model"], prompt,
                             temperature, c.get("thinking", ""), c.get("timeout", 30))
            except Exception as e:
                last_exc = e
                continue
        raise last_exc

    return caller
