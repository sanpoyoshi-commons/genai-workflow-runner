"""validate_text_safe（TOML 本文を受ける安全版）の突合。

MCP ツールはフロー TOML を「本文の文字列」で受けるため、パス受けの
`validate_file_safe` と同じ形の結果をテキストから得られる必要がある。
両者が同一の dict になることをここで固定する（理由コードは増やさない）。
"""

from pathlib import Path

import pytest

from gwr.validate import report_to_dict, validate_file_safe, validate_text_safe

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "examples" / "poc.toml"


def _dicts(path: Path) -> tuple[dict, dict]:
    by_path = report_to_dict(validate_file_safe(path))
    by_text = report_to_dict(validate_text_safe(path.read_text("utf-8")))
    return by_path, by_text


def test_valid_flow_matches_path_version():
    by_path, by_text = _dicts(POC)
    assert by_text == by_path
    assert by_text["ok"] is True
    assert by_text["issues"] == []


# 引き継ぎノートの「意図的な破壊3種」をテキスト経路でも同一に再現する。
BROKEN = {
    # 未定義 id へのジャンプ（static / BRANCH_ELSE_UNRESOLVED）
    "branch_else_unresolved": ('else = "write"', 'else = "wirte"'),
    # out の書き忘れ（dataflow / READ_BEFORE_WRITE）
    "read_before_write": ('out = "tables.summary"', 'out = "tables.summary_"'),
    # 型不整合（type / TYPE_MISMATCH）— file.write の table に FileRef を渡す
    "type_mismatch": (
        'name = "summary.xlsx"\nin = { table = "$.tables.summary" }',
        'name = "summary.xlsx"\nin = { table = "$.files.data" }',
    ),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_flow_matches_path_version(tmp_path, name):
    old, new = BROKEN[name]
    text = POC.read_text("utf-8")
    assert old in text, f"poc.toml に {old!r} が無い（テストの前提が崩れている）"
    broken = text.replace(old, new, 1)

    path = tmp_path / "broken.toml"
    path.write_text(broken, "utf-8")

    by_path = report_to_dict(validate_file_safe(path))
    by_text = report_to_dict(validate_text_safe(broken))
    assert by_text == by_path
    assert by_text["ok"] is False
    assert by_text["issues"], "壊したのに issues が空"


def test_toml_parse_error_is_reported_not_raised():
    result = report_to_dict(validate_text_safe("これは TOML ではない ["))
    assert result["ok"] is False
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["category"] == "static"
    assert issue["reason"] == "TOML_PARSE_ERROR"
    assert issue["step_id"] is None


def test_schema_version_is_present():
    assert report_to_dict(validate_text_safe("version = \"1\"\n"))["schema_version"] == 1


def test_file_unreadable_is_path_only():
    """FILE_UNREADABLE はテキスト受けでは構造的に発生しない。"""
    missing = report_to_dict(validate_file_safe("/nonexistent/flow.toml"))
    assert missing["issues"][0]["reason"] == "FILE_UNREADABLE"
    by_text = report_to_dict(validate_text_safe("x = ["))
    assert {i["reason"] for i in by_text["issues"]} == {"TOML_PARSE_ERROR"}
