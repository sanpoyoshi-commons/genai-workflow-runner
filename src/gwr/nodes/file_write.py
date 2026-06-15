"""file.write — TableRef を csv/xlsx に出力し base64 の FileRef（artifacts）にする。

出力前に数式インジェクション対策（先頭 = + - @ を ' で無害化）を必ず適用する。
"""

from __future__ import annotations

import base64
import io
from typing import Any, cast

from gwr.datatypes import TableRef, make_file_ref
from gwr.nodes._frame import sanitize_df_for_spreadsheet, table_ref_to_df
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature

_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class FileWriteNode(Node):
    TYPE = "file.write"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"table": "TableRef"}, outputs={"out": "FileRef"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        table = inputs.get("table")
        if not isinstance(table, dict):
            raise NodeError("TABLE_MISSING", "入力 table が TableRef ではない")
        fmt = str(config.get("format", "csv")).lower()
        if fmt not in _MIME:
            raise NodeError("UNSUPPORTED_FORMAT", fmt)

        df = sanitize_df_for_spreadsheet(table_ref_to_df(cast(TableRef, table)))
        default_name = f"output.{fmt}"
        display_name = str(config.get("name", default_name))

        buf = io.BytesIO()
        if fmt == "csv":
            buf.write(df.to_csv(index=False).encode("utf-8"))
        else:
            df.to_excel(buf, index=False, engine="openpyxl")

        contents = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"out": make_file_ref(display_name, _MIME[fmt], contents)}
