"""`gwr mcp` を別プロセスで起動し、JSON-RPC を直接喋る結合テスト。

クライアント側は MCP SDK を使わず素の JSON-RPC（改行区切り）を stdin/stdout に流す。
initialize → tools/list → tools/call / resources/read までを実際の stdio で通す。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="gwr[mcp] 未導入（uv sync --extra mcp）")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
PROTOCOL_VERSION = "2026-07-28"


class StdioClient:
    """`gwr mcp` を子プロセスで動かす最小の JSON-RPC クライアント。"""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = {**os.environ, **(env or {})}
        self._next_id = 0
        self.proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> StdioClient:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "gwr.cli", "mcp"],
            cwd=ROOT,
            env=self._env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.proc is not None
        self.proc.stdin.close()  # type: ignore[union-attr]
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — 念のため
            self.proc.kill()

    # --- 素の JSON-RPC ---------------------------------------------------
    def _send(self, payload: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc is not None and self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise AssertionError(f"サーバが応答せず終了した。stderr:\n{stderr}")
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        self._send(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        )
        response = self._recv()
        assert response["id"] == self._next_id, response
        return response

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> dict:
        response = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gwr-tests", "version": "0"},
            },
        )
        assert "result" in response, response
        self.notify("notifications/initialized")
        self.initialize_result = response["result"]
        return self.initialize_result

    # --- 便利ラッパ ------------------------------------------------------
    def call_tool(self, name: str, arguments: dict) -> dict:
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        assert "result" in response, response
        return response["result"]


@pytest.fixture
def client():
    with StdioClient() as c:
        yield c


def test_initialize_advertises_tools_and_resources(client):
    result = client.initialize_result
    assert result["serverInfo"]["name"] == "gwr"
    assert result["serverInfo"]["version"]
    assert "tools" in result["capabilities"]
    assert "resources" in result["capabilities"]
    # stdio のみ＝HTTP/SSE 用の能力は出さない（トランスポートを増やしていない）。
    assert client.request("ping").get("result") == {}


def test_tools_list_returns_exactly_four_tools(client):
    result = client.request("tools/list")["result"]
    names = [t["name"] for t in result["tools"]]
    assert names == ["gwr_spec", "gwr_validate", "gwr_dry_run", "gwr_ui_spec"]
    assert all(t.get("description") for t in result["tools"])


def test_validate_ok_over_the_wire(client):
    result = client.call_tool(
        "gwr_validate", {"flow_toml": (EXAMPLES / "poc.toml").read_text("utf-8")}
    )
    assert result.get("isError") is not True
    assert result["structuredContent"] == {"schema_version": 1, "ok": True, "issues": []}


@pytest.mark.parametrize(
    ("label", "old", "new", "reason"),
    [
        ("branch", 'else = "write"', 'else = "wirte"', "BRANCH_ELSE_UNRESOLVED"),
        ("dataflow", 'out = "tables.summary"', 'out = "tables.summary_"', "READ_BEFORE_WRITE"),
        (
            "type",
            'name = "summary.xlsx"\nin = { table = "$.tables.summary" }',
            'name = "summary.xlsx"\nin = { table = "$.files.data" }',
            "TYPE_MISMATCH",
        ),
    ],
)
def test_validate_broken_flow_over_the_wire(client, label, old, new, reason):
    text = (EXAMPLES / "poc.toml").read_text("utf-8")
    assert old in text
    result = client.call_tool("gwr_validate", {"flow_toml": text.replace(old, new, 1)})
    report = result["structuredContent"]
    assert report["ok"] is False
    assert reason in {i["reason"] for i in report["issues"]}


def test_validate_not_toml_over_the_wire(client):
    report = client.call_tool("gwr_validate", {"flow_toml": "これは TOML ではない ["})[
        "structuredContent"
    ]
    assert report["ok"] is False
    assert report["issues"] == [
        {
            "category": "static",
            "step_id": None,
            "reason": "TOML_PARSE_ERROR",
            "detail": report["issues"][0]["detail"],
        }
    ]
    assert report["issues"][0]["step_id"] is None


def test_spec_over_the_wire(client):
    result = client.call_tool("gwr_spec", {})
    text = result["content"][0]["text"]
    assert text == (ROOT / "docs" / "flow-toml-for-ai.md").read_text("utf-8")


def test_dry_run_over_the_wire_returns_no_file_body(client):
    flow = """version = "1"

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
    result = client.call_tool("gwr_dry_run", {"flow_toml": flow, "inputs": {"q": "x"}})
    payload = result["structuredContent"]
    assert [sorted(m) for m in payload["artifacts_meta"]] == [
        ["col_count", "display_name", "row_count", "size_bytes"]
    ]
    assert "contents" not in json.dumps(result)


def test_dry_run_with_file_inputs_is_an_error_result(client):
    result = client.call_tool(
        "gwr_dry_run", {"flow_toml": (EXAMPLES / "poc.toml").read_text("utf-8"), "inputs": {}}
    )
    assert result["isError"] is True
    assert "gwr_validate" in result["content"][0]["text"]


def test_resources_list_and_read_over_the_wire(client):
    listed = client.request("resources/list")["result"]["resources"]
    assert [r["uri"] for r in listed] == [
        f"gwr://examples/{p.name}" for p in sorted(EXAMPLES.glob("*.toml"))
    ]

    read = client.request("resources/read", {"uri": "gwr://examples/poc.toml"})["result"]
    assert read["contents"][0]["text"] == (EXAMPLES / "poc.toml").read_text("utf-8")


def test_resources_read_outside_examples_is_a_protocol_error(client):
    response = client.request(
        "resources/read", {"uri": "gwr://examples/../flow-toml-for-ai.md"}
    )
    assert "error" in response, response


def test_endpoints_in_env_do_not_cause_delegation_over_the_wire():
    """実エンドポイント設定済みの環境でも Fake が使われる（到達不能ホストを指定）。"""
    env = {
        "GWR_LLM_ENDPOINT": "http://must-not-be-called.invalid/v1/chat/completions",
        "GWR_RAG_ENDPOINT": "http://must-not-be-called.invalid/api/law-rag",
        "GWR_CI_ENDPOINT": "http://must-not-be-called.invalid/api/code-interpreter",
    }
    flow = """version = "1"

[[inputs]]
key = "q"
type = "text"
required = true

[[steps]]
id = "answer"
type = "llm"
config_type = "answer_generation"
in = { text = "$.vars.q" }
out = "vars.msg"

[[outputs]]
key = "msg"
from = "$.vars.msg"
type = "text"
"""
    with StdioClient(env=env) as client:
        payload = client.call_tool("gwr_dry_run", {"flow_toml": flow, "inputs": {"q": "問"}})
        assert payload["structuredContent"]["outputs"] == "（モック出力）"


def test_stdout_carries_only_json_rpc(client):
    """dry_run を通しても stdout に JSON-RPC 以外の行が混ざらない。"""
    client.call_tool(
        "gwr_dry_run",
        {"flow_toml": (EXAMPLES / "hello.toml").read_text("utf-8"), "inputs": {}},
    )
    # 次の応答がそのまま読めれば、余計な行が挟まっていない。
    result = client.request("tools/list")["result"]
    assert len(result["tools"]) == 4
