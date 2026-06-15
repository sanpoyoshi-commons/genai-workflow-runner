"""TableRef ↔ pandas.DataFrame 変換とスプレッドシート注入対策の共通ヘルパ。

すべて JSON ネイティブ型のみを TableRef.data に残す（Envelope 直列化往復一致の前提）。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from gwr.datatypes import TableRef, make_table_ref

# 数式インジェクションの起点になりうる先頭文字（OWASP CSV Injection）。
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: Any) -> Any:
    """文字列セルが数式起点文字で始まる場合、先頭に ' を付けて無害化する。"""
    if isinstance(value, str) and value.startswith(_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def df_to_table_ref(df: pd.DataFrame) -> TableRef:
    """DataFrame を inline TableRef（JSON ネイティブ）へ変換する。"""
    columns = [str(c) for c in df.columns]
    dtypes = {str(c): str(df[c].dtype) for c in df.columns}
    # to_json 経由で numpy 型・NaN(→null) を JSON ネイティブへ正規化する。
    records: list[dict[str, Any]] = json.loads(df.to_json(orient="records", force_ascii=False))
    return make_table_ref(columns=columns, dtypes=dtypes, data=records)


def table_ref_to_df(table: TableRef) -> pd.DataFrame:
    """inline TableRef を DataFrame へ復元する（列順を保持）。"""
    columns = table.get("columns") or None
    data = table.get("data", [])
    df = pd.DataFrame(data)
    if columns:
        # data が空でも宣言列で空フレームを作る／列順を宣言どおりに揃える。
        df = df.reindex(columns=columns)
    return df


def sanitize_df_for_spreadsheet(df: pd.DataFrame) -> pd.DataFrame:
    """全セル＋ヘッダを数式インジェクション対策で無害化した複製を返す。"""
    safe = df.copy()
    for col in safe.columns:
        series = safe[col]
        # pandas 3.0 は文字列列を string dtype にするため object/string 双方を対象にする。
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            safe[col] = series.map(sanitize_cell)
    safe.columns = [sanitize_cell(str(c)) for c in safe.columns]
    return safe
