"""file.load — csv/xlsx を読み込んで TableRef を産出する（ローカル処理・外部に出ない）。

入力 `file` は inline（base64）の FileRef。format は明示 or 拡張子/mime から推定。
行数上限を超えたら NodeError（巨大ファイル保護）。
"""

from __future__ import annotations

import base64
import binascii
import io
import zipfile
from typing import Any

import pandas as pd

from gwr.nodes._frame import df_to_table_ref
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature

DEFAULT_MAX_ROWS = 100_000
# xlsx(zip) の解凍後合計サイズ上限（zip-bomb 保護）。解凍前に中央ディレクトリの宣言サイズで判定。
DEFAULT_MAX_XLSX_UNCOMPRESSED = 200 * 1024 * 1024  # 200 MiB


def _guard_xlsx_zipbomb(raw: bytes, max_uncompressed: int) -> None:
    """xlsx(zip) の解凍後合計サイズが上限を超えないか中央ディレクトリで検査する（解凍はしない）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            total = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as e:
        raise NodeError("FILE_PARSE_ERROR", f"xlsx: {e}") from e
    if total > max_uncompressed:
        raise NodeError("XLSX_TOO_LARGE", f"uncompressed {total} > {max_uncompressed}")


def _detect_format(file_ref: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit.lower()
    name = (file_ref.get("display_name") or "").lower()
    mime = (file_ref.get("mime") or "").lower()
    if name.endswith((".xlsx", ".xls")) or "spreadsheet" in mime or "excel" in mime:
        return "xlsx"
    if name.endswith(".csv") or "csv" in mime:
        return "csv"
    return "csv"


class FileLoadNode(Node):
    TYPE = "file.load"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"file": "FileRef"}, outputs={"out": "TableRef"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        file_ref = inputs.get("file")
        if not isinstance(file_ref, dict) or "contents" not in file_ref:
            raise NodeError("FILE_MISSING", "入力 file が inline FileRef ではない")
        try:
            raw = base64.b64decode(file_ref["contents"], validate=True)
        except (binascii.Error, ValueError) as e:
            raise NodeError("FILE_DECODE_ERROR", str(e)) from e

        fmt = _detect_format(file_ref, config.get("format"))
        max_rows = int(config.get("max_rows", DEFAULT_MAX_ROWS))
        # zip-bomb 保護は解凍前に実施（下の広域 except で握り潰さず専用 reason で fail-closed）。
        if fmt == "xlsx":
            _guard_xlsx_zipbomb(
                raw, int(config.get("max_uncompressed_bytes", DEFAULT_MAX_XLSX_UNCOMPRESSED))
            )
        buf = io.BytesIO(raw)
        try:
            if fmt == "xlsx":
                df = pd.read_excel(buf, engine="openpyxl")
            else:
                df = pd.read_csv(buf)
        except Exception as e:  # noqa: BLE001 — pandas/openpyxl の多様な例外を集約
            raise NodeError("FILE_PARSE_ERROR", f"{fmt}: {e}") from e

        if len(df) > max_rows:
            raise NodeError("ROW_LIMIT_EXCEEDED", f"{len(df)} > {max_rows}")

        return {"out": df_to_table_ref(df)}
