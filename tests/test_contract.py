"""contract: inputs→源内UI JSON、conversation_history=hidden、outputs写像、検証。"""

import pytest

from gwr.contract import (
    ContractError,
    bind_inputs,
    bind_outputs,
    parse_inputs,
    parse_outputs,
    to_genai_ui_spec,
)
from gwr.envelope import Envelope


def test_to_genai_ui_spec_all_eight_components():
    flow = {
        "inputs": [
            {"key": "q", "type": "text", "label": "質問", "required": True, "max_length": 100},
            {"key": "n", "type": "number", "label": "数", "min": 1, "max": 10, "default_value": 3},
            {"key": "memo", "type": "textarea", "label": "メモ"},
            {"key": "f", "type": "file", "label": "ファイル", "accept": "text/csv"},
            {"key": "s", "type": "select", "label": "選択", "items": ["a", "b"]},
            {"key": "c", "type": "checkbox", "label": "チェック", "items": ["x"]},
            {"key": "r", "type": "radio", "label": "ラジオ", "items": ["y"]},
            {"key": "h", "type": "hidden", "default_value": "z"},
        ]
    }
    spec = to_genai_ui_spec(parse_inputs(flow))
    assert spec["q"] == {"type": "text", "title": "質問", "required": True, "max_length": 100}
    assert spec["n"]["type"] == "number" and spec["n"]["default_value"] == 3
    assert spec["s"]["items"] == ["a", "b"]
    assert spec["h"] == {"type": "hidden", "default_value": "z"}  # title/required を付けない


def test_conversation_history_forced_hidden():
    flow = {"inputs": [{"key": "conversation_history", "type": "textarea", "label": "履歴"}]}
    specs = parse_inputs(flow)
    assert specs[0].type == "hidden"


def test_choice_without_items_rejected():
    with pytest.raises(ContractError) as ei:
        parse_inputs({"inputs": [{"key": "s", "type": "select", "label": "x"}]})
    assert ei.value.reason == "INPUT_MISSING_ITEMS"


def test_bind_inputs_scalar_file_and_required():
    flow = {
        "inputs": [
            {"key": "q", "type": "text", "required": True},
            {"key": "n", "type": "number"},
            {"key": "data", "type": "file", "required": True},
        ]
    }
    request = {
        "q": "hello",
        "n": "5",  # 数値は文字列で来ても数値化
        "data": [{"key": "data", "files": [{"filename": "x.csv", "content": "QQ=="}]}],
    }
    env = bind_inputs(parse_inputs(flow), request, Envelope())
    assert env.get("vars.q") == "hello"
    assert env.get("vars.n") == 5
    assert env.get("files.data")["display_name"] == "x.csv"
    assert env.get("files.data")["mime"] == "text/csv"
    assert env.get("files.data")["contents"] == "QQ=="


def test_bind_inputs_real_genai_files_bucket_form():
    # 源内本来の形：ファイルは inputs.files[] に集約され key で識別される
    flow = {
        "inputs": [
            {"key": "question", "type": "text"},
            {"key": "data", "type": "file", "required": True},
        ]
    }
    request = {
        "question": "集計して",
        "files": [
            {"key": "data", "files": [{"filename": "uriage.xlsx", "content": "UEsD"}]}
        ],
    }
    env = bind_inputs(parse_inputs(flow), request, Envelope())
    assert env.get("vars.question") == "集計して"
    assert env.get("files.data")["display_name"] == "uriage.xlsx"
    assert env.get("files.data")["contents"] == "UEsD"


def test_bind_inputs_files_bucket_missing_required_fails():
    flow = {"inputs": [{"key": "data", "type": "file", "required": True}]}
    # files バケットに別 key しか無い → data は欠落扱い
    request = {"files": [{"key": "other", "files": [{"filename": "x", "content": "QQ=="}]}]}
    with pytest.raises(ContractError) as ei:
        bind_inputs(parse_inputs(flow), request, Envelope())
    assert ei.value.reason == "REQUIRED_MISSING"


def test_bind_inputs_accepts_async_flat_file_form():
    # 非同期curl例の平坦形 {key, contents, filename} も受理する（受信両対応）
    flow = {"inputs": [{"key": "data", "type": "file", "required": True}]}
    request = {"data": [{"key": "data", "filename": "y.csv", "contents": "QkI="}]}
    env = bind_inputs(parse_inputs(flow), request, Envelope())
    assert env.get("files.data")["display_name"] == "y.csv"
    assert env.get("files.data")["contents"] == "QkI="
    assert env.get("files.data")["mime"] == "text/csv"


def test_bind_inputs_required_missing_fails_closed():
    flow = {"inputs": [{"key": "q", "type": "text", "required": True}]}
    with pytest.raises(ContractError) as ei:
        bind_inputs(parse_inputs(flow), {}, Envelope())
    assert ei.value.reason == "REQUIRED_MISSING"


def test_bind_inputs_number_range_and_checkbox():
    flow = {
        "inputs": [
            {"key": "n", "type": "number", "min": 1, "max": 3},
            {"key": "c", "type": "checkbox", "items": ["a", "b", "c"]},
        ]
    }
    with pytest.raises(ContractError) as ei:
        bind_inputs(parse_inputs(flow), {"n": 9}, Envelope())
    assert ei.value.reason == "ABOVE_MAX"

    env = bind_inputs(parse_inputs(flow), {"n": 2, "c": "a,c"}, Envelope())
    assert env.get("vars.c") == ["a", "c"]


def test_bind_inputs_default_value_applied_when_absent():
    flow = {"inputs": [{"key": "n", "type": "number", "default_value": 7}]}
    env = bind_inputs(parse_inputs(flow), {}, Envelope())
    assert env.get("vars.n") == 7


def test_bind_outputs_text_and_artifacts():
    flow = {
        "outputs": [
            {"key": "msg", "from": "$.vars.msg", "type": "text", "label": "メッセージ"},
            {"key": "report", "from": "$.files.report", "type": "file", "label": "結果"},
        ]
    }
    env = Envelope()
    env.set("vars.msg", "完了しました")
    env.set("files.report", {"display_name": "r.xlsx", "contents": "QkI=", "storage": "inline"})
    result = bind_outputs(parse_outputs(flow), env)
    assert "## メッセージ" in result["outputs"]
    assert "完了しました" in result["outputs"]
    # ファイル自身の display_name（拡張子つき）を優先
    assert result["artifacts"] == [{"display_name": "r.xlsx", "contents": "QkI="}]


def test_parse_outputs_missing_from_rejected():
    with pytest.raises(ContractError) as ei:
        parse_outputs({"outputs": [{"key": "x", "type": "text"}]})
    assert ei.value.reason == "OUTPUT_MISSING_FROM"
