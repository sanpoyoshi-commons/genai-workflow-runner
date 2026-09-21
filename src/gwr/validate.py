"""validate — フロー TOML 検証（Runner の dry-run モードの薄いラッパ）。

実行と同じグラフ解釈コードを共有する。検証 3 系統：
- 静的：構文・必須キー・id 一意・then/else/next の実在・in 参照妥当・when コンパイル可。
- データフロー：読まれるスロットが上流で書かれているか（綴り誤り検出）。出力 from も照合。
- 型伝播：ノードの型シグネチャからスロット→型を伝播し in 参照の型整合を照合。
不正は fail-closed（ok=False）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from gwr.contract import parse_inputs, parse_outputs
from gwr.datatypes import TypeLevel
from gwr.registry import build_default_registry
from gwr.runner import Runner, ValidationReport


def _input_slots(flow: dict[str, Any]) -> dict[str, TypeLevel]:
    """[[inputs]] から初期スロット→型を導出（file→FileRef、その他→scalar）。"""
    slots: dict[str, TypeLevel] = {}
    for inp in parse_inputs(flow):
        if inp.type == "file":
            slots[f"files.{inp.key}"] = "FileRef"
        else:
            slots[f"vars.{inp.key}"] = "scalar"
    return slots


def validate_flow(
    flow: dict[str, Any], registry: dict[str, Any] | None = None
) -> ValidationReport:
    """パース済みフロー（dict）を検証してレポートを返す。"""
    runner = Runner(registry or build_default_registry())
    steps = flow.get("steps", [])
    initial_slots = _input_slots(flow)
    output_refs = [o.from_ref for o in parse_outputs(flow)]
    return runner.dry_run(steps, initial_slots=initial_slots, output_refs=output_refs)


def validate_text(text: str, registry: dict[str, Any] | None = None) -> ValidationReport:
    flow = tomllib.loads(text)
    return validate_flow(flow, registry)


def validate_file(path: str | Path, registry: dict[str, Any] | None = None) -> ValidationReport:
    return validate_text(Path(path).read_text("utf-8"), registry)


# --- 機械可読レポート ----------------------------------------------------
# `gwr validate --json` の出力スキーマ版。既存の reason コードはこの版の間は
# 改名・削除しない（追加のみ）。破壊的変更を要する場合はこの数値を上げる。
SCHEMA_VERSION = 1


def validate_text_safe(text: str, registry: dict[str, Any] | None = None) -> ValidationReport:
    """`validate_text` と同じだが、TOML パース失敗もレポートとして返す。

    フロー TOML を「本文の文字列」で受ける経路（MCP ツール）用。パス受けの
    `validate_file_safe` と同じ形の結果になる（`report_to_dict` に渡せる）。
    `FILE_UNREADABLE` はファイルに触らないため構造的に発生しない。
    理由コードは report.add の引数にリテラルで書く（docs/cli.md との突合テストが
    AST で全数を拾えるように。tests/test_reason_codes.py）。
    """
    report = ValidationReport()
    try:
        return validate_text(text, registry)
    except tomllib.TOMLDecodeError as e:
        report.add("static", "", "TOML_PARSE_ERROR", str(e))
        return report


def validate_file_safe(
    path: str | Path, registry: dict[str, Any] | None = None
) -> ValidationReport:
    """`validate_file` と同じだが、読み取り/パース失敗もレポートとして返す。

    機械可読出力では例外を投げずに常に構造化結果を返す必要があるため。
    読み取れた後の判定は `validate_text_safe` と同一（経路を1本にして差を作らない）。
    """
    report = ValidationReport()
    try:
        text = Path(path).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        report.add("static", "", "FILE_UNREADABLE", str(e))
        return report
    return validate_text_safe(text, registry)


def report_to_dict(report: ValidationReport) -> dict[str, Any]:
    """レポートを JSON 化可能な dict にする（`--json` と後続 MCP の共通形）。

    `step_id` は「特定できる step が無い」場合に null。空文字は出さない。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": report.ok,
        "issues": [
            {
                "category": issue.category,
                "step_id": issue.step_id or None,
                "reason": issue.reason,
                "detail": issue.detail,
            }
            for issue in report.issues
        ],
    }
