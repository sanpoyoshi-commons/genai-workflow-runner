"""expr — 参照解決（JMESPath）と条件評価（CEL）。

- resolve(path, env): "$.tables.raw" 形式の参照を JMESPath で解決して生値を返す。
- evaluate(when, env): CEL 式を bool 評価する。許可演算のみ。禁止構文・式長超過・
  タイムアウト・未登録関数呼び出しは ExprError。

CEL を用いることで Python レベルの import / 任意属性アクセス / eval は構造的に不可能
（CEL は登録済み関数しか呼べないサンドボックス）。`$.` 前置は envelope ルート参照規約。
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import celpy
import jmespath
from celpy import celtypes, json_to_cel

MAX_EXPR_LEN = 500
MAX_DEPTH = 32  # 括弧ネストの上限（再帰的構造の暴走防止）
EVAL_TIMEOUT_SEC = 2.0

# when 内で呼べる関数の許可リスト（これ以外の name( は静的に拒否）。
# `matches`（正規表現）は celpy が Python re で評価し ReDoS の余地があるため許可しない
# （評価は 2s ソフトタイムアウトのみ＝スレッド強制終了不可）。文字列判定は
# startsWith/endsWith/contains で代替。必要なら RE2 等の安全評価を入れてから再追加する。
ALLOWED_FUNCTIONS = frozenset(
    {
        "len",
        "size",
        "int",
        "double",
        "string",
        "bool",
        "has",
        "startsWith",
        "endsWith",
        "contains",
    }
)

_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class ExprError(Exception):
    """参照／式評価の失敗（fail-closed 用）。"""


# --- 参照解決（JMESPath） -----------------------------------------------
def _to_jmespath(path: str) -> str:
    if path.startswith("$."):
        return path[2:]
    if path == "$":
        return "@"
    return path


def _env_data(env: Any) -> Any:
    """式評価用の読み取りデータ。Envelope は raw()（非deepcopyの読み取りビュー）を使い
    全体 deepcopy を回避（非破壊読み取りのみ・ノードは入力を変更しない前提）。dict は素通し。"""
    if hasattr(env, "raw"):
        return env.raw()
    if hasattr(env, "to_dict"):
        return env.to_dict()
    return env


@lru_cache(maxsize=512)
def _compiled_jmespath(jpath: str) -> Any:
    """JMESPath をコンパイルしてキャッシュ（同一参照式の再コンパイルを避ける）。"""
    return jmespath.compile(jpath)


def resolve(path: str, env: Any) -> Any:
    """env（Envelope or dict）から path を JMESPath で解決して返す。"""
    data = _env_data(env)
    try:
        compiled = _compiled_jmespath(_to_jmespath(path))
    except Exception as e:  # JMESPath 構文エラー
        raise ExprError(f"不正な参照式: {path!r}: {e}") from e
    return compiled.search(data)


def compile_reference(path: str) -> None:
    """参照式が JMESPath としてコンパイル可能かだけ検査（静的検証用）。"""
    try:
        _compiled_jmespath(_to_jmespath(path))
    except Exception as e:
        raise ExprError(f"不正な参照式: {path!r}: {e}") from e


# --- 条件評価（CEL） -----------------------------------------------------
def _cel_len(item: Any) -> celtypes.IntType:
    return celtypes.IntType(len(item))


_CUSTOM_FUNCS = {"len": _cel_len}


def _scan_functions(src: str) -> set[str]:
    """`name(` パターンを抽出して呼ばれる関数名の集合を返す（簡易・値フリー）。"""
    names: set[str] = set()
    i = 0
    n = len(src)
    while i < n:
        if src[i] == "(":
            j = i - 1
            while j >= 0 and src[j] in _IDENT_CHARS:
                j -= 1
            name = src[j + 1 : i]
            # メンバ呼び出し（".foo(")も name 部分のみ拾う。数値直前は除外。
            if name and not name[0].isdigit():
                names.add(name)
        i += 1
    return names


def _max_paren_depth(src: str) -> int:
    depth = max_depth = 0
    for ch in src:
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth -= 1
    return max_depth


def _preprocess(src: str) -> str:
    """`$.` ルート前置を CEL のドット参照へ変換（"$.a.b" -> "a.b"）。"""
    return src.replace("$.", "")


def _static_checks(src: str) -> str:
    if len(src) > MAX_EXPR_LEN:
        raise ExprError(f"式が長すぎる（>{MAX_EXPR_LEN}）")
    pre = _preprocess(src)
    if _max_paren_depth(pre) > MAX_DEPTH:
        raise ExprError(f"ネストが深すぎる（>{MAX_DEPTH}）")
    bad = _scan_functions(pre) - ALLOWED_FUNCTIONS
    if bad:
        raise ExprError(f"禁止された関数呼び出し: {sorted(bad)}")
    return pre


@lru_cache(maxsize=512)
def compile_condition(when: str) -> celpy.Runner:
    """when の CEL をコンパイルして Runner を返す（評価せずコンパイル検証）。同一式はキャッシュ。

    返す program は文脈非依存（program.evaluate(ctx) で都度評価）なので使い回せる。
    不正式（_static_checks/compile が ExprError）は lru_cache に載らず毎回再検査される。
    """
    pre = _static_checks(when)
    env = celpy.Environment()
    try:
        ast = env.compile(pre)
    except Exception as e:  # celpy.CELParseError 等
        raise ExprError(f"CEL コンパイル失敗: {when!r}: {e}") from e
    return env.program(ast, functions=_CUSTOM_FUNCS)  # type: ignore[arg-type]


def _referenced_slots_subset(when: str, data: Any) -> Any:
    """when が参照する top-level キーだけを抜き出す（json_to_cel に Envelope 全体＝大テーブル等を
    渡さないための絞り込み）。参照されるルートは式中に語として必ず現れるため、部分文字列一致で
    取りこぼさない（過剰包含は安全＝余分なスロットを変換するだけ）。"""
    if not isinstance(data, dict):
        return data
    pre = _preprocess(when)
    return {k: v for k, v in data.items() if k in pre}


def evaluate(when: str, env: Any) -> bool:
    """when を bool 評価。許可演算のみ・タイムアウト付き。"""
    program = compile_condition(when)
    data = _env_data(env)
    ctx = json_to_cel(_referenced_slots_subset(when, data))

    result_box: dict[str, Any] = {}

    def _run() -> None:
        try:
            result_box["value"] = program.evaluate(ctx)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001 — CELEvalError 含む
            result_box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(EVAL_TIMEOUT_SEC)
    if t.is_alive():
        raise ExprError(f"式評価タイムアウト（>{EVAL_TIMEOUT_SEC}s）: {when!r}")
    if "error" in result_box:
        raise ExprError(f"式評価エラー: {when!r}: {result_box['error']}")

    value = result_box.get("value")
    if not isinstance(value, (bool, celtypes.BoolType)):
        raise ExprError(f"条件式が bool を返さない: {when!r} -> {value!r}")
    return bool(value)
