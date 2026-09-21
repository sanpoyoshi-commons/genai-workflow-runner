"""Runner — フラットな step リストを解釈し各ノードを実行する。

実行モデル：
- step は宣言順に進む。`branch` は `when` を評価して `then`/`else` の id へジャンプ。
- 通常 step は任意の `next = "<id>"` で明示ジャンプ可（無ければ次の step へフォールスルー）。
- `foreach` はリスト参照を反復し、`body`（step id 列）を各要素について実行し、
  `collect` を `out` へ集約する。body の step は直線走査から除外（foreach 経由でのみ実行）。

エラー契約（fail-fast・完全ステートレス）：
- on_error 既定 `fail`：その step で停止しフロー全体失敗（RunnerError）。
  Envelope は呼び出し側が破棄する。
- `skip`：当該 step を飛ばす。`default`：step の `default` 値を out に書いて続行。
- 中間 Envelope はプロセスメモリ内のみ。永続化・resume はしない。

dry_run：ノードを実行せずグラフを走査し、静的・データフロー・型伝播の検証レポートを返す。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from gwr import expr
from gwr.datatypes import TypeLevel
from gwr.envelope import Envelope
from gwr.nodes.base import DataError, Node, NodeContext, NodeError
from gwr.nodes.file_write import writes_unsanitized_spreadsheet

CONTROL_TYPES = frozenset({"branch", "foreach"})

# then/else/next がこの予約 id を指すとフローを正常終了する（フラット flow の終端プリミティブ）。
END = "__end__"

# foreach の反復上限（無制限消費＝DoS 防止）。seq は `$.` 参照で入力由来になり得るため縛る。
# 既定 1000・`GWR_MAX_FOREACH_ITEMS` で可変（file.load の行数上限と同方針・fail-closed）。
DEFAULT_MAX_FOREACH_ITEMS = 1000


class RunnerError(Exception):
    """フロー実行の停止（未捕捉エラー → 呼び出し側で源内OSS の ERROR へ写像）。

    `reason` は**大文字スネークの理由コードのみ**を入れる。詳細は `detail` へ分ける。
    reason に `f"CODE: {詳細}"` と詰めると呼び出し側が `reason == "CODE"` で判定できず、
    日本語の文章を入れると機械可読な理由コード体系から外れるため
    （tests/test_user_messages.py が両方を落とす）。

    `detail` は例外メッセージには載せるが**応答には載せない**。ノード由来の詳細には
    データが混ざりうるため（値フリーのログ／応答方針を保つ）。
    """

    def __init__(
        self,
        reason: str,
        step_id: str = "",
        coordinates: dict[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(f"[{step_id}] {reason}{f': {detail}' if detail else ''}")
        self.reason = reason
        self.step_id = step_id
        self.coordinates = coordinates
        self.detail = detail


# --- 検証レポート --------------------------------------------------------
IssueCategory = Literal["static", "dataflow", "type"]


@dataclass(frozen=True)
class Issue:
    category: IssueCategory
    step_id: str
    reason: str
    detail: str = ""


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, category: IssueCategory, step_id: str, reason: str, detail: str = "") -> None:
        self.issues.append(Issue(category, step_id, reason, detail))

    def by_category(self, category: IssueCategory) -> list[Issue]:
        return [i for i in self.issues if i.category == category]


# --- 入力参照の解決 ------------------------------------------------------
def _is_reference(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("$.") or value == "$")


def _resolve_input(value: Any, env: Envelope) -> Any:
    """`$.` 始まりは参照解決、それ以外はリテラルとして扱う。"""
    if _is_reference(value):
        return expr.resolve(value, env)
    return value


def _slot_path(reference: str) -> str:
    """参照文字列から書込/読取対象のスロットパス（"$." 剥がし）を返す。"""
    return reference[2:] if reference.startswith("$.") else reference


def _root_slot(path: str) -> str:
    """型/データフロー追跡のスロットキー（先頭 2 セグメント、例 "tables.summary"）。

    値の内部へのさらに深いパス（".data" など）はスロット単位に丸める。
    """
    head = path.split("[", 1)[0]
    parts = [p for p in head.split(".") if p]
    return ".".join(parts[:2])


# --- Runner --------------------------------------------------------------
class Runner:
    def __init__(
        self, registry: dict[str, Node], max_foreach_items: int | None = None
    ) -> None:
        self.registry = registry
        self._steps_cache: list[dict[str, Any]] = []
        self.max_foreach_items = (
            max_foreach_items
            if max_foreach_items is not None
            else int(os.environ.get("GWR_MAX_FOREACH_ITEMS", str(DEFAULT_MAX_FOREACH_ITEMS)))
        )

    # ===== 実行 =====
    def run(
        self,
        steps: list[dict[str, Any]],
        envelope: Envelope,
        ctx: NodeContext | None = None,
    ) -> Envelope:
        ctx = ctx or NodeContext()
        if ctx.usage_sink is None:
            ctx.usage_sink = envelope.add_usage
        self._steps_cache = steps
        id_to_index = _index_steps(steps)
        owned = _owned_body_ids(steps)

        pc = _next_unowned(steps, owned, 0)
        guard = 0
        max_iters = len(steps) * 1000 + 1000  # 無限ループ保険
        while pc is not None and pc < len(steps):
            guard += 1
            if guard > max_iters:
                raise RunnerError(
                    "STEP_LIMIT_EXCEEDED", detail=f"guard={guard} max_iters={max_iters}"
                )
            step = steps[pc]
            stype = step.get("type")

            if stype == "branch":
                target_id = self._eval_branch(step, envelope)
                if target_id == END:
                    break
                pc = id_to_index[target_id]
                continue

            if stype == "foreach":
                self._run_foreach(step, envelope, ctx, id_to_index, owned)
                pc = self._after(step, steps, owned, pc, id_to_index)
                continue

            self._run_node(step, envelope, ctx)
            pc = self._after(step, steps, owned, pc, id_to_index)

        return envelope

    def _after(
        self,
        step: dict[str, Any],
        steps: list[dict[str, Any]],
        owned: set[str],
        pc: int,
        id_to_index: dict[str, int],
    ) -> int | None:
        nxt = step.get("next")
        if nxt == END:
            return None
        if nxt is not None:
            return id_to_index[nxt]
        return _next_unowned(steps, owned, pc + 1)

    def _eval_branch(self, step: dict[str, Any], env: Envelope) -> str:
        when = step["when"]
        try:
            taken = expr.evaluate(when, env)
        except expr.ExprError as e:
            raise RunnerError(
                "BRANCH_EVAL_FAILED", step.get("id", ""), detail=str(e)
            ) from e
        return step["then"] if taken else step["else"]

    def _run_node(self, step: dict[str, Any], env: Envelope, ctx: NodeContext) -> None:
        step_id = step.get("id", "")
        node = self.registry.get(step.get("type", ""))
        if node is None:
            raise RunnerError(
                "UNKNOWN_NODE_TYPE", step_id, detail=repr(step.get("type"))
            )
        inputs = {name: _resolve_input(ref, env) for name, ref in step.get("in", {}).items()}
        on_error = step.get("on_error", "fail")
        try:
            outputs = node.run(inputs, step, ctx)
        except DataError as e:
            if on_error == "skip":
                return
            if on_error == "default":
                self._write_outputs(
                    step, {n: copy.deepcopy(step.get("default")) for n in _out_names(step)}, env
                )
                return
            raise RunnerError(e.reason, step_id, e.coordinates(), detail=e.message) from e
        except NodeError as e:
            if on_error == "skip":
                return
            if on_error == "default":
                self._write_outputs(
                    step, {n: copy.deepcopy(step.get("default")) for n in _out_names(step)}, env
                )
                return
            raise RunnerError(e.reason, step_id, detail=e.message) from e
        self._write_outputs(step, outputs, env)

    def _write_outputs(
        self, step: dict[str, Any], outputs: dict[str, Any], env: Envelope
    ) -> None:
        out_decl = step.get("out")
        if out_decl is None:
            return
        if isinstance(out_decl, str):
            # 単一 out。ノードの主要出力（"out" or 唯一のキー）を書く。
            value = outputs.get("out")
            if value is None and len(outputs) == 1:
                value = next(iter(outputs.values()))
            env.set(_slot_path(out_decl), value)
        elif isinstance(out_decl, dict):
            for out_name, path in out_decl.items():
                env.set(_slot_path(path), outputs.get(out_name))

    def _run_foreach(
        self,
        step: dict[str, Any],
        env: Envelope,
        ctx: NodeContext,
        id_to_index: dict[str, int],
        owned: set[str],
    ) -> None:
        seq = _resolve_input(step["foreach"], env)
        if seq is None:
            seq = []
        if not isinstance(seq, list):
            raise RunnerError("FOREACH_NOT_LIST", step.get("id", ""))
        if len(seq) > self.max_foreach_items:
            raise RunnerError(
                "FOREACH_LIMIT_EXCEEDED",
                step.get("id", ""),
                detail=f"{len(seq)} > {self.max_foreach_items}",
            )
        as_name = step.get("as", "item")
        body_ids = step.get("body", [])
        collect_ref = step.get("collect")
        collected: list[Any] = []

        for item in seq:
            # item は seq（Envelope 内の実体）への生参照。set は参照保存のため、別スロット
            # vars.<as> がそれをエイリアスしないよう明示コピーする。
            env.set(f"vars.{as_name}", copy.deepcopy(item))
            for sid in body_ids:
                bstep = self._steps_cache[id_to_index[sid]]
                self._run_node(bstep, env, ctx)
            if collect_ref is not None:
                collected.append(_resolve_input(collect_ref, env))

        out_decl = step.get("out")
        if out_decl is not None and isinstance(out_decl, str):
            # collected[-1] は collect 元 scratch をエイリアスし得るため独立集約として保存。
            env.set(_slot_path(out_decl), copy.deepcopy(collected))

    # ===== dry-run（検証） =====
    def dry_run(
        self,
        steps: list[dict[str, Any]],
        initial_slots: dict[str, TypeLevel] | None = None,
        output_refs: list[str] | None = None,
    ) -> ValidationReport:
        report = ValidationReport()
        self._static_checks(steps, report)
        if report.by_category("static"):
            # 構造が壊れている場合は後段（データフロー/型）を回さず fail-closed。
            return report
        written = self._dataflow_and_type_checks(steps, initial_slots or {}, report)
        for ref in output_refs or []:
            root = _root_slot(_slot_path(ref))
            if root not in written:
                report.add("dataflow", "outputs", "OUTPUT_READ_BEFORE_WRITE", root)
        return report

    def _static_checks(self, steps: list[dict[str, Any]], report: ValidationReport) -> None:
        seen: set[str] = set()
        ids = set()
        for step in steps:
            sid = step.get("id")
            if not sid:
                report.add("static", "", "MISSING_ID")
                continue
            ids.add(sid)
        for step in steps:
            sid = step.get("id", "")
            if sid in seen:
                report.add("static", sid, "DUPLICATE_ID")
            seen.add(sid)
            stype = step.get("type")
            if not stype:
                report.add("static", sid, "MISSING_TYPE")
                continue
            if stype == "branch":
                # 理由コードは report.add の引数にリテラルで書く（docs/cli.md との
                # 突合テストが AST で全数を拾えるように。tests/test_reason_codes.py）。
                if "when" not in step:
                    report.add("static", sid, "BRANCH_MISSING_WHEN")
                if "then" not in step:
                    report.add("static", sid, "BRANCH_MISSING_THEN")
                if "else" not in step:
                    report.add("static", sid, "BRANCH_MISSING_ELSE")
                if "when" in step:
                    try:
                        expr.compile_condition(step["when"])
                    except expr.ExprError as e:
                        report.add("static", sid, "BRANCH_WHEN_UNCOMPILABLE", str(e))
                then_tgt = step.get("then")
                if then_tgt and then_tgt != END and then_tgt not in ids:
                    report.add("static", sid, "BRANCH_THEN_UNRESOLVED", then_tgt)
                else_tgt = step.get("else")
                if else_tgt and else_tgt != END and else_tgt not in ids:
                    report.add("static", sid, "BRANCH_ELSE_UNRESOLVED", else_tgt)
            elif stype == "foreach":
                if "foreach" not in step:
                    report.add("static", sid, "FOREACH_MISSING_REF")
                for bid in step.get("body", []):
                    if bid not in ids:
                        report.add("static", sid, "FOREACH_BODY_UNRESOLVED", bid)
            else:
                if stype not in self.registry:
                    report.add("static", sid, "UNKNOWN_NODE_TYPE", str(stype))
                elif writes_unsanitized_spreadsheet(step):
                    # format = "text" は無害化しない。表計算ソフトが開く名前との
                    # 組み合わせを fail-closed で落とす（数式インジェクション）。
                    report.add(
                        "static", sid, "TEXT_FORMAT_SPREADSHEET_NAME", str(step.get("name", ""))
                    )
            # next の到達性（END 予約 id は常に有効）
            nxt = step.get("next")
            if nxt and nxt != END and nxt not in ids:
                report.add("static", sid, "NEXT_UNRESOLVED", nxt)
            # in 参照の JMESPath 妥当性
            for ref in step.get("in", {}).values():
                if _is_reference(ref):
                    try:
                        expr.compile_reference(ref)
                    except expr.ExprError as e:
                        report.add("static", sid, "BAD_REFERENCE", str(e))

    def _dataflow_and_type_checks(
        self,
        steps: list[dict[str, Any]],
        initial_slots: dict[str, TypeLevel],
        report: ValidationReport,
    ) -> set[str]:
        slot_types: dict[str, TypeLevel | None] = dict(initial_slots)
        written: set[str] = set(initial_slots.keys())

        for step in steps:
            sid = step.get("id", "")
            stype = step.get("type")

            if stype == "branch":
                self._check_when_reads(step, written, report)
                continue
            if stype == "foreach":
                # foreach ref の読取チェック＋束縛変数/out を written 登録（型は保守的に未知）。
                self._check_reads(step, written, report, only=("foreach",))
                as_name = step.get("as", "item")
                written.add(f"vars.{as_name}")
                slot_types[f"vars.{as_name}"] = None
                out_decl = step.get("out")
                if isinstance(out_decl, str):
                    written.add(_root_slot(_slot_path(out_decl)))
                continue

            node = self.registry.get(stype or "")
            sig = node.signature(step) if node else None

            for name, ref in step.get("in", {}).items():
                if not _is_reference(ref):
                    continue
                root = _root_slot(_slot_path(ref))
                if root not in written:
                    report.add("dataflow", sid, "READ_BEFORE_WRITE", root)
                    continue
                if sig and name in sig.inputs:
                    declared = slot_types.get(root)
                    required = sig.inputs[name]
                    if declared is not None and declared != required:
                        report.add(
                            "type",
                            sid,
                            "TYPE_MISMATCH",
                            f"{root}:{declared} != {name} requires {required}",
                        )

            # 出力スロットを written/型マップへ登録
            if sig:
                self._register_outputs(step, sig, slot_types, written)

        return written

    def _register_outputs(self, step, sig, slot_types, written) -> None:
        out_decl = step.get("out")
        if isinstance(out_decl, str):
            root = _root_slot(_slot_path(out_decl))
            written.add(root)
            # 単一出力なら型を伝播
            if len(sig.outputs) == 1:
                slot_types[root] = next(iter(sig.outputs.values()))
        elif isinstance(out_decl, dict):
            for out_name, path in out_decl.items():
                root = _root_slot(_slot_path(path))
                written.add(root)
                if out_name in sig.outputs:
                    slot_types[root] = sig.outputs[out_name]

    def _check_reads(self, step, written, report, only=None) -> None:
        items = step.get("in", {}).items()
        refs = [(k, v) for k, v in items]
        if only:
            refs += [(k, step[k]) for k in only if k in step]
        for _name, ref in refs:
            if _is_reference(ref):
                root = _root_slot(_slot_path(ref))
                if root not in written:
                    report.add("dataflow", step.get("id", ""), "READ_BEFORE_WRITE", root)

    def _check_when_reads(self, step, written, report) -> None:
        when = step.get("when", "")
        for token in _extract_dollar_paths(when):
            root = _root_slot(token)
            if root not in written:
                report.add("dataflow", step.get("id", ""), "READ_BEFORE_WRITE", root)

# --- step グラフ補助 -----------------------------------------------------
def _index_steps(steps: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, step in enumerate(steps):
        sid = step.get("id")
        if sid:
            out[sid] = i
    return out


def _owned_body_ids(steps: list[dict[str, Any]]) -> set[str]:
    owned: set[str] = set()
    for step in steps:
        if step.get("type") == "foreach":
            owned.update(step.get("body", []))
    return owned


def _next_unowned(steps: list[dict[str, Any]], owned: set[str], start: int) -> int | None:
    i = start
    while i < len(steps):
        if steps[i].get("id") not in owned:
            return i
        i += 1
    return None


def _out_names(step: dict[str, Any]) -> tuple[str, ...]:
    out_decl = step.get("out")
    if isinstance(out_decl, dict):
        return tuple(out_decl.keys())
    return ("out",)


def _extract_dollar_paths(text: str) -> list[str]:
    """文字列中の `$.a.b.c` トークンを抽出（データフロー読取チェック用）。"""
    paths: list[str] = []
    i = 0
    n = len(text)
    ident = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")
    while i < n - 1:
        if text[i] == "$" and text[i + 1] == ".":
            j = i + 2
            while j < n and text[j] in ident:
                j += 1
            token = text[i + 2 : j].rstrip(".")
            if token:
                paths.append(token)
            i = j
        else:
            i += 1
    return paths
