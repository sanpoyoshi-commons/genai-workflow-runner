"""Runner: 直列順序・branch分岐・foreach反復/空/集約・未到達skip・on_error・
失敗時ステートレス・dry_run（静的/データフロー/型）。"""

import pytest

from gwr.envelope import Envelope
from gwr.runner import Runner, RunnerError
from tests.conftest import make_test_registry


def _runner():
    return Runner(make_test_registry())


# --- 直列・ジャンプ -----------------------------------------------------
def test_serial_order_and_literal_input():
    steps = [
        {"id": "a", "type": "test.echo", "in": {"value": "hello"}, "out": "vars.a"},
        {"id": "b", "type": "test.echo", "in": {"value": "$.vars.a"}, "out": "vars.b"},
    ]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.a") == "hello"
    assert env.get("vars.b") == "hello"


def test_explicit_next_jump_skips_unreached():
    steps = [
        {"id": "a", "type": "test.echo", "in": {"value": 1}, "out": "vars.a", "next": "c"},
        {"id": "b", "type": "test.echo", "in": {"value": 2}, "out": "vars.b"},
        {"id": "c", "type": "test.echo", "in": {"value": 3}, "out": "vars.c"},
    ]
    env = _runner().run(steps, Envelope())
    assert env.has("vars.a") and env.has("vars.c")
    assert not env.has("vars.b")  # 未到達 step は実行されない


# --- branch -------------------------------------------------------------
def test_branch_true_then_false_else():
    steps = [
        {"id": "seed", "type": "test.echo", "in": {"value": 0}, "out": "vars.n"},
        {"id": "gate", "type": "branch", "when": "vars.n == 0", "then": "t", "else": "f"},
        {"id": "t", "type": "test.echo", "in": {"value": "T"}, "out": "vars.r", "next": "end"},
        {"id": "f", "type": "test.echo", "in": {"value": "F"}, "out": "vars.r"},
        {"id": "end", "type": "test.echo", "in": {"value": "done"}, "out": "vars.end"},
    ]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.r") == "T"

    steps[0]["in"]["value"] = 1
    env2 = _runner().run(steps, Envelope())
    assert env2.get("vars.r") == "F"


# --- foreach ------------------------------------------------------------
def test_foreach_iterates_and_aggregates():
    steps = [
        {"id": "seed", "type": "test.echo", "in": {"value": [1, 2, 3]}, "out": "vars.nums"},
        {
            "id": "loop",
            "type": "foreach",
            "foreach": "$.vars.nums",
            "as": "x",
            "body": ["inc"],
            "collect": "$.vars.incd",
            "out": "vars.result",
        },
        {"id": "inc", "type": "test.addone", "in": {"n": "$.vars.x"}, "out": "vars.incd"},
        {"id": "after", "type": "test.echo", "in": {"value": "ok"}, "out": "vars.after"},
    ]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.result") == [2, 3, 4]
    assert env.get("vars.after") == "ok"  # body step はループ後の直線走査で再実行されない


def test_foreach_empty_runs_zero_times():
    steps = [
        {"id": "seed", "type": "test.echo", "in": {"value": []}, "out": "vars.nums"},
        {
            "id": "loop",
            "type": "foreach",
            "foreach": "$.vars.nums",
            "as": "x",
            "body": ["inc"],
            "collect": "$.vars.incd",
            "out": "vars.result",
        },
        {"id": "inc", "type": "test.addone", "in": {"n": "$.vars.x"}, "out": "vars.incd"},
    ]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.result") == []
    assert not env.has("vars.incd")


def _foreach_steps():
    return [
        {"id": "seed", "type": "test.echo", "in": {"value": [1, 2, 3]}, "out": "vars.nums"},
        {"id": "loop", "type": "foreach", "foreach": "$.vars.nums", "as": "x",
         "body": ["inc"], "collect": "$.vars.incd", "out": "vars.result"},
        {"id": "inc", "type": "test.addone", "in": {"n": "$.vars.x"}, "out": "vars.incd"},
    ]


def test_foreach_limit_exceeded_fails_closed():
    # seq は入力由来になり得る → 反復上限超過で fail-closed（無制限消費＝DoS 防止）。
    r = Runner(make_test_registry(), max_foreach_items=2)
    with pytest.raises(RunnerError) as ei:
        r.run(_foreach_steps(), Envelope())
    # reason は理由コードのみ。詳細は detail へ分ける（tests/test_user_messages.py が固定）。
    assert ei.value.reason == "FOREACH_LIMIT_EXCEEDED"
    assert ei.value.detail == "3 > 2"


def test_foreach_within_limit_ok():
    env = Runner(make_test_registry(), max_foreach_items=3).run(_foreach_steps(), Envelope())
    assert env.get("vars.result") == [2, 3, 4]


def test_foreach_item_binding_is_copied_not_aliased():
    # set 参照保存後も、foreach の item 束縛は seq の実体をエイリアスしない（明示 deepcopy）。
    items = [{"k": 1}, {"k": 2}]
    steps = [
        {"id": "seed", "type": "test.echo", "in": {"value": items}, "out": "vars.nums"},
        {"id": "loop", "type": "foreach", "foreach": "$.vars.nums", "as": "x",
         "body": ["e"], "out": "vars.r"},
        {"id": "e", "type": "test.echo", "in": {"value": "$.vars.x"}, "out": "vars.last"},
    ]
    env = _runner().run(steps, Envelope())
    nums = env.get("vars.nums")
    assert env.get("vars.x") == nums[-1]  # 値は等しいが
    assert env.get("vars.x") is not nums[-1]  # 別オブジェクト（エイリアスしない）


def test_on_error_default_is_copied_not_aliased_to_flow():
    # on_error=default の既定値はフロー定義(step['default'])をエイリアスしない（明示 deepcopy）。
    default_obj = {"v": 1}
    steps = [{"id": "a", "type": "test.boom", "in": {}, "out": "vars.x",
              "on_error": "default", "default": default_obj}]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.x") == {"v": 1}
    assert env.get("vars.x") is not default_obj


# --- on_error -----------------------------------------------------------
def test_on_error_fail_raises_and_is_stateless():
    steps = [
        {"id": "ok", "type": "test.echo", "in": {"value": 1}, "out": "vars.ok"},
        {"id": "bad", "type": "test.boom", "in": {"value": 1}, "out": "vars.bad"},
    ]
    env = Envelope()
    with pytest.raises(RunnerError) as ei:
        _runner().run(steps, env)
    assert ei.value.reason == "BOOM"
    # fail-fast：失敗 step の out は書かれない（呼び出し側が Envelope を破棄する前提）
    assert not env.has("vars.bad")


def test_on_error_skip_continues():
    steps = [
        {"id": "bad", "type": "test.boom", "in": {"value": 1}, "out": "vars.bad",
         "on_error": "skip"},
        {"id": "ok", "type": "test.echo", "in": {"value": 2}, "out": "vars.ok"},
    ]
    env = _runner().run(steps, Envelope())
    assert not env.has("vars.bad")
    assert env.get("vars.ok") == 2


def test_on_error_default_writes_default():
    steps = [
        {"id": "bad", "type": "test.boom", "in": {"value": 1}, "out": "vars.bad",
         "on_error": "default", "default": -1},
    ]
    env = _runner().run(steps, Envelope())
    assert env.get("vars.bad") == -1


def test_data_error_carries_coordinates_no_value():
    steps = [{"id": "d", "type": "test.databoom", "in": {"value": 1}, "out": "vars.d"}]
    with pytest.raises(RunnerError) as ei:
        _runner().run(steps, Envelope())
    assert ei.value.coordinates == {"row": 42, "col": 2, "reason": "NOT_NUMERIC"}


# --- dry_run ------------------------------------------------------------
def test_dry_run_clean_flow_ok():
    steps = [
        {"id": "t", "type": "test.table", "out": "tables.raw"},
        {"id": "c", "type": "test.needs_table", "in": {"table": "$.tables.raw"},
         "out": "vars.n"},
    ]
    report = _runner().dry_run(steps)
    assert report.ok, report.issues


def test_dry_run_duplicate_id_and_unknown_type():
    steps = [
        {"id": "a", "type": "test.echo", "in": {"value": 1}, "out": "vars.a"},
        {"id": "a", "type": "nope.node", "in": {"value": 1}, "out": "vars.b"},
    ]
    report = _runner().dry_run(steps)
    reasons = {i.reason for i in report.by_category("static")}
    assert "DUPLICATE_ID" in reasons
    assert "UNKNOWN_NODE_TYPE" in reasons


def test_dry_run_branch_unresolved_target():
    steps = [
        {"id": "g", "type": "branch", "when": "vars.x == 1", "then": "missing", "else": "g"},
    ]
    report = _runner().dry_run(steps)
    assert any(i.reason == "BRANCH_THEN_UNRESOLVED" for i in report.by_category("static"))


def test_dry_run_uncompilable_when():
    steps = [{"id": "g", "type": "branch", "when": "vars.x ==", "then": "g", "else": "g"}]
    report = _runner().dry_run(steps)
    assert any(i.reason == "BRANCH_WHEN_UNCOMPILABLE" for i in report.by_category("static"))


def test_dry_run_read_before_write():
    steps = [
        {"id": "c", "type": "test.needs_table", "in": {"table": "$.tables.ghost"},
         "out": "vars.n"},
    ]
    report = _runner().dry_run(steps)
    assert any(i.reason == "READ_BEFORE_WRITE" for i in report.by_category("dataflow"))


def test_dry_run_type_mismatch():
    # scalar を産出した slot を TableRef 要求ノードへ渡す
    steps = [
        {"id": "e", "type": "test.echo", "in": {"value": 1}, "out": "tables.raw"},
        {"id": "c", "type": "test.needs_table", "in": {"table": "$.tables.raw"},
         "out": "vars.n"},
    ]
    report = _runner().dry_run(steps)
    assert any(i.reason == "TYPE_MISMATCH" for i in report.by_category("type"))


def test_dry_run_clean_with_initial_slots():
    steps = [
        {"id": "c", "type": "test.needs_table", "in": {"table": "$.files.data"},
         "out": "vars.n"},
    ]
    report = _runner().dry_run(steps, initial_slots={"files.data": "TableRef"})
    assert report.ok, report.issues
