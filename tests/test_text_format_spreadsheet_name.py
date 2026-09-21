"""`format = "text"` ＋ 表計算の拡張子を validate が落とすこと（TEXT_FORMAT_SPREADSHEET_NAME）。

`text` は数式インジェクション無害化をしない（渡された文字列をそのまま書く）。表計算ソフトが
開く名前と組み合わせると、無害化されていない内容が利用者の手元で数式として解釈されるため、
静的検査で fail-closed に落とす。csv / xlsx 側の無害化は従来どおり効く。
"""

from pathlib import Path

import pytest

from gwr.nodes.file_write import SPREADSHEET_SUFFIXES, writes_unsanitized_spreadsheet
from gwr.validate import report_to_dict, validate_text_safe

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

REASON = "TEXT_FORMAT_SPREADSHEET_NAME"


def _flow(fmt: str, name: str) -> str:
    return f"""version = "1"

[[inputs]]
key = "body"
type = "textarea"
required = true

[[steps]]
id = "write"
type = "file.write"
format = "{fmt}"
name = "{name}"
in = {{ text = "$.vars.body" }}
out = "files.doc"

[[outputs]]
key = "doc"
from = "$.files.doc"
type = "file"
"""


def _reasons(flow_toml: str) -> set[str]:
    return {i["reason"] for i in report_to_dict(validate_text_safe(flow_toml))["issues"]}


# --- 落ちること ----------------------------------------------------------
@pytest.mark.parametrize("suffix", SPREADSHEET_SUFFIXES)
def test_text_format_with_spreadsheet_suffix_is_rejected(suffix):
    report = report_to_dict(validate_text_safe(_flow("text", f"report{suffix}")))
    assert report["ok"] is False
    issue = next(i for i in report["issues"] if i["reason"] == REASON)
    assert issue["category"] == "static"
    assert issue["step_id"] == "write"
    assert issue["detail"] == f"report{suffix}"


@pytest.mark.parametrize("name", ["REPORT.CSV", "Report.XlSx", "data.TSV"])
def test_suffix_match_is_case_insensitive(name):
    assert REASON in _reasons(_flow("text", name))


def test_format_match_is_case_insensitive():
    assert REASON in _reasons(_flow("TEXT", "report.csv"))


def test_suffix_check_looks_at_the_end_only():
    """名前の途中に拡張子らしき文字列があっても落とさない（過検知しない）。"""
    assert REASON not in _reasons(_flow("text", "csv-notes.md"))
    assert REASON not in _reasons(_flow("text", "about.xlsx.toml"))


# --- 通ること ------------------------------------------------------------
@pytest.mark.parametrize("name", ["flow.toml", "note.md", "output.txt", "readme"])
def test_text_format_with_safe_name_passes(name):
    assert report_to_dict(validate_text_safe(_flow("text", name)))["ok"] is True


def test_text_format_without_name_passes():
    """`name` 省略時の既定は output.txt なので安全側。"""
    flow = _flow("text", "x").replace('name = "x"\n', "")
    assert report_to_dict(validate_text_safe(flow))["ok"] is True


# --- csv / xlsx は従来どおり ---------------------------------------------
@pytest.mark.parametrize(("fmt", "name"), [("csv", "report.csv"), ("xlsx", "report.xlsx")])
def test_table_formats_keep_their_spreadsheet_names(fmt, name):
    """無害化される側は表計算の拡張子で当然通る（この検査で巻き添えにしない）。"""
    flow = f"""version = "1"

[[inputs]]
key = "data"
type = "file"
required = true

[[steps]]
id = "load"
type = "file.load"
in = {{ file = "$.files.data" }}
out = "tables.raw"

[[steps]]
id = "write"
type = "file.write"
format = "{fmt}"
name = "{name}"
in = {{ table = "$.tables.raw" }}
out = "files.report"

[[outputs]]
key = "report"
from = "$.files.report"
type = "file"
"""
    assert report_to_dict(validate_text_safe(flow))["ok"] is True


# --- 述語の単体 ----------------------------------------------------------
@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ({"format": "text", "name": "a.csv"}, True),
        ({"format": "text", "name": "a.toml"}, False),
        ({"format": "csv", "name": "a.csv"}, False),
        ({"format": "xlsx", "name": "a.xlsx"}, False),
        ({"name": "a.csv"}, False),  # format 省略時の既定は csv
        ({"format": "text"}, False),  # name 省略時の既定は output.txt
        ({}, False),
    ],
)
def test_predicate(step, expected):
    assert writes_unsanitized_spreadsheet(step) is expected


# --- 既存 examples を巻き添えにしない ------------------------------------
@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.toml")), ids=lambda p: p.name)
def test_shipped_examples_still_validate(path):
    assert report_to_dict(validate_text_safe(path.read_text("utf-8")))["ok"] is True
