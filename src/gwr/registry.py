"""既定ノードレジストリの構築。

branch / foreach は Runner が制御フローとして直接扱うため、レジストリには載せない。
adapters（llm/retrieval/code_interpreter）は実体を注入する都合で別途登録する。
"""

from __future__ import annotations

from gwr.adapters.code_interpreter import CodeInterpreterNode
from gwr.adapters.llm import LLMNode
from gwr.adapters.retrieval import RetrievalNode
from gwr.nodes.base import Node
from gwr.nodes.file_load import FileLoadNode
from gwr.nodes.file_transform import FileTransformNode
from gwr.nodes.file_write import FileWriteNode
from gwr.nodes.flow_validate import FlowValidateNode


def build_default_registry() -> dict[str, Node]:
    """ローカル（file.* / flow.validate）ノード＋源内OSS の API 委譲ノードを
    登録したレジストリを返す。

    委譲ノードのアダプタ実装は NodeContext.adapters で別途注入する（本番＝源内OSS／テスト＝Fake）。
    """
    nodes: list[Node] = [
        FileLoadNode(),
        FileTransformNode(),
        FileWriteNode(),
        FlowValidateNode(),
        LLMNode(),
        RetrievalNode(),
        CodeInterpreterNode(),
    ]
    return {n.TYPE: n for n in nodes}
