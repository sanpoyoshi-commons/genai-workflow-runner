"""file.write — TableRef を csv/xlsx に、文字列を text に出力し base64 の FileRef にする。

表（csv / xlsx）は出力前に数式インジェクション対策（先頭 = + - @ を ' で無害化）を必ず
適用する。text は表計算ソフトで開くものではないため無害化を行わず、渡された文字列を
そのまま UTF-8 で書く（TOML や Markdown を生成して返す用途）。
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
    "text": "text/plain",
}
# text だけ入力が TableRef ではなく文字列（スロットの型レベルは scalar）。
_TEXT_FORMAT = "text"

# 表計算ソフトが開く拡張子。`format = "text"` は無害化しない（渡された文字列を
# そのまま書く）ため、この拡張子と組み合わせると無害化されていないセルが利用者の
# 手元で数式として解釈される。組み合わせは静的検査で落とす（runner._static_checks）。
SPREADSHEET_SUFFIXES = (".csv", ".tsv", ".xls", ".xlsx", ".xlsm", ".slk", ".dif")


def _format_of(step: dict[str, Any]) -> str:
    return str(step.get("format", "csv")).lower()


def writes_unsanitized_spreadsheet(step: dict[str, Any]) -> bool:
    """`format = "text"` で表計算ソフトが開く名前を出そうとしているか。

    真なら無害化されていない内容がスプレッドシートとして開かれ得る。判定だけを返し、
    レポートへの記録は呼び出し側（runner._static_checks）が行う。理由コードは
    report.add の引数にリテラルで書く必要があるため（tests/test_reason_codes.py）。
    """
    if _format_of(step) != _TEXT_FORMAT:
        return False
    return str(step.get("name", "")).lower().endswith(SPREADSHEET_SUFFIXES)


class FileWriteNode(Node):
    TYPE = "file.write"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        if _format_of(step) == _TEXT_FORMAT:
            return NodeSignature(inputs={"text": "scalar"}, outputs={"out": "FileRef"})
        return NodeSignature(inputs={"table": "TableRef"}, outputs={"out": "FileRef"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        fmt = _format_of(config)
        if fmt not in _MIME:
            raise NodeError("UNSUPPORTED_FORMAT", fmt)
        display_name = str(config.get("name", f"output.{'txt' if fmt == _TEXT_FORMAT else fmt}"))

        if fmt == _TEXT_FORMAT:
            text = inputs.get("text")
            if not isinstance(text, str):
                raise NodeError("TEXT_MISSING", "input=text expected=str")
            raw = text.encode("utf-8")
        else:
            table = inputs.get("table")
            if not isinstance(table, dict):
                raise NodeError("TABLE_MISSING", "input=table expected=TableRef")
            df = sanitize_df_for_spreadsheet(table_ref_to_df(cast(TableRef, table)))
            buf = io.BytesIO()
            if fmt == "csv":
                buf.write(df.to_csv(index=False).encode("utf-8"))
            else:
                df.to_excel(buf, index=False, engine="openpyxl")
            raw = buf.getvalue()

        contents = base64.b64encode(raw).decode("ascii")
        return {"out": make_file_ref(display_name, _MIME[fmt], contents)}
