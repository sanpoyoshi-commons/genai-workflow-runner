"""`required = true` は「キーの存在」ではなく「値が入っていること」を見る。

`[[inputs]]` はスキーマではなく UI 生成の宣言なので、利用者から見た `required` は
HTML5 の required（空欄不可）と意味を揃える。ブラウザ側の検証は `--set` や API 直叩きでは
効かないため、サーバ側で弾く（fail-closed）。

「無い」（REQUIRED_MISSING）と「空」（REQUIRED_EMPTY）は原因が違うので別コードで返す。
どちらも実行時エラー（ContractError）で、`validate` の理由コード一覧（docs/cli.md）とは別系統。
"""

from pathlib import Path

import pytest

from gwr.app import WorkflowApp, render_user_message
from gwr.contract import ContractError, bind_inputs, parse_inputs
from gwr.envelope import Envelope
from gwr.validate import report_to_dict, validate_text_safe

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

FLOW = """version = "1"

[[inputs]]
key = "must"
type = "text"
label = "必須の項目"
required = true

[[inputs]]
key = "opt"
type = "text"
label = "任意の項目"
required = false

[[steps]]
id = "echo"
type = "llm"
config_type = "answer_generation"
in = { text = "$.vars.must" }
out = "vars.msg"

[[outputs]]
key = "msg"
from = "$.vars.msg"
type = "text"
"""

BLANKS = {
    "空文字": "",
    "半角スペース": "   ",
    "全角スペース": "　　",
    "タブ": "\t",
    "改行": "\n",
    "NBSP": " ",
    "混在": " 　\t\n",
}


def _bind(request_inputs: dict) -> Envelope:
    return bind_inputs(parse_inputs({"inputs": [
        {"key": "must", "type": "text", "required": True},
        {"key": "opt", "type": "text", "required": False},
    ]}), request_inputs, Envelope())


# --- 空は弾く ------------------------------------------------------------
@pytest.mark.parametrize("label", sorted(BLANKS))
def test_required_rejects_blank(label):
    with pytest.raises(ContractError) as excinfo:
        _bind({"must": BLANKS[label]})
    assert excinfo.value.reason == "REQUIRED_EMPTY"
    assert excinfo.value.key == "must"


def test_required_rejects_none():
    with pytest.raises(ContractError) as excinfo:
        _bind({"must": None})
    assert excinfo.value.reason == "REQUIRED_EMPTY"


def test_missing_key_is_still_required_missing():
    """「無い」と「空」を取り違えない。"""
    with pytest.raises(ContractError) as excinfo:
        _bind({})
    assert excinfo.value.reason == "REQUIRED_MISSING"


# --- 値ありは通す --------------------------------------------------------
@pytest.mark.parametrize("value", ["x", "  x  ", "0", "あ", "　x"])
def test_required_accepts_a_real_value(value):
    env = _bind({"must": value})
    assert env.get("vars.must") == value, "値は trim せずそのまま束縛する"


def test_zero_is_not_blank():
    """数値の 0 や False は「空」ではない（文字列以外は判定対象外）。"""
    inputs = parse_inputs({"inputs": [{"key": "n", "type": "number", "required": True}]})
    assert bind_inputs(inputs, {"n": 0}, Envelope()).get("vars.n") == 0


# --- required = false は空を通す -----------------------------------------
@pytest.mark.parametrize("label", sorted(BLANKS))
def test_optional_accepts_blank(label):
    env = _bind({"must": "ok", "opt": BLANKS[label]})
    assert env.get("vars.opt") == BLANKS[label]


# --- 利用者向け文言が分かれる --------------------------------------------
def test_user_messages_differ():
    missing = render_user_message("REQUIRED_MISSING", None)
    empty = render_user_message("REQUIRED_EMPTY", None)
    assert missing != empty
    assert "空" in empty


# --- invoke 経由（アプリ全体） -------------------------------------------
def test_invoke_reports_required_empty():
    app = WorkflowApp.from_toml(FLOW, config_defaults='[default]\nmodel_id = "local"\n')
    result = app.invoke({"must": "  "})
    assert result["error"]["reason"] == "REQUIRED_EMPTY"
    assert result["outputs"] == "必須の入力が空です。"
    assert result["artifacts"] == []


def test_invoke_succeeds_with_a_value():
    app = WorkflowApp.from_toml(FLOW, config_defaults='[default]\nmodel_id = "local"\n')
    from gwr.adapters.fakes import FakeLLMAdapter

    app.adapters = {"llm": FakeLLMAdapter()}
    result = app.invoke({"must": "こんにちは"})
    assert result.get("error") is None


# --- 既存 examples を巻き添えにしない ------------------------------------
@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.toml")), ids=lambda p: p.name)
def test_shipped_examples_still_validate(path):
    assert report_to_dict(validate_text_safe(path.read_text("utf-8")))["ok"] is True


def test_hidden_default_value_is_unaffected():
    """hidden の default_value 経路（キー自体が来ない）は従来どおり。"""
    inputs = parse_inputs({"inputs": [
        {"key": "greeting", "type": "hidden", "default_value": "HelloWorld"},
    ]})
    assert bind_inputs(inputs, {}, Envelope()).get("vars.greeting") == "HelloWorld"
