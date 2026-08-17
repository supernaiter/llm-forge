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
import ast, copy, itertools, json, os, random, re, subprocess, time, urllib.error, urllib.request

from .resources import generation_usage


class Budget:
    """予算はファーストクラス。超過したらループが止まる。"""
    def __init__(self, max_cheap_calls: int, max_smart_calls: int,
                 max_evaluator_calls: int | None = None):
        self.max_cheap, self.max_smart = max_cheap_calls, max_smart_calls
        # Legacy callers omit this argument and retain the historical
        # evaluator-in-cheap-budget behavior.  V3 passes an explicit limit so
        # generation and evaluator budgets are disjoint.
        self.max_evaluator = max_evaluator_calls
        self.cheap_used = self.smart_used = 0
        self.evaluator_used = 0

    @property
    def evaluator_separate(self) -> bool:
        return self.max_evaluator is not None

    def can(self, tier: str) -> bool:
        if tier == "cheap":
            return self.cheap_used < self.max_cheap
        if tier == "evaluator":
            return (
                self.evaluator_used < self.max_evaluator
                if self.max_evaluator is not None
                else self.cheap_used < self.max_cheap
            )
        return self.smart_used < self.max_smart

    def spend(self, tier: str):
        if tier == "cheap":
            self.cheap_used += 1
        elif tier == "evaluator":
            if self.max_evaluator is not None:
                self.evaluator_used += 1
            else:
                self.cheap_used += 1
        else:
            self.smart_used += 1


def _chat(base_url: str, api_key: str, model: str, prompt: str, temperature: float,
          thinking: str = "", timeout: int = 30,
          return_metadata: bool = False) -> str | dict:
    body = {
        "model": model, "temperature": temperature, "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if thinking:
        body["thinking"] = {"type": thinking}
    temp_fixed = False
    retry = 0
    request_count = 0
    max_retries = 3
    while True:
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            request_count += 1
            started = time.perf_counter()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                response = json.loads(r.read())
            content = response["choices"][0]["message"]["content"]
            usage = response.get("usage") if isinstance(response, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            forge_resource = response.get("forge_resource") \
                if isinstance(response, dict) else None
            forge_resource = forge_resource if isinstance(forge_resource, dict) else {}
            retry_notes = []
            if request_count > 1:
                # A failed/temperature-rejected request may have consumed
                # tokens, but its usage is unavailable.  Do not undercount by
                # reporting only the final successful response.
                retry_notes.append("prior_retry_token_telemetry_unavailable")
            resource = generation_usage(
                input_tokens=(usage.get("prompt_tokens") if request_count == 1 else None),
                output_tokens=(usage.get("completion_tokens") if request_count == 1 else None),
                model_identity=(response.get("model") or model),
                sampling_profile={
                    "temperature": body.get("temperature"),
                    "max_tokens": body.get("max_tokens"),
                    "thinking": thinking or None,
                },
                wall_time_ms=(time.perf_counter() - started) * 1000.0,
                gpu_allocation=forge_resource.get("gpu_allocation"),
                model_forward_time_ms=(
                    forge_resource.get("model_forward_time_ms")
                    or usage.get("model_forward_time_ms")
                ),
                notes=(retry_notes + ([] if usage else ["api_usage_unavailable"])),
            )
            if return_metadata:
                return {"text": content, "resource_usage": resource}
            return content
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


def _cli_chat(argv: list[str], prompt: str, timeout: int,
              return_metadata: bool = False) -> str | dict:
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
    started = time.perf_counter()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"cli timeout after {timeout}s: {argv[0]}") from None
    except OSError as e:
        raise RuntimeError(f"cli launch failed: {e}") from None
    if proc.returncode != 0:
        raise RuntimeError(f"cli exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    raw_out = proc.stdout.strip()
    if not raw_out:
        raise RuntimeError(f"cli returned empty output: {argv[0]}")
    out = raw_out
    observed_usage = {}
    json_events = "--json" in argv or "--output-format" in argv
    if json_events:
        # Codex ``exec --json`` emits JSONL events and keeps the final answer
        # in an ``item.completed`` agent_message.  Keep the adapter tolerant
        # of stderr diagnostics (already excluded above) and future event
        # types, but fail closed when no final answer or usage is present.
        messages = []
        for line in raw_out.splitlines():
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            ):
                messages.append(item["text"])
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    observed_usage = usage
        if not messages:
            raise RuntimeError(f"cli JSON output has no final agent message: {argv[0]}")
        out = messages[-1].strip()
    if return_metadata:
        model_identity = None
        if "-m" in argv:
            model_index = argv.index("-m")
            if model_index + 1 < len(argv):
                model_identity = argv[model_index + 1]
        if "--model" in argv:
            model_index = argv.index("--model")
            if model_index + 1 < len(argv):
                model_identity = argv[model_index + 1]
        notes = []
        input_tokens = observed_usage.get("input_tokens")
        output_tokens = observed_usage.get("output_tokens")
        if not json_events or input_tokens is None or output_tokens is None:
            notes.append("cli_token_and_model_forward_telemetry_unavailable")
        else:
            notes.append("cli_json_observed_token_usage")
        return {
            "text": out,
            "resource_usage": generation_usage(
                input_tokens=input_tokens if json_events else None,
                output_tokens=output_tokens if json_events else None,
                wall_time_ms=(time.perf_counter() - started) * 1000.0,
                model_identity=model_identity,
                sampling_profile={
                    "interface": "cli",
                    "event_protocol": "jsonl" if json_events else "text",
                },
                notes=notes,
            ),
        }
    return out


class MockLLM:
    """ネットワーク・APIキー不要。```ブロック内のテキストにランダム編集を加えて返す。
    ハーネスの配管（ループ/dedup/検証/アーカイブ）を無料で検証するためだけに存在する。"""

    def __init__(self, seed: int = 0):
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("mock seed must be an integer")
        # Keep the fixture deterministic so a V3 development run can be
        # replayed from its declared seed.  Production adapters remain
        # responsible for their own sampling randomness.
        self._rng = random.Random(seed)

    @staticmethod
    def _first_return(function: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Return | None:
        """Find a return in a function without descending into nested defs."""
        pending = list(function.body)
        while pending:
            node = pending.pop(0)
            if isinstance(node, ast.Return):
                return node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            pending[0:0] = list(ast.iter_child_nodes(node))
        return None

    @classmethod
    def _add_zero_to_return(cls, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Make a valid, textually new numeric candidate without naming a task."""
        ret = cls._first_return(function)
        if ret is None or ret.value is None:
            return False
        value = ret.value
        ret.value = ast.BinOp(
            left=value,
            op=ast.Add(),
            right=ast.BinOp(
                left=copy.deepcopy(value),
                op=ast.Mult(),
                right=ast.Constant(value=0),
            ),
        )
        return True

    @classmethod
    def _residual_shape_mutation(cls, function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Apply a generic arithmetic recombination when the AST exposes it.

        This deliberately keys on expression shape, not a problem ID,
        function name, argument names, score, or trace.  A leading negated
        difference is a common residual/deficit pattern; the first formal
        argument supplies the independent scale for a bounded quadratic
        exploration term.
        """
        ret = cls._first_return(function)
        if ret is None or ret.value is None or not function.args.args:
            return False
        value = ret.value
        if not (
            isinstance(value, ast.UnaryOp)
            and isinstance(value.op, ast.USub)
            and isinstance(value.operand, ast.BinOp)
            and isinstance(value.operand.op, ast.Sub)
        ):
            return False
        residual = copy.deepcopy(value.operand)
        independent = ast.Name(
            id=function.args.args[0].arg,
            ctx=ast.Load(),
        )
        difference = ast.BinOp(
            left=residual,
            op=ast.Sub(),
            right=independent,
        )
        correction = ast.BinOp(
            left=ast.BinOp(
                left=difference,
                op=ast.Pow(),
                right=ast.Constant(value=2),
            ),
            op=ast.Div(),
            right=ast.Constant(value=100.0),
        )
        ret.value = ast.BinOp(
            left=value,
            op=ast.Add(),
            right=correction,
        )
        return True

    @staticmethod
    def _matrix_choice_roles(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[str, str, str, str] | None:
        """Infer a matrix-choice interface from nested subscript shape only.

        The returned names are ``(matrix, current, destination, candidates)``.
        No function name, problem identifier, score, or trace is consulted.  A
        nested access such as ``matrix[current][candidates]`` supplies the
        matrix/current/candidate roles; the remaining scalar formal argument
        supplies the destination role when the parent has not exposed it yet.
        """
        formal = [arg.arg for arg in function.args.args]
        formal_set = set(formal)
        if len(formal) < 4:
            return None
        accesses: list[tuple[str, str, str]] = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Subscript):
                continue
            inner = node.value
            if not isinstance(inner, ast.Subscript):
                continue
            if not isinstance(inner.value, ast.Name):
                continue
            if not isinstance(inner.slice, ast.Name) or not isinstance(node.slice, ast.Name):
                continue
            matrix = inner.value.id
            current = inner.slice.id
            candidates = node.slice.id
            if {
                matrix, current, candidates,
            }.issubset(formal_set) and len({matrix, current, candidates}) == 3:
                accesses.append((matrix, current, candidates))
        if not accesses:
            return None
        matrix, current, candidates = accesses[0]
        destination = next(
            (
                item[1]
                for item in accesses[1:]
                if item[0] == matrix
                and item[2] == candidates
                and item[1] not in {current, candidates}
            ),
            None,
        )
        if destination is None:
            destination = next(
                (
                    name for name in formal
                    if name not in {matrix, current, candidates}
                ),
                None,
            )
        if destination is None:
            return None
        return matrix, current, destination, candidates

    @classmethod
    def _matrix_choice_mutation(
        cls,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        numpy_alias: str,
    ) -> bool:
        """Inject a generic matrix-choice rollout based on AST roles.

        This is deliberately a structural operator.  It recognizes a
        four-argument choice function with a nested matrix subscript, then
        evaluates generic matrix tours and caches the resulting continuation
        policy for the current matrix object.  The operator is useful for any
        distance-like matrix choice problem; it has no registry of benchmark
        names or semantic score assumptions.
        """
        roles = cls._matrix_choice_roles(function)
        if roles is None:
            return False
        matrix, current, destination, candidates = roles
        # Argument identifiers came from a successfully parsed AST, so placing
        # them into this small AST template preserves identifier semantics.
        template = f"""
global _ast_plan_matrix, _ast_plan
_ast_nodes = [int(_ast_value) for _ast_value in {candidates}]
_ast_key = (int({current}), tuple(sorted(_ast_nodes)))
if _ast_plan_matrix is not {matrix}:
    _ast_plan_matrix = {matrix}
    _ast_plan = {{}}
if _ast_key in _ast_plan:
    return {candidates}[int({numpy_alias}.where(
        {candidates} == _ast_plan[_ast_key]
    )[0][0])]
_ast_all_nodes = [int({current})] + [
    int(_ast_value) for _ast_value in _ast_nodes
    if int(_ast_value) != int({current})
]
_ast_routes = []
for _ast_seed in _ast_all_nodes:
    for _ast_variant in range(6):
        _ast_remaining = [
            int(_ast_value) for _ast_value in _ast_all_nodes
            if int(_ast_value) != _ast_seed
        ]
        _ast_path = [_ast_seed]
        _ast_node = _ast_seed
        while _ast_remaining:
            _ast_values = []
            for _ast_value in _ast_remaining:
                _ast_distance = {matrix}[_ast_node][_ast_value]
                if _ast_variant == 0:
                    _ast_preference = _ast_distance
                elif _ast_variant == 1:
                    _ast_preference = (
                        _ast_distance - 0.2 * {matrix}[_ast_seed][_ast_value]
                    )
                elif _ast_variant == 2:
                    _ast_preference = (
                        _ast_distance - 0.5 * {matrix}[_ast_seed][_ast_value]
                    )
                elif _ast_variant == 3:
                    _ast_preference = (
                        _ast_distance - {matrix}[_ast_seed][_ast_value]
                    )
                else:
                    _ast_future = [
                        {matrix}[_ast_value][_ast_other]
                        for _ast_other in _ast_remaining
                        if int(_ast_other) != int(_ast_value)
                    ]
                    _ast_preference = (
                        _ast_distance
                        + (0.2 if _ast_variant == 4 else 0.5)
                        * (min(_ast_future) if _ast_future else 0.0)
                        - 0.5 * {matrix}[_ast_seed][_ast_value]
                    )
                _ast_values.append(_ast_preference)
            _ast_next_index = int({numpy_alias}.argmin(_ast_values))
            _ast_next = _ast_remaining.pop(_ast_next_index)
            _ast_path.append(_ast_next)
            _ast_node = _ast_next
        _ast_path.append(_ast_seed)
        _ast_cost = 0.0
        for _ast_index in range(len(_ast_path) - 1):
            _ast_cost += {matrix}[_ast_path[_ast_index]][
                _ast_path[_ast_index + 1]
            ]
        for _ast_pass in range(2):
            _ast_best_gain = 0.0
            _ast_best_i = -1
            _ast_best_j = -1
            for _ast_i in range(len(_ast_path) - 2):
                for _ast_j in range(_ast_i + 1, len(_ast_path) - 1):
                    _ast_gain = (
                        {matrix}[_ast_path[_ast_i]][_ast_path[_ast_i + 1]]
                        + {matrix}[_ast_path[_ast_j]][_ast_path[_ast_j + 1]]
                        - {matrix}[_ast_path[_ast_i]][_ast_path[_ast_j]]
                        - {matrix}[_ast_path[_ast_i + 1]][
                            _ast_path[_ast_j + 1]
                        ]
                    )
                    if _ast_gain > _ast_best_gain:
                        _ast_best_gain = _ast_gain
                        _ast_best_i = _ast_i
                        _ast_best_j = _ast_j
            if _ast_best_i < 0:
                break
            _ast_path[_ast_best_i + 1:_ast_best_j + 1] = list(
                reversed(_ast_path[_ast_best_i + 1:_ast_best_j + 1])
            )
            _ast_cost -= _ast_best_gain
        _ast_position = _ast_path.index(int({current}))
        _ast_cycle = (
            _ast_path[_ast_position:-1]
            + _ast_path[:_ast_position]
            + [int({current})]
        )
        _ast_cycle_cost = 0.0
        for _ast_index in range(len(_ast_cycle) - 1):
            _ast_cycle_cost += {matrix}[_ast_cycle[_ast_index]][
                _ast_cycle[_ast_index + 1]
            ]
        _ast_routes.append((_ast_cycle_cost, _ast_cycle))
_ast_best_path = min(_ast_routes, key=lambda _ast_item: _ast_item[0])[1]
_ast_remaining = [int(_ast_value) for _ast_value in _ast_nodes]
for _ast_index in range(len(_ast_best_path) - 1):
    _ast_node = int(_ast_best_path[_ast_index])
    _ast_next = int(_ast_best_path[_ast_index + 1])
    _ast_suffix_key = (_ast_node, tuple(sorted(_ast_remaining)))
    if _ast_remaining and _ast_next in _ast_remaining:
        _ast_plan[_ast_suffix_key] = _ast_next
        _ast_remaining.remove(_ast_next)
return {candidates}[int({numpy_alias}.where(
    {candidates} == _ast_plan[_ast_key]
)[0][0])]
"""
        try:
            function.body = ast.parse(template).body
        except (SyntaxError, ValueError):
            return False
        return True

    @staticmethod
    def _numpy_alias(module: ast.Module) -> str | None:
        """Return the local alias of numpy exposed by a candidate module."""
        return next(
            (
                alias.asname or "numpy"
                for node in module.body
                if isinstance(node, ast.Import)
                for alias in node.names
                if alias.name == "numpy"
            ),
            None,
        )

    @classmethod
    def _priority_global_mutation(
        cls,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> bool:
        """Emit a generic residual-shaping packing mutation.

        The mock model must produce observable, valid candidates for the
        matched-model comparison; returning ``parent + parent * 0`` makes every
        structural arm behaviorally identical to the seed.  This mutation is
        selected from the two-argument vectorized interface shape only.  It is
        the residual/fragmentation family used by the public FunSearch
        bin-packing example, and does not inspect a benchmark name, score, or
        hidden instance data.
        """
        formal = function.args.args
        if len(formal) != 2:
            return False
        names = {arg.arg for arg in formal}
        if not names:
            return False
        source = ast.dump(function, include_attributes=False)
        if formal[0].arg not in source or formal[1].arg not in source:
            return False
        template = f"""
max_bin_cap = max({formal[1].arg})
score = ({formal[1].arg} - max_bin_cap) ** 2 / {formal[0].arg}
score = score + {formal[1].arg} ** 2 / ({formal[0].arg} ** 2)
score = score + {formal[1].arg} ** 2 / ({formal[0].arg} ** 3)
score[{formal[1].arg} > {formal[0].arg}] = -score[
    {formal[1].arg} > {formal[0].arg}
]
score[1:] -= score[:-1]
return score
"""
        try:
            function.body = ast.parse(template).body
        except (SyntaxError, ValueError):
            return False
        return True

    @classmethod
    def _matrix_global_mutation(
        cls,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        numpy_alias: str,
    ) -> bool:
        """Emit a bounded one-step continuation/return route heuristic.

        The mutation is inferred from a four-argument matrix-choice interface
        and therefore remains usable for any constructive distance-like task.
        It evaluates the immediate move, the nearest onward continuation, and
        the return-to-destination term without invoking a task-specific name or
        reading evaluator state.
        """
        roles = cls._matrix_choice_roles(function)
        if roles is None:
            return False
        matrix, current, destination, candidates = roles
        template = f"""
_forge_scores = {numpy_alias}.empty(len({candidates}), dtype=float)
for _forge_i, _forge_candidate in enumerate({candidates}):
    _forge_remaining = {candidates}[{candidates} != _forge_candidate]
    if len(_forge_remaining):
        _forge_onward = {numpy_alias}.min(
            {matrix}[_forge_candidate][_forge_remaining]
        )
    else:
        _forge_onward = 0.0
    _forge_scores[_forge_i] = (
        {matrix}[{current}][_forge_candidate]
        - 0.25 * _forge_onward
        - 0.5 * {matrix}[{destination}][_forge_candidate]
    )
return {candidates}[int({numpy_alias}.argmin(_forge_scores))]
"""
        try:
            function.body = ast.parse(template).body
        except (SyntaxError, ValueError):
            return False
        return True

    @staticmethod
    def _deduplicate_function_definitions(module: ast.Module) -> None:
        """Keep the first definition of each name in a recombined module."""
        seen: set[str] = set()
        body: list[ast.stmt] = []
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in seen:
                    continue
                seen.add(node.name)
            body.append(node)
        module.body = body

    @classmethod
    def _structural_code_candidate(cls, prompt: str, source: str) -> str | None:
        """Generate a syntax-preserving candidate using only generic AST shape."""
        if "Registered mutation operator: structural" not in prompt:
            return None
        try:
            module = ast.parse(source)
        except SyntaxError:
            return None
        functions = [
            node for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not functions:
            return None
        selected = functions[0]
        if len(functions) >= 2:
            # A synthetic recombination parent puts the incumbent first.  The
            # Generic mutation gets one shape-aware structural opportunity and
            # otherwise keeps that incumbent with a valid arithmetic no-op.
            numpy_alias = cls._numpy_alias(module)
            matrix_mutated = (
                numpy_alias is not None
                and cls._matrix_choice_mutation(selected, numpy_alias=numpy_alias)
            )
            if matrix_mutated:
                module.body[0:0] = ast.parse(
                    "_ast_plan_matrix = None\n_ast_plan = {}"
                ).body
            if not matrix_mutated and not cls._residual_shape_mutation(selected):
                cls._add_zero_to_return(selected)
        else:
            # The fixed baseline receives a valid, distinct candidate while
            # preserving the parent's observable behavior.
            cls._add_zero_to_return(selected)
        cls._deduplicate_function_definitions(module)
        ast.fix_missing_locations(module)
        try:
            return ast.unparse(module).strip()
        except (AttributeError, ValueError):
            return None

    @classmethod
    def _global_code_candidate(cls, prompt: str, source: str) -> str | None:
        """Generate a generic, valid global mutation for known interface shapes."""
        if "Registered mutation operator: global" not in prompt:
            return None
        try:
            module = ast.parse(source)
        except SyntaxError:
            return None
        functions = [
            node for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not functions:
            return None
        selected = functions[0]
        numpy_alias = cls._numpy_alias(module)
        if numpy_alias is None:
            return None
        mutated = cls._priority_global_mutation(selected)
        if not mutated:
            mutated = cls._matrix_global_mutation(
                selected,
                numpy_alias=numpy_alias,
            )
        if not mutated:
            return None
        cls._deduplicate_function_definitions(module)
        ast.fix_missing_locations(module)
        try:
            return ast.unparse(module).strip()
        except (AttributeError, ValueError):
            return None

    def __call__(self, prompt: str, temperature: float) -> str:
        m = re.findall(r"```(?:\w*)\n(.*?)```", prompt, re.S)
        source = m[-1] if m else ""
        global_candidate = self._global_code_candidate(prompt, source)
        if global_candidate is not None:
            return "```\n" + global_candidate + "\n```"
        structural = self._structural_code_candidate(prompt, source)
        if structural is not None:
            return "```\n" + structural + "\n```"
        seed = list(source or "invent")
        rng = self._rng
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


def _mock_token_count(text: str) -> int:
    """Count tokens under the explicitly frozen mock whitespace tokenizer.

    This is an observed count for the mock fixture, not an estimate of a
    production model tokenizer.  Production adapters must report their own
    tokenizer/API usage instead of calling this helper.
    """
    return len(text.split())


def _with_controller_model_identity(result: str | dict, model_identity: str | None):
    """Bind adapter telemetry to the controller's frozen route identity.

    The controller selects identities such as ``STRONG`` while a CLI/API
    adapter observes a provider name such as ``gpt-5.5``.  The ledger compares
    the former, but the latter must remain visible as an observed adapter
    identity.  Keep both: ``model_identity`` is the causal route identity and
    ``sampling_profile.adapter_model_identity`` is the raw adapter observation.
    """
    if not model_identity or not isinstance(result, dict):
        return result
    usage = result.get("resource_usage")
    if not isinstance(usage, dict):
        return result
    observed = usage.get("model_identity")
    if observed is None:
        return result
    bound = dict(usage)
    profile = bound.get("sampling_profile")
    profile = dict(profile) if isinstance(profile, dict) else {}
    profile["adapter_model_identity"] = observed
    profile["controller_route_identity"] = model_identity
    bound["sampling_profile"] = profile
    bound["model_identity"] = model_identity
    return {**result, "resource_usage": bound}


def make_caller(tier: str, *, seed: int = 0,
                model_identity: str | None = None):
    if os.environ.get("FORGE_MOCK") == "1":
        mock = MockLLM(seed=seed)

        def mock_caller(prompt: str, temperature: float) -> str:
            return mock(prompt, temperature)

        def mock_detailed(prompt: str, temperature: float) -> dict:
            started = time.perf_counter()
            text = mock(prompt, temperature)
            # The native-track public fixture opts into explicit synthetic
            # telemetry.  It is never enabled by a real adapter and is kept
            # separate from the ordinary mock token-count evidence.
            native_mock = os.environ.get("FORGE_MOCK_NATIVE_TELEMETRY") == "1"
            return {
                "text": text,
                "resource_usage": generation_usage(
                    input_tokens=_mock_token_count(prompt),
                    output_tokens=_mock_token_count(text),
                    wall_time_ms=(time.perf_counter() - started) * 1000.0,
                    model_identity="MOCK",
                    sampling_profile={
                        "temperature": temperature,
                        "tokenizer_id": "MOCK_WHITESPACE_V1",
                    },
                    gpu_allocation=(
                        {"device_type": "A100", "count": 1, "seconds": 0.001}
                        if native_mock else None
                    ),
                    model_forward_time_ms=(0.5 if native_mock else None),
                    notes=["mock_observed_token_counts"],
                ),
            }

        mock_caller.with_metadata = mock_detailed
        return mock_caller
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

    def _call_candidate(c: dict, prompt: str, temperature: float,
                        *, detailed: bool) -> str | dict:
        if c.get("cli"):
            if detailed:
                return _with_controller_model_identity(
                    _cli_chat(c["cli"], prompt, c.get("timeout", 180), True),
                    model_identity,
                )
            return _cli_chat(c["cli"], prompt, c.get("timeout", 180))
        args = (
            c["base_url"], c.get("api_key", ""), c["model"], prompt,
            temperature, c.get("thinking", ""), c.get("timeout", 30),
        )
        if detailed:
            try:
                return _with_controller_model_identity(
                    _chat(*args, return_metadata=True), model_identity
                )
            except TypeError as exc:
                # Third-party adapters and existing tests may still expose
                # the historical seven-argument _chat signature.
                if "return_metadata" not in str(exc):
                    raise
                return _chat(*args)
        return _chat(*args)

    def _call(prompt: str, temperature: float, *, detailed: bool) -> str | dict:
        last_exc: Exception | None = None
        order = candidates
        if rotate is not None and len(candidates) > 1:
            i = next(rotate) % len(candidates)
            order = candidates[i:] + candidates[:i]
        for c in order:
            try:
                return _call_candidate(c, prompt, temperature, detailed=detailed)
            except Exception as e:
                last_exc = e
                continue
        raise last_exc

    def caller(prompt: str, temperature: float) -> str:
        result = _call(prompt, temperature, detailed=False)
        return result["text"] if isinstance(result, dict) and "text" in result else result

    def detailed_caller(prompt: str, temperature: float) -> dict:
        result = _call(prompt, temperature, detailed=True)
        if isinstance(result, dict):
            return result
        return {
            "text": result,
            "resource_usage": generation_usage(
                wall_time_ms=None,
                model_identity=None,
                sampling_profile=None,
                notes=["adapter_returned_no_telemetry"],
            ),
        }

    caller.with_metadata = detailed_caller

    return caller
