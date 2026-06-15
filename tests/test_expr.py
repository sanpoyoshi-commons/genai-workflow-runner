"""expr: CEL（比較・論理・len 等／禁止構文・タイムアウト・深さ）と JMESPath。"""

import pytest

from gwr.envelope import Envelope
from gwr.expr import (
    ExprError,
    _referenced_slots_subset,
    compile_condition,
    compile_reference,
    evaluate,
    resolve,
)


def _env():
    env = Envelope()
    env.set("tables.summary", {"data": [{"部署": "A", "売上": 10}]})
    env.set("vars.n", 3)
    env.set("vars.flag", True)
    env.set("vars.name", "report.xlsx")
    return env


# --- JMESPath resolve ---------------------------------------------------
def test_resolve_existing_path():
    assert resolve("$.vars.n", _env()) == 3
    assert resolve('$.tables.summary.data[0]."売上"', _env()) == 10


def test_resolve_nonexistent_returns_none():
    assert resolve("$.vars.missing", _env()) is None


def test_resolve_array_projection():
    env = Envelope()
    env.set("tables.t", {"data": [{"x": 1}, {"x": 2}]})
    assert resolve("$.tables.t.data[*].x", env) == [1, 2]


def test_compile_reference_rejects_bad_jmespath():
    with pytest.raises(ExprError):
        compile_reference("$.[[[bad")


# --- CEL evaluate -------------------------------------------------------
def test_evaluate_comparison_and_logic():
    env = _env()
    assert evaluate("vars.n == 3", env) is True
    assert evaluate("vars.n > 5 || vars.flag", env) is True
    assert evaluate("vars.n > 5 && vars.flag", env) is False


def test_evaluate_len_function_with_dollar_root():
    env = _env()
    assert evaluate("len($.tables.summary.data) == 0", env) is False
    env.set("tables.summary", {"data": []})
    assert evaluate("len($.tables.summary.data) == 0", env) is True


def test_evaluate_string_helpers():
    assert evaluate('vars.name.endsWith(".xlsx")', _env()) is True


def test_evaluate_forbidden_function_rejected():
    with pytest.raises(ExprError):
        evaluate("__import__('os')", _env())
    with pytest.raises(ExprError):
        compile_condition("danger(vars.n)")


def test_evaluate_matches_rejected_redos_hardening():
    # matches は ReDoS の余地があるため許可リストから除外（startsWith/endsWith/contains で代替）。
    with pytest.raises(ExprError):
        compile_condition('vars.name.matches(".*")')


# --- パフォーマンス（deepcopy 廃止 / コンパイルキャッシュ / 参照スロット絞り込み） ----------
def test_resolve_returns_live_reference_no_copy():
    # 参照解決は内部データを deepcopy せず実体を返す（高速読み取り経路）。
    env = Envelope()
    env.set("tables.t", {"data": [{"x": 1}]})
    got = resolve("$.tables.t", env)
    assert got is env.raw()["tables"]["t"]


def test_compile_condition_is_cached():
    assert compile_condition("vars.n == 3") is compile_condition("vars.n == 3")


def test_referenced_slots_subset_excludes_unreferenced():
    data = {"vars": {"n": 1}, "tables": {"big": [1, 2, 3]}, "docs": {}}
    sub = _referenced_slots_subset("len($.vars) == 0", data)
    assert "vars" in sub and "tables" not in sub  # 参照されない tables は CEL 変換対象から除外


def test_evaluate_correct_with_unreferenced_big_slot():
    # 参照されない大スロットがあっても評価は正しい（絞り込みが正しさを壊さない）。
    env = Envelope()
    env.set("vars.docs", [])
    env.set("tables.big", {"data": [{"x": i} for i in range(5)]})
    assert evaluate("len($.vars.docs) == 0", env) is True


def test_evaluate_too_long_rejected():
    with pytest.raises(ExprError):
        evaluate("vars.n == " + "1" * 600, _env())


def test_evaluate_deep_nesting_rejected():
    expr = "(" * 40 + "vars.n" + ")" * 40 + " == 3"
    with pytest.raises(ExprError):
        evaluate(expr, _env())


def test_evaluate_non_bool_rejected():
    with pytest.raises(ExprError):
        evaluate("vars.n + 1", _env())


def test_compile_condition_rejects_uncompilable():
    with pytest.raises(ExprError):
        compile_condition("vars.n ==")
