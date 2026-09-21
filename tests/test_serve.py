"""serve: /invoke が源内OSS の Web プロトコル準拠で {outputs, artifacts} を返す（mock LLM）。"""

import base64
import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from gwr.adapters.fakes import FakeLLMAdapter
from gwr.app import WorkflowApp
from gwr.serve import create_app

POC_TOML = (Path(__file__).resolve().parents[1] / "examples" / "poc.toml").read_text("utf-8")
CONFIG_DEFAULTS = """
[answer_generation]
model_id = "mock-llm"
system_prompt = "要約"
"""


def _client():
    app = WorkflowApp.from_toml(
        POC_TOML,
        adapters={"llm": FakeLLMAdapter(output="要約しました。")},
        config_defaults=CONFIG_DEFAULTS,
    )
    return TestClient(create_app(app))


def _xlsx_b64(records):
    df = pd.DataFrame(records, columns=["部署", "売上"])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_invoke_returns_outputs_and_artifacts():
    client = _client()
    body = {
        "inputs": {
            "data": [
                {"key": "data", "files": [{"filename": "in.xlsx",
                                           "content": _xlsx_b64([{"部署": "A", "売上": 3}])}]}
            ]
        }
    }
    resp = client.post("/invoke", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert "要約しました。" in data["outputs"]
    assert len(data["artifacts"]) == 1
    assert data["artifacts"][0]["display_name"] == "summary.xlsx"


def test_invoke_missing_required_returns_user_error():
    resp = _client().post("/invoke", json={"inputs": {}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"]["reason"] == "REQUIRED_MISSING"
    assert data["artifacts"] == []


def test_ui_spec_endpoint():
    resp = _client().get("/ui-spec")
    assert resp.status_code == 200
    assert resp.json()["data"]["type"] == "file"


def test_invoke_requires_api_key_when_configured():
    app = WorkflowApp.from_toml(
        POC_TOML,
        adapters={"llm": FakeLLMAdapter(output="x")},
        config_defaults=CONFIG_DEFAULTS,
    )
    client = TestClient(create_app(app, api_key="secret-key"))
    body = {"inputs": {"data": [{"key": "data", "files": [
        {"filename": "i.xlsx", "content": _xlsx_b64([{"部署": "A", "売上": 1}])}]}]}}

    # キー無し → 401
    assert client.post("/invoke", json=body).status_code == 401
    # 誤キー → 401
    assert client.post("/invoke", json=body, headers={"x-api-key": "wrong"}).status_code == 401
    # 正キー → 200
    ok = client.post("/invoke", json=body, headers={"x-api-key": "secret-key"})
    assert ok.status_code == 200
    assert "x" in ok.json()["outputs"]


def test_startup_validation_rejects_bad_flow():
    bad = """
version = "1"
[[steps]]
id = "x"
type = "no.such.node"
out = "vars.x"
"""
    app = WorkflowApp.from_toml(bad, adapters={})
    with pytest.raises(RuntimeError):
        create_app(app)
