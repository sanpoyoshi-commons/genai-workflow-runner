"""PoC 統合テスト（受け入れの最終ゲート）。

examples/poc.toml を 1 本同期実行し、出力テーブル・artifact・メッセージがゴールデンと
完全一致することを確認する。LLM は Fake（決定論）。スコープ外＝非同期・大データ・実 LLM。
"""

import base64
import io
import json
from pathlib import Path

import pandas as pd

from gwr.adapters.fakes import FakeLLMAdapter
from gwr.app import WorkflowApp

ROOT = Path(__file__).resolve().parents[1]
POC_TOML = (ROOT / "examples" / "poc.toml").read_text("utf-8")
GOLDEN = json.loads((Path(__file__).parent / "golden" / "poc_summary.json").read_text("utf-8"))

CONFIG_DEFAULTS = """
[answer_generation]
model_id = "mock-llm"
system_prompt = "あなたは集計結果を要約するアシスタントです。"
[answer_generation.inference_config]
temperature = 0.0
"""

LLM_OUTPUT = "部署別に売上を集計しました。"


def _xlsx_request(records):
    df = pd.DataFrame(records, columns=["部署", "売上"])
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    content = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "data": [{"key": "data", "files": [{"filename": "in.xlsx", "content": content}]}]
    }


def _app():
    return WorkflowApp.from_toml(
        POC_TOML,
        adapters={"llm": FakeLLMAdapter(output=LLM_OUTPUT)},
        config_defaults=CONFIG_DEFAULTS,
    )


def test_poc_flow_matches_golden():
    request = _xlsx_request(
        [{"部署": "B", "売上": 20}, {"部署": "A", "売上": 10}, {"部署": "A", "売上": 5}]
    )
    result = _app().invoke(request)

    # 利用者向けメッセージがゴールデンと一致
    assert result["outputs"] == GOLDEN["outputs"]

    # artifact（xlsx）を復元しテーブル内容がゴールデンと完全一致
    assert len(result["artifacts"]) == 1
    art = result["artifacts"][0]
    assert art["display_name"] == "summary.xlsx"  # file.write の name（拡張子つき）を優先
    raw = base64.b64decode(art["contents"])
    df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
    records = json.loads(df.to_json(orient="records", force_ascii=False))
    assert records == GOLDEN["summary_records"]


def test_poc_empty_data_takes_empty_branch():
    request = _xlsx_request([])  # 空データ
    result = _app().invoke(request)
    # 空分岐：メッセージのみ・artifact 無し（write を通らない）
    assert result["outputs"] == f"## 要約メッセージ\n\n{LLM_OUTPUT}"
    assert result["artifacts"] == []


def test_poc_flow_validates_clean():
    report = _app().validate()
    assert report.ok, report.issues


def test_poc_missing_required_input_returns_user_error():
    result = _app().invoke({})  # data 未指定
    assert result["artifacts"] == []
    assert result["error"]["reason"] == "REQUIRED_MISSING"
    assert "必須" in result["outputs"]


def test_poc_ui_spec_generates_file_component():
    spec = _app().ui_spec()
    assert spec["data"]["type"] == "file"
    assert spec["data"]["title"] == "対象Excel"
    assert spec["data"]["required"] is True
