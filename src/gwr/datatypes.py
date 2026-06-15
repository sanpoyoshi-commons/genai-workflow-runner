"""データ契約（厳守）。

ステップ間でやり取りする正規化単位を定義する。すべて JSON ネイティブ型のみで
構成し（Envelope の直列化往復一致＝再開単位の前提）、pandas 等の非 JSON 型を
スロットに残さない。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# 型伝播チェックが参照する「型レベル」。カラム名照合とは別レイヤ。
TypeLevel = Literal["scalar", "Doc[]", "TableRef", "FileRef"]

Storage = Literal["inline", "gcs_uri"]


class Doc(TypedDict, total=False):
    """検索／参照の正規化単位。"""

    id: str
    title: str
    source: str
    content: str
    score: float  # optional


class TableRef(TypedDict, total=False):
    """表データ。大データは uri 参照（storage="gcs_uri"）、PoC は inline。"""

    columns: list[str]
    dtypes: dict[str, str]
    row_count: int
    storage: Storage
    data: list[dict[str, Any]]  # storage=="inline" のときのみ


class FileRef(TypedDict, total=False):
    """ファイル。生成物は base64 で artifacts に乗る。"""

    display_name: str
    mime: str
    storage: Storage
    contents: str  # base64（storage=="inline" のとき）


def make_table_ref(
    columns: list[str],
    dtypes: dict[str, str],
    data: list[dict[str, Any]],
) -> TableRef:
    """inline な TableRef を組み立てる。row_count は data から導出する。"""
    return {
        "columns": list(columns),
        "dtypes": dict(dtypes),
        "row_count": len(data),
        "storage": "inline",
        "data": data,
    }


def make_file_ref(display_name: str, mime: str, contents_b64: str) -> FileRef:
    """inline（base64）な FileRef を組み立てる。"""
    return {
        "display_name": display_name,
        "mime": mime,
        "storage": "inline",
        "contents": contents_b64,
    }
