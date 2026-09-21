"""flow.validate — フロー TOML の本文を検証してレポートを産出する（ローカル処理）。

生成したフロー TOML をフロー自身の中で検証するためのノード。`gwr validate --json` や
MCP の `gwr_validate` と**同じレポート**（`{schema_version, ok, issues[]}`）を返すので、
`branch` の `when` に `$.<slot>.ok` を書けば「通ったか」で分岐できる。

外部 I/O は持たない。入力はファイルパスではなく**本文の文字列**で、ファイルシステムには
触らない（`gwr.validate.validate_text_safe` と同じ境界）。
"""

from __future__ import annotations

from typing import Any

from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature

# 受け取る本文の上限。MCP ツールの上限（1 MiB）と揃える。step の max_bytes で変更可。
DEFAULT_MAX_BYTES = 1024 * 1024


class FlowValidateNode(Node):
    TYPE = "flow.validate"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        # レポートは JSON ネイティブな dict（＝型レベルは scalar）。
        return NodeSignature(inputs={"flow_toml": "scalar"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        # 遅延 import：gwr.validate → gwr.registry → 各ノード、と循環するため。
        from gwr.validate import report_to_dict, validate_text_safe

        text = inputs.get("flow_toml")
        if not isinstance(text, str):
            raise NodeError("FLOW_TOML_MISSING", "input=flow_toml expected=str")

        max_bytes = int(config.get("max_bytes", DEFAULT_MAX_BYTES))
        if len(text.encode("utf-8")) > max_bytes:
            raise NodeError("FLOW_TOML_TOO_LARGE", f"max_bytes={max_bytes}")

        return {"out": report_to_dict(validate_text_safe(text))}
