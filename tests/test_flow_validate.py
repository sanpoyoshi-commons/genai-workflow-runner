"""flow.validate ノードの単体テスト（決定論・外部 I/O なし）。

`gwr validate --json` / MCP の `gwr_validate` と**同じレポート**を返すことを固定する。
新しい応答形を作らない（schema_version 1 の契約はここでも同じ）。
"""

from pathlib import Path

import pytest

from gwr.nodes.base import NodeContext, NodeError
from gwr.nodes.flow_validate import DEFAULT_MAX_BYTES, FlowValidateNode
from gwr.registry import build_default_registry
from gwr.validate import report_to_dict, validate_text_safe

CTX = NodeContext()
ROOT = Path(__file__).resolve().parents[1]
POC = (ROOT / "examples" / "poc.toml").read_text("utf-8")


def _run(text, config=None):
    return FlowValidateNode().run({"flow_toml": text}, config or {}, CTX)["out"]


def test_registered_in_the_default_registry():
    assert build_default_registry()["flow.validate"].TYPE == "flow.validate"


def test_signature_is_scalar_in_scalar_out():
    sig = FlowValidateNode.signature({})
    assert sig.inputs == {"flow_toml": "scalar"}
    assert sig.outputs == {"out": "scalar"}


@pytest.mark.parametrize(
    "flow_toml",
    [POC, POC.replace('else = "write"', 'else = "wirte"'), "これは TOML ではない ["],
    ids=["ok", "broken", "not-toml"],
)
def test_report_is_identical_to_validate_text_safe(flow_toml):
    assert _run(flow_toml) == report_to_dict(validate_text_safe(flow_toml))


def test_valid_flow_reports_ok():
    assert _run(POC) == {"schema_version": 1, "ok": True, "issues": []}


def test_broken_flow_reports_issues():
    report = _run('version = "1"\n[[steps]]\nid = "x"\ntype = "no.such"\nout = "vars.x"\n')
    assert report["ok"] is False
    assert report["issues"][0]["reason"] == "UNKNOWN_NODE_TYPE"


def test_toml_parse_error_is_a_report_not_an_exception():
    report = _run("x = [")
    assert report["ok"] is False
    assert report["issues"][0]["reason"] == "TOML_PARSE_ERROR"
    assert report["issues"][0]["step_id"] is None


# --- 入力の門番 ----------------------------------------------------------
@pytest.mark.parametrize("value", [None, 1, {"a": 1}, ["x"], b"bytes"])
def test_non_string_input_is_a_node_error(value):
    with pytest.raises(NodeError) as excinfo:
        FlowValidateNode().run({"flow_toml": value}, {}, CTX)
    assert excinfo.value.reason == "FLOW_TOML_MISSING"


def test_oversized_input_is_refused():
    with pytest.raises(NodeError) as excinfo:
        _run("#" + "a" * DEFAULT_MAX_BYTES)
    assert excinfo.value.reason == "FLOW_TOML_TOO_LARGE"


def test_limit_counts_utf8_bytes_not_characters():
    with pytest.raises(NodeError):
        _run("#" + "あ" * (DEFAULT_MAX_BYTES // 3 + 1))


def test_limit_is_configurable_per_step():
    with pytest.raises(NodeError) as excinfo:
        _run(POC, {"max_bytes": 10})
    assert excinfo.value.reason == "FLOW_TOML_TOO_LARGE"


def test_does_not_open_files(tmp_path):
    """パス文字列を渡してもファイルとして読まれない（本文として扱う）。"""
    target = tmp_path / "real.toml"
    target.write_text(POC, "utf-8")
    report = _run(str(target))
    assert report["ok"] is False
    assert {i["reason"] for i in report["issues"]} == {"TOML_PARSE_ERROR"}
