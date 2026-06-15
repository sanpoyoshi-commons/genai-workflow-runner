"""file.load / file.transform / file.write のノード単体テスト（決定論）。"""

import base64
import io

import pandas as pd
import pytest

from gwr.nodes._frame import sanitize_cell
from gwr.nodes.base import NodeContext, NodeError
from gwr.nodes.file_load import FileLoadNode
from gwr.nodes.file_transform import FileTransformNode
from gwr.nodes.file_write import FileWriteNode

CTX = NodeContext()


def _csv_file_ref(text: str, name="data.csv"):
    contents = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"display_name": name, "mime": "text/csv", "storage": "inline", "contents": contents}


def _xlsx_file_ref(df: pd.DataFrame, name="data.xlsx"):
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    contents = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"display_name": name, "storage": "inline", "contents": contents}


# --- file.load ----------------------------------------------------------
def test_load_csv_to_table_ref():
    ref = _csv_file_ref("部署,売上\nA,10\nB,20\n")
    out = FileLoadNode().run({"file": ref}, {}, CTX)["out"]
    assert out["columns"] == ["部署", "売上"]
    assert out["row_count"] == 2
    assert out["data"] == [{"部署": "A", "売上": 10}, {"部署": "B", "売上": 20}]
    assert out["dtypes"]["売上"].startswith("int")


def test_load_xlsx_to_table_ref():
    df = pd.DataFrame({"部署": ["A", "B"], "売上": [10, 20]})
    out = FileLoadNode().run({"file": _xlsx_file_ref(df)}, {}, CTX)["out"]
    assert out["columns"] == ["部署", "売上"]
    assert out["row_count"] == 2


def test_load_empty_csv_is_zero_rows():
    ref = _csv_file_ref("部署,売上\n")
    out = FileLoadNode().run({"file": ref}, {}, CTX)["out"]
    assert out["row_count"] == 0
    assert out["data"] == []


def test_load_row_limit_exceeded():
    ref = _csv_file_ref("x\n" + "\n".join(str(i) for i in range(5)) + "\n")
    with pytest.raises(NodeError) as ei:
        FileLoadNode().run({"file": ref}, {"max_rows": 2}, CTX)
    assert ei.value.reason == "ROW_LIMIT_EXCEEDED"


def test_load_xlsx_uncompressed_limit_exceeded():
    # zip-bomb 保護：解凍後合計サイズ上限を超えたら解凍前に XLSX_TOO_LARGE で fail-closed。
    df = pd.DataFrame({"a": [1, 2, 3]})
    with pytest.raises(NodeError) as ei:
        FileLoadNode().run({"file": _xlsx_file_ref(df)}, {"max_uncompressed_bytes": 1}, CTX)
    assert ei.value.reason == "XLSX_TOO_LARGE"


def test_load_bad_base64_rejected():
    bad = {"display_name": "x.csv", "contents": "!!!notbase64!!!"}
    with pytest.raises(NodeError) as ei:
        FileLoadNode().run({"file": bad}, {}, CTX)
    assert ei.value.reason == "FILE_DECODE_ERROR"


def test_load_missing_file_rejected():
    with pytest.raises(NodeError) as ei:
        FileLoadNode().run({"file": "not a fileref"}, {}, CTX)
    assert ei.value.reason == "FILE_MISSING"


# --- file.transform -----------------------------------------------------
def _table(records, columns):
    df = pd.DataFrame(records, columns=columns)
    from gwr.nodes._frame import df_to_table_ref

    return df_to_table_ref(df)


def test_transform_groupby_sum_deterministic():
    table = _table(
        [{"部署": "B", "売上": 5}, {"部署": "A", "売上": 10}, {"部署": "A", "売上": 3}],
        ["部署", "売上"],
    )
    ops = [{"op": "groupby", "by": ["部署"], "agg": {"売上": "sum"}}]
    out = FileTransformNode().run({"table": table}, {"ops": ops}, CTX)["out"]
    # key ソートで A,B の順＝決定論
    assert out["data"] == [{"部署": "A", "売上": 13}, {"部署": "B", "売上": 5}]


def test_transform_filter_sort_select():
    table = _table(
        [{"k": "a", "v": 3}, {"k": "b", "v": 1}, {"k": "c", "v": 2}], ["k", "v"]
    )
    ops = [
        {"op": "filter", "column": "v", "ge": 2},
        {"op": "sort", "by": ["v"], "ascending": True},
        {"op": "select", "columns": ["k"]},
    ]
    out = FileTransformNode().run({"table": table}, {"ops": ops}, CTX)["out"]
    assert out["data"] == [{"k": "c"}, {"k": "a"}]


def test_transform_unknown_op_rejected():
    table = _table([{"k": "a"}], ["k"])
    with pytest.raises(NodeError) as ei:
        FileTransformNode().run({"table": table}, {"ops": [{"op": "nope"}]}, CTX)
    assert ei.value.reason == "UNKNOWN_OP"


def test_transform_sanitize_op_escapes_formula():
    table = _table([{"name": "=SUM(A1)"}, {"name": "ok"}], ["name"])
    out = FileTransformNode().run({"table": table}, {"ops": [{"op": "sanitize"}]}, CTX)["out"]
    assert out["data"] == [{"name": "'=SUM(A1)"}, {"name": "ok"}]


# --- file.write ---------------------------------------------------------
def test_write_csv_artifact_roundtrip():
    table = _table([{"部署": "A", "売上": 10}], ["部署", "売上"])
    out = FileWriteNode().run({"table": table}, {"format": "csv", "name": "r.csv"}, CTX)["out"]
    assert out["display_name"] == "r.csv"
    assert out["mime"] == "text/csv"
    decoded = base64.b64decode(out["contents"]).decode("utf-8")
    assert "部署,売上" in decoded
    assert "A,10" in decoded


def test_write_xlsx_artifact_readable():
    table = _table([{"部署": "A", "売上": 10}], ["部署", "売上"])
    out = FileWriteNode().run({"table": table}, {"format": "xlsx"}, CTX)["out"]
    raw = base64.b64decode(out["contents"])
    df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
    assert list(df.columns) == ["部署", "売上"]
    assert df.iloc[0]["売上"] == 10


def test_write_sanitizes_formula_injection():
    table = _table([{"name": "=cmd"}], ["name"])
    out = FileWriteNode().run({"table": table}, {"format": "csv"}, CTX)["out"]
    decoded = base64.b64decode(out["contents"]).decode("utf-8")
    assert "'=cmd" in decoded  # 先頭 = が無害化される


def test_write_unsupported_format_rejected():
    table = _table([{"k": "a"}], ["k"])
    with pytest.raises(NodeError) as ei:
        FileWriteNode().run({"table": table}, {"format": "pdf"}, CTX)
    assert ei.value.reason == "UNSUPPORTED_FORMAT"


def test_sanitize_cell_unit():
    assert sanitize_cell("=1+1") == "'=1+1"
    assert sanitize_cell("+x") == "'+x"
    assert sanitize_cell("normal") == "normal"
    assert sanitize_cell(5) == 5
