"""file.transform — pandas による集計・整形（ローカル処理）。

`ops` を順に適用し TableRef→TableRef。決定論を死守（groupby は既定で key ソート）。
対応 op：select / rename / filter / dropna / sort / head / groupby / sanitize。
"""

from __future__ import annotations

from typing import Any, cast

import pandas as pd

from gwr.datatypes import TableRef
from gwr.nodes._frame import df_to_table_ref, sanitize_df_for_spreadsheet, table_ref_to_df
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature


def _op_select(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    return df[list(spec["columns"])]


def _op_rename(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    return df.rename(columns=dict(spec["columns"]))


def _op_filter(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    col = spec["column"]
    series = df[col]
    if "eq" in spec:
        return df[series == spec["eq"]]
    if "ne" in spec:
        return df[series != spec["ne"]]
    if "gt" in spec:
        return df[series > spec["gt"]]
    if "ge" in spec:
        return df[series >= spec["ge"]]
    if "lt" in spec:
        return df[series < spec["lt"]]
    if "le" in spec:
        return df[series <= spec["le"]]
    raise NodeError("BAD_OP", "filter は eq/ne/gt/ge/lt/le のいずれかが必要")


def _op_dropna(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    subset = spec.get("columns")
    return df.dropna(subset=subset)


def _op_sort(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    return df.sort_values(
        by=list(spec["by"]), ascending=spec.get("ascending", True), kind="stable"
    ).reset_index(drop=True)


def _op_head(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    return df.head(int(spec["n"]))


def _op_groupby(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    by = list(spec["by"])
    agg = dict(spec["agg"])
    # sort=True（既定）でキー順に整列＝決定論。
    return df.groupby(by, as_index=False, sort=True).agg(agg)


def _op_sanitize(df: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    return sanitize_df_for_spreadsheet(df)


_OPS = {
    "select": _op_select,
    "rename": _op_rename,
    "filter": _op_filter,
    "dropna": _op_dropna,
    "sort": _op_sort,
    "head": _op_head,
    "groupby": _op_groupby,
    "sanitize": _op_sanitize,
}


class FileTransformNode(Node):
    TYPE = "file.transform"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        return NodeSignature(inputs={"table": "TableRef"}, outputs={"out": "TableRef"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        table = inputs.get("table")
        if not isinstance(table, dict):
            raise NodeError("TABLE_MISSING", "入力 table が TableRef ではない")
        df = table_ref_to_df(cast(TableRef, table))
        ops = config.get("ops") or []
        for spec in ops:
            op_name = spec.get("op")
            func = _OPS.get(op_name)
            if func is None:
                raise NodeError("UNKNOWN_OP", str(op_name))
            try:
                df = func(df, spec)
            except KeyError as e:
                raise NodeError("MISSING_COLUMN", str(e)) from e
        return {"out": df_to_table_ref(df)}
