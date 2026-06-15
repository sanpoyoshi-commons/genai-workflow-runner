"""Envelope: ネスト path read/write・未定義 path・JSON 往復・深いコピー独立性。"""

import pytest

from gwr.envelope import Envelope, EnvelopePathError


def test_set_get_nested_path():
    env = Envelope()
    env.set("tables.summary", {"data": [{"a": 1}]})
    assert env.get("tables.summary") == {"data": [{"a": 1}]}
    assert env.get("tables.summary.data.0.a") == 1


def test_get_strips_dollar_root():
    env = Envelope()
    env.set("vars.msg", "hello")
    assert env.get("$.vars.msg") == "hello"
    assert env.has("$.vars.msg")


def test_get_undefined_path_raises():
    env = Envelope()
    with pytest.raises(EnvelopePathError):
        env.get("vars.nope")
    assert env.has("vars.nope") is False


def test_set_unknown_slot_rejected():
    env = Envelope()
    with pytest.raises(EnvelopePathError):
        env.set("bogus.x", 1)
    with pytest.raises(EnvelopePathError):
        env.set("", 1)


def test_set_autocreates_intermediate_dicts():
    env = Envelope()
    env.set("vars.a.b.c", 42)
    assert env.get("vars.a.b.c") == 42


def test_json_round_trip_equal():
    env = Envelope()
    env.set("tables.t", {"columns": ["x"], "row_count": 1, "data": [{"x": "あ"}]})
    env.set("vars.n", 3)
    env.add_usage({"input_tokens": 10, "output_tokens": 5})
    text = env.to_json()
    restored = Envelope.from_json(text)
    assert restored == env
    assert restored.to_json() == text  # 安定（sort_keys）


def test_set_stores_by_reference():
    # D1: set は参照保存（ホットパスの全コピー回避）。値は不変前提で扱う。
    payload = {"data": [{"a": 1}]}
    env = Envelope()
    env.set("tables.t", payload)
    assert env.get("tables.t") is payload  # deepcopy せず実体を保持


def test_from_dict_is_isolated_boundary():
    # 外部 dict からの構築は __init__ で deepcopy＝外向き隔離は境界で担保。
    data = {"vars": {"x": [1]}}
    env = Envelope(data)
    data["vars"]["x"].append(2)  # 外部変更
    assert env.get("vars.x") == [1]  # 影響を受けない


def test_deep_copy_independence_on_get():
    env = Envelope()
    env.set("vars.list", [1, 2, 3])
    got = env.to_dict()
    got["vars"]["list"].append(4)
    assert env.get("vars.list") == [1, 2, 3]  # to_dict は deepcopy 境界


def test_add_usage_accumulates():
    env = Envelope()
    env.add_usage({"input_tokens": 10})
    env.add_usage({"input_tokens": 5, "output_tokens": 2})
    assert env.usage == {"input_tokens": 15, "output_tokens": 2}


def test_init_from_partial_dict():
    env = Envelope({"vars": {"a": 1}})
    assert env.get("vars.a") == 1
    assert env.docs == {}  # 欠損スロットは空 dict
