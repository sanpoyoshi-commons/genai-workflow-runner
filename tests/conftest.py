"""テスト用の最小ノード群（実 I/O なし・決定論）。"""

from __future__ import annotations

from typing import Any

from gwr.nodes.base import DataError, Node, NodeContext, NodeError, NodeSignature


class EchoNode(Node):
    """入力 value をそのまま out に返す scalar ノード。"""

    TYPE = "test.echo"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"value": "scalar"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        return {"out": inputs.get("value")}


class AddOneNode(Node):
    """vars 経由の数値に +1 する（foreach 集約テスト用）。"""

    TYPE = "test.addone"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"n": "scalar"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        return {"out": inputs["n"] + 1}


class BoomNode(Node):
    """常に NodeError を投げる（on_error テスト用）。"""

    TYPE = "test.boom"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"value": "scalar"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        raise NodeError("BOOM")


class DataBoomNode(Node):
    """DataError（座標付き）を投げる。"""

    TYPE = "test.databoom"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"value": "scalar"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        raise DataError("NOT_NUMERIC", row=42, col=2)


class TableProducerNode(Node):
    """out に TableRef を産出する（型伝播テスト用）。"""

    TYPE = "test.table"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(outputs={"out": "TableRef"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "columns": [],
            "dtypes": {},
            "row_count": 0,
            "storage": "inline",
            "data": [],
        }
        return {"out": empty}


class TableConsumerNode(Node):
    """TableRef を要求するノード（型不一致検出テスト用）。"""

    TYPE = "test.needs_table"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"table": "TableRef"}, outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        return {"out": inputs["table"]["row_count"]}


def make_test_registry() -> dict[str, Node]:
    nodes = [
        EchoNode(),
        AddOneNode(),
        BoomNode(),
        DataBoomNode(),
        TableProducerNode(),
        TableConsumerNode(),
    ]
    return {n.TYPE: n for n in nodes}
