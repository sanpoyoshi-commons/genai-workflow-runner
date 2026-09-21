"""MCP サーバのツール／Resources とセキュリティ境界の固定。

MCP SDK は optional extra（`gwr[mcp]`）なので、未導入の環境ではこのファイルを skip する。
JSON-RPC を実際に喋る結合テストは tests/test_mcp_stdio.py（プロセス越し）。
"""

from __future__ import annotations

import base64
import io
import json
import socket
from pathlib import Path

import anyio
import pandas as pd
import pytest

pytest.importorskip("mcp", reason="gwr[mcp] 未導入（uv sync --extra mcp）")

from mcp.server.mcpserver.exceptions import ResourceError, ToolError  # noqa: E402

from gwr import assets  # noqa: E402
from gwr.app import WorkflowApp  # noqa: E402
from gwr.mcp_server import (  # noqa: E402
    MAX_FLOW_TOML_BYTES,
    _artifact_meta,
    build_server,
)
from gwr.validate import report_to_dict, validate_text_safe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
POC = (EXAMPLES / "poc.toml").read_text("utf-8")
HELLO = (EXAMPLES / "hello.toml").read_text("utf-8")

TOOL_NAMES = ["gwr_spec", "gwr_validate", "gwr_dry_run", "gwr_ui_spec"]

# 実ファイル入力を持たないので dry_run できる、artifacts を返すフロー。
CI_FLOW = """version = "1"

[[inputs]]
key = "q"
type = "text"
required = true

[[steps]]
id = "analyze"
type = "code_interpreter"
instruction = "集計して"
out = { artifacts = "files.charts", output = "vars.msg" }

[[outputs]]
key = "charts"
from = "$.files.charts[0]"
type = "file"
"""


def call(name: str, arguments: dict | None = None):
    """ツールを 1 回呼ぶ（構造化結果を返す）。"""

    async def _run():
        return await build_server().call_tool(name, arguments or {})

    return anyio.run(_run)


def read_resource(uri: str):
    async def _run():
        return await build_server().read_resource(uri)

    return anyio.run(_run)


# --- ツールの顔ぶれ ------------------------------------------------------
def test_exactly_four_tools_are_published():
    async def _run():
        return await build_server().list_tools()

    tools = anyio.run(_run)
    assert [t.name for t in tools] == TOOL_NAMES


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_has_a_description(name):
    """description は AI が読む唯一の手引きなので空を許さない。"""

    async def _run():
        return await build_server().list_tools()

    tool = next(t for t in anyio.run(_run) if t.name == name)
    assert tool.description and len(tool.description) >= 40


def test_validate_description_tells_the_agent_to_reach_ok():
    async def _run():
        return await build_server().list_tools()

    tool = next(t for t in anyio.run(_run) if t.name == "gwr_validate")
    assert "ok" in tool.description
    assert "reason" in tool.description


# --- gwr_validate は report_to_dict そのまま ------------------------------
@pytest.mark.parametrize(
    "flow_toml",
    [POC, POC.replace('else = "write"', 'else = "wirte"'), "これは TOML ではない ["],
    ids=["ok", "broken", "not-toml"],
)
def test_validate_returns_report_to_dict_verbatim(flow_toml):
    result = call("gwr_validate", {"flow_toml": flow_toml})
    assert result.structured_content == report_to_dict(validate_text_safe(flow_toml))


def test_validate_ok_for_poc():
    assert call("gwr_validate", {"flow_toml": POC}).structured_content == {
        "schema_version": 1,
        "ok": True,
        "issues": [],
    }


def test_validate_reports_toml_parse_error_with_null_step_id():
    issues = call("gwr_validate", {"flow_toml": "x = ["}).structured_content["issues"]
    assert [(i["category"], i["reason"], i["step_id"]) for i in issues] == [
        ("static", "TOML_PARSE_ERROR", None)
    ]


def test_validate_text_content_is_the_same_json():
    """content（非構造）と structuredContent が食い違わないこと。"""
    result = call("gwr_validate", {"flow_toml": POC})
    assert json.loads(result.content[0].text) == result.structured_content


# --- gwr_spec / gwr_ui_spec ---------------------------------------------
def test_spec_returns_the_bundled_markdown():
    text = call("gwr_spec").content[0].text
    assert text == assets.spec_markdown()
    assert text == (ROOT / "docs" / "flow-toml-for-ai.md").read_text("utf-8")


def test_ui_spec_matches_the_cli_output():
    """`gwr ui-spec` と同じ JSON になること（2 経路で形が分岐しない）。"""
    expected = WorkflowApp.from_toml(POC).ui_spec()
    assert call("gwr_ui_spec", {"flow_toml": POC}).structured_content == expected


def test_ui_spec_rejects_broken_toml():
    with pytest.raises(ToolError, match="TOML"):
        call("gwr_ui_spec", {"flow_toml": "x = ["})


# --- gwr_dry_run ---------------------------------------------------------
def test_dry_run_returns_outputs():
    result = call("gwr_dry_run", {"flow_toml": HELLO, "inputs": {}}).structured_content
    assert result["artifacts_meta"] == []
    assert "HelloWorld" in result["outputs"]


def test_dry_run_refuses_flows_that_need_real_files():
    """file 入力を要求するフローは MCP 経由では検証のみ可能とする。"""
    with pytest.raises(ToolError) as excinfo:
        call("gwr_dry_run", {"flow_toml": POC, "inputs": {}})
    message = str(excinfo.value)
    assert "gwr_validate" in message
    assert "file" in message


def test_dry_run_artifacts_meta_has_no_file_body():
    result = call("gwr_dry_run", {"flow_toml": CI_FLOW, "inputs": {"q": "x"}})
    meta = result.structured_content["artifacts_meta"]
    assert len(meta) == 1
    assert set(meta[0]) == {"display_name", "size_bytes", "row_count", "col_count"}
    # 本体を運びうるキーが 1 つも無いこと（応答全体を文字列化して確認）。
    assert "contents" not in json.dumps(result.structured_content)


def test_dry_run_does_not_write_to_stdout(capsys):
    """stdout は JSON-RPC 専用。利用者データがログ系統（既定 sink=stdout）へ出ない。"""
    call("gwr_dry_run", {"flow_toml": HELLO, "inputs": {}})
    assert capsys.readouterr().out == ""


# --- 入力サイズ上限 -------------------------------------------------------
@pytest.mark.parametrize("tool", ["gwr_validate", "gwr_dry_run", "gwr_ui_spec"])
def test_flow_toml_over_the_limit_is_refused(tool):
    oversized = "#" + "a" * MAX_FLOW_TOML_BYTES
    with pytest.raises(ToolError, match="大きすぎます"):
        call(tool, {"flow_toml": oversized})


def test_flow_toml_at_the_limit_is_accepted():
    padding = MAX_FLOW_TOML_BYTES - len(HELLO.encode("utf-8"))
    at_limit = HELLO + "#" + "a" * (padding - 1)
    assert len(at_limit.encode("utf-8")) == MAX_FLOW_TOML_BYTES
    assert call("gwr_validate", {"flow_toml": at_limit}).structured_content["ok"] is True


def test_limit_counts_utf8_bytes_not_characters():
    """日本語 1 文字は 3 バイト。文字数で数えていたらここが通ってしまう。"""
    oversized = "#" + "あ" * (MAX_FLOW_TOML_BYTES // 3 + 1)
    assert len(oversized) < MAX_FLOW_TOML_BYTES
    with pytest.raises(ToolError, match="大きすぎます"):
        call("gwr_validate", {"flow_toml": oversized})


# --- パスを受け取らない ---------------------------------------------------
@pytest.mark.parametrize(
    "path_like",
    ["examples/poc.toml", "/etc/passwd", "../../etc/passwd", str(EXAMPLES / "poc.toml")],
)
def test_flow_toml_is_never_treated_as_a_path(path_like):
    """パス文字列を渡してもファイルとして開かれず、TOML 本文として扱われる。"""
    result = call("gwr_validate", {"flow_toml": path_like}).structured_content
    assert result["ok"] is False
    assert result["issues"][0]["reason"] == "TOML_PARSE_ERROR"
    # 実在するファイルのパスを渡しても中身が読まれていない＝FILE_UNREADABLE も出ない。
    assert {i["reason"] for i in result["issues"]} == {"TOML_PARSE_ERROR"}


# --- 委譲が起きない -------------------------------------------------------
DELEGATE_ENV = {
    "GWR_LLM_ENDPOINT": "http://must-not-be-called.invalid/v1/chat/completions",
    "GWR_RAG_ENDPOINT": "http://must-not-be-called.invalid/api/law-rag",
    "GWR_CI_ENDPOINT": "http://must-not-be-called.invalid/api/code-interpreter",
    "GWR_LLM_API_KEY": "dummy",
    "GWR_RAG_API_KEY": "dummy",
    "GWR_CI_API_KEY": "dummy",
}

DELEGATING_FLOW = """version = "1"

[[inputs]]
key = "q"
type = "text"
required = true

[[steps]]
id = "retrieve"
type = "retrieval"
in = { query = "$.vars.q" }
out = "vars.docs"

[[steps]]
id = "answer"
type = "llm"
config_type = "answer_generation"
in = { docs = "$.vars.docs" }
out = "vars.msg"

[[outputs]]
key = "msg"
from = "$.vars.msg"
type = "text"
"""


@pytest.fixture
def no_network(monkeypatch):
    """ソケット接続そのものを塞ぐ見張り（gwr 側は一切差し替えない）。"""
    dialed: list[object] = []

    def refuse(self, address):  # noqa: ANN001
        dialed.append(address)
        raise AssertionError(f"MCP 経由で外部接続が発生した: {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    return dialed


def test_dry_run_does_not_delegate_even_with_endpoints_configured(monkeypatch, no_network):
    for key, value in DELEGATE_ENV.items():
        monkeypatch.setenv(key, value)

    result = call("gwr_dry_run", {"flow_toml": DELEGATING_FLOW, "inputs": {"q": "問"}})

    assert no_network == []
    # Fake アダプタの既定出力が返っている＝委譲先ではなく Fake が使われた。
    assert result.structured_content["outputs"] == "（モック出力）"


def test_dry_run_never_imports_the_http_transport_layer(monkeypatch, no_network):
    """env が揃っていてもネットワーク呼び出し層（httpx トランスポート）に触らない。"""
    import sys

    for key, value in DELEGATE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delitem(sys.modules, "gwr.adapters.transport", raising=False)

    call("gwr_dry_run", {"flow_toml": DELEGATING_FLOW, "inputs": {"q": "問"}})

    assert "gwr.adapters.transport" not in sys.modules


# --- 式評価の既存制限が MCP 経由でも効く（Step 4-4） ---------------------
def _flow_with_when(when: str) -> str:
    return f"""version = "1"

[[inputs]]
key = "q"
type = "text"
required = true

[[steps]]
id = "gate"
type = "branch"
when = {json.dumps(when)}
then = "hit"
else = "hit"

[[steps]]
id = "hit"
type = "llm"
config_type = "answer_generation"
in = {{ text = "$.vars.q" }}
out = "vars.msg"
next = "__end__"

[[outputs]]
key = "msg"
from = "$.vars.msg"
type = "text"
"""


@pytest.mark.parametrize(
    ("label", "when"),
    [
        ("too-long", "len($.vars.q) == 0 || " * 40 + "true"),
        ("too-deep", "(" * 40 + "true" + ")" * 40),
        ("matches-not-allowed", 'matches($.vars.q, "a")'),
        ("function-not-allowed", 'eval($.vars.q)'),
    ],
)
def test_expression_limits_still_apply_through_mcp(label, when):
    result = call("gwr_validate", {"flow_toml": _flow_with_when(when)}).structured_content
    assert result["ok"] is False
    assert "BRANCH_WHEN_UNCOMPILABLE" in {i["reason"] for i in result["issues"]}


def test_allowed_function_still_compiles():
    when = "len($.vars.q) == 0"
    result = call("gwr_validate", {"flow_toml": _flow_with_when(when)}).structured_content
    assert "BRANCH_WHEN_UNCOMPILABLE" not in {i["reason"] for i in result["issues"]}


# --- Resources（Step 3 / Step 4-3） --------------------------------------
def test_resources_list_exactly_the_bundled_examples():
    async def _run():
        return await build_server().list_resources()

    resources = anyio.run(_run)
    assert [str(r.uri) for r in resources] == [
        f"gwr://examples/{name}" for name in assets.example_names()
    ]
    assert all(r.description for r in resources)


@pytest.mark.parametrize("name", assets.example_names())
def test_resource_read_returns_the_example_body(name):
    contents = read_resource(f"gwr://examples/{name}")
    assert contents[0].content == (EXAMPLES / name).read_text("utf-8")


@pytest.mark.parametrize(
    "uri",
    [
        "gwr://examples/../flow-toml-for-ai.md",
        "gwr://examples/../../cli.py",
        "gwr://examples/%2e%2e/cli.py",
        "gwr://examples/poc.toml/../hello.toml",
        "file:///etc/passwd",
        "gwr://examples/",
        "gwr://examples/rag-no-result-markers.example.txt",
    ],
)
def test_resource_read_cannot_escape_the_bundled_examples(uri):
    with pytest.raises(ResourceError):
        read_resource(uri)


# --- artifacts のメタ化（単体） -----------------------------------------
def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def test_artifact_meta_counts_csv_rows_and_columns():
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    meta = _artifact_meta(
        {"display_name": "out.csv", "contents": _b64(df.to_csv(index=False).encode("utf-8"))}
    )
    assert meta["row_count"] == 3
    assert meta["col_count"] == 2
    assert meta["size_bytes"] > 0
    assert "contents" not in meta


def test_artifact_meta_counts_xlsx_rows_and_columns():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    meta = _artifact_meta({"display_name": "out.xlsx", "contents": _b64(buf.getvalue())})
    assert (meta["row_count"], meta["col_count"]) == (2, 3)


def test_artifact_meta_leaves_shape_unknown_for_other_formats():
    meta = _artifact_meta({"display_name": "chart.png", "contents": _b64(b"\x89PNG\r\n")})
    assert meta["row_count"] is None
    assert meta["col_count"] is None
    assert meta["size_bytes"] == 6


def test_artifact_meta_survives_unparsable_contents():
    meta = _artifact_meta({"display_name": "broken.csv", "contents": "not base64!!"})
    assert meta == {
        "display_name": "broken.csv",
        "size_bytes": 0,
        "row_count": None,
        "col_count": None,
    }


# --- flow.validate / file.write(text) が MCP 経由でも使える -------------
SELF_CHECKING_FLOW = """version = "1"

[[inputs]]
key = "q"
type = "text"
required = true

[[steps]]
id = "gen"
type = "llm"
config_type = "answer_generation"
in = { text = "$.vars.q" }
out = "vars.body"

[[steps]]
id = "check"
type = "flow.validate"
in = { flow_toml = "$.vars.body" }
out = "vars.report"

[[steps]]
id = "write"
type = "file.write"
format = "text"
name = "note.md"
in = { text = "$.vars.body" }
out = "files.doc"

[[outputs]]
key = "report"
from = "$.vars.report"
type = "text"

[[outputs]]
key = "doc"
from = "$.files.doc"
type = "file"
"""


def test_flow_validate_node_is_usable_through_mcp():
    assert call("gwr_validate", {"flow_toml": SELF_CHECKING_FLOW}).structured_content["ok"] is True


def test_dry_run_reports_artifacts_from_a_text_write():
    """file.load を使わないフローでも artifacts_meta が実体を持つ（本体は返さない）。"""
    result = call("gwr_dry_run", {"flow_toml": SELF_CHECKING_FLOW, "inputs": {"q": "x"}})
    meta = result.structured_content["artifacts_meta"]
    assert len(meta) == 1
    assert meta[0]["display_name"] == "note.md"
    assert meta[0]["size_bytes"] > 0
    assert "contents" not in json.dumps(result.structured_content)


def test_dry_run_renders_the_validation_report_as_json():
    outputs = call(
        "gwr_dry_run", {"flow_toml": SELF_CHECKING_FLOW, "inputs": {"q": "x"}}
    ).structured_content["outputs"]
    assert '"schema_version": 1' in outputs
    assert "'ok'" not in outputs


# --- 引数の説明文が入力スキーマに載っていること ------------------------
# 受け入れテストで、AI が gwr_validate にファイルパスを渡そうとする挙動が
# 2 回中 2 回観測された。ツールの description だけでは引数を組み立てる時点で
# 目に入らないため、入力スキーマ側にも「本文を渡す」と明記してある。
FLOW_TOML_TOOLS = ["gwr_validate", "gwr_dry_run", "gwr_ui_spec"]


def _schema(name: str) -> dict:
    async def _run():
        return await build_server().list_tools()

    tool = next(t for t in anyio.run(_run) if t.name == name)
    return tool.input_schema


@pytest.mark.parametrize("name", FLOW_TOML_TOOLS)
def test_flow_toml_argument_says_to_pass_the_body_not_a_path(name):
    description = _schema(name)["properties"]["flow_toml"].get("description", "")
    assert description, f"{name}.flow_toml に説明が無い"
    assert "本文" in description
    assert "パス" in description


def test_dry_run_inputs_argument_is_described():
    description = _schema("gwr_dry_run")["properties"]["inputs"].get("description", "")
    assert description
    assert "inputs" in description


def test_passing_a_path_is_rejected_with_a_schema_error():
    """パスを『flow_toml として』渡すのは本文扱いになる（＝開かれない）。
    別名の引数（path 等）はスキーマ検証で弾かれる。"""
    with pytest.raises(ToolError):
        call("gwr_validate", {"path": str(EXAMPLES / "poc.toml")})
