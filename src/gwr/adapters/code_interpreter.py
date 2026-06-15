"""code_interpreter ノード ＋ 源内 Code Interpreter API アダプタ契約。

instruction と入力ファイル（FileRef[]）を委譲し、生成された artifacts（FileRef[]）を
回収する。実行環境（サンドボックス）は源内/クラウド側。本基盤はサンドボックスを持たない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gwr.datatypes import FileRef
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature


@dataclass
class CodeInterpreterRequest:
    instruction: str
    files: list[FileRef] = field(default_factory=list)


@dataclass
class CodeInterpreterResponse:
    artifacts: list[FileRef] = field(default_factory=list)
    output: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class CodeInterpreterAdapter(Protocol):
    def run(self, request: CodeInterpreterRequest) -> CodeInterpreterResponse: ...


class CodeInterpreterNode(Node):
    TYPE = "code_interpreter"
    OUT_NAMES = ("artifacts", "output")

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(outputs={"artifacts": "FileRef", "output": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        adapter: CodeInterpreterAdapter | None = ctx.adapters.get("code_interpreter")
        if adapter is None:
            raise NodeError("ADAPTER_MISSING", "code_interpreter アダプタ未注入")
        instruction = config.get("instruction")
        if not isinstance(instruction, str) or not instruction:
            raise NodeError("INSTRUCTION_MISSING", "instruction が未指定")
        files = inputs.get("files", [])
        if isinstance(files, dict):  # 単一 FileRef を許容
            files = [files]
        req = CodeInterpreterRequest(instruction=instruction, files=list(files))
        resp = adapter.run(req)
        if resp.usage:
            ctx.add_usage(resp.usage)
        return {"artifacts": list(resp.artifacts), "output": resp.output}
