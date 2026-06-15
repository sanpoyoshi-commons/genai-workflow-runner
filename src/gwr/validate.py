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
