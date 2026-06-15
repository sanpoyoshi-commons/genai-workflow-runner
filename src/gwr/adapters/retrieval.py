"""retrieval ノード ＋ 源内 RAG API アダプタ契約。

source（検索対象）と query を載せて委譲し、正規化済み Doc[] を out に書く。
実体（検索・索引）は源内側。本モジュールは整形と件数検証のみ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gwr.datatypes import Doc
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature


@dataclass
class RetrievalRequest:
    source: str
    query: str
    top_k: int = 5
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResponse:
    docs: list[Doc]
    usage: dict[str, int] = field(default_factory=dict)


class RetrievalAdapter(Protocol):
    def search(self, request: RetrievalRequest) -> RetrievalResponse: ...


class RetrievalNode(Node):
    TYPE = "retrieval"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(outputs={"out": "Doc[]"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        adapter: RetrievalAdapter | None = ctx.adapters.get("retrieval")
        if adapter is None:
            raise NodeError("ADAPTER_MISSING", "retrieval アダプタ未注入")
        query = inputs.get("query")
        if not isinstance(query, str):
            raise NodeError("QUERY_MISSING", "入力 query が文字列ではない")
        req = RetrievalRequest(
            source=str(config.get("source", "")),
            query=query,
            top_k=int(config.get("top_k", 5)),
            filters=dict(config.get("filters", {})),
        )
        resp = adapter.search(req)
        if resp.usage:
            ctx.add_usage(resp.usage)
        return {"out": list(resp.docs)}
