"""実行時の理由コード（`error.reason`）と利用者向けメッセージ・docs の突合。

理由コードの系統は 2 つある。**混同しない。**

- **検証**：`ValidationReport.add()` → `Issue`。`validate` / `flow.validate` / MCP の
  `gwr_validate` が返す。docs は `docs/cli.md` の一覧で、突合は tests/test_reason_codes.py。
- **実行時**：`ContractError` / `NodeError` / `DataError`。`invoke` が `error.reason` に載せ、
  `app._USER_MESSAGES` が利用者向け文言へ写す。docs は `docs/flow-toml.md`。
  **本ファイルが突合する。**

固定する不変条件は 3 つ。

1. 実行時の理由コードは `raise` の第1引数に**文字列リテラル**で書く（機械抽出できるように）。
2. `_USER_MESSAGES` に**死んだエントリが無い**（実装で raise されないキーを持たない）。
3. `_USER_MESSAGES` のキー集合と `docs/flow-toml.md` の表が**完全一致**する。
"""

import ast
import re
from pathlib import Path

import pytest

from gwr.app import _USER_MESSAGES, render_user_message

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gwr"
DOCS = ROOT / "docs" / "flow-toml.md"

HEADING = "#### 実行時の理由コード一覧"
# 実行時エラーを表す例外。第1引数が reason コード。
# `RunnerError` は `invoke` が `error.reason` に載せる**最後の経路**なので必ず含める
# （2026-09-20 に追加。当初 3 つだけを見ていて、reason に日本語の文章や `f"CODE: 詳細"`
# が入っていたのを取りこぼしていた）。
RUNTIME_EXCEPTIONS = {"ContractError", "NodeError", "DataError", "RunnerError"}

# `RUNTIME_EXCEPTIONS` のうち、src に raise が 1 件も無くても残してよいもの。
# **理由の文字列が必須**（名前だけの登録は下のテストが落とす）。理由を書けないなら、
# それは死んだエントリなので `RUNTIME_EXCEPTIONS` から外す。
EXTENSION_POINT_EXCEPTIONS = {
    "DataError": (
        "src に raise は無いが、カスタムノード向けの拡張点として生きている。"
        "runner._run_node が DataError を個別に捕まえ、on_error（skip/default）と "
        "coordinates（行・列）を扱う。tests/conftest.py の擬似ノードが投げて経路を固定している。"
    ),
}

# 個別メッセージを持たず汎用文言（「処理を完了できませんでした。」）へ落とすもの。
# いずれも**フロー作成者向けの誤り**で、利用者に個別の説明をしても直せない。
# 新しい理由コードを足したら、ここへ加えるか `_USER_MESSAGES` に文言を足すかを選ぶ
# （どちらもしないとこのテストが落ちる＝判断を忘れないための forcing function）。
GENERIC_MESSAGE_REASONS = {
    # 入力契約・出力写像の宣言ミス（フロー TOML の誤り）
    "INPUT_MISSING_KEY",
    "INPUT_MISSING_ITEMS",
    "INPUT_UNKNOWN_TYPE",
    "OUTPUT_MISSING_KEY",
    "OUTPUT_MISSING_FROM",
    # ノードへ渡る値の型・必須パラメータの誤り（フロー TOML の誤り）
    "ADAPTER_MISSING",
    "FILE_MISSING",
    "TABLE_MISSING",
    "TEXT_MISSING",
    "QUERY_MISSING",
    "INSTRUCTION_MISSING",
    "UNSUPPORTED_FORMAT",
    "SCHEMA_MISMATCH",
    # file.transform の ops 指定ミス
    "BAD_OP",
    "UNKNOWN_OP",
    "MISSING_COLUMN",
    # flow.validate の入力ミス・上限
    "FLOW_TOML_MISSING",
    "FLOW_TOML_TOO_LARGE",
    # 巨大ファイル保護（利用者には汎用文言。詳細は運用側で見る）
    "XLSX_TOO_LARGE",
    # Runner が止めるもの（フロー定義の誤り・保険の上限）
    "STEP_LIMIT_EXCEEDED",
    "BRANCH_EVAL_FAILED",
    "UNKNOWN_NODE_TYPE",
    "FOREACH_NOT_LIST",
    "FOREACH_LIMIT_EXCEEDED",
}


def _raise_sites() -> list[tuple[str, int, ast.Call]]:
    """`raise <RuntimeException>("<REASON>", ...)` を src 全体から拾う。"""
    sites: list[tuple[str, int, ast.Call]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in RUNTIME_EXCEPTIONS and node.exc.args:
                sites.append((path.name, node.lineno, node.exc))
    return sites


def _is_passthrough(node: ast.expr) -> bool:
    """`raise RunnerError(e.reason, ...)` のような再送出か。

    ノード／入力契約の理由コードをそのまま載せ直すだけで、新しいコードを作らない。
    """
    return isinstance(node, ast.Attribute) and node.attr == "reason"


def implementation_reasons() -> set[str]:
    reasons: set[str] = set()
    for name, lineno, call in _raise_sites():
        reason = call.args[0]
        if _is_passthrough(reason):
            continue
        # リテラルでない（f-string 等）と機械抽出できず突合が抜ける。
        # `f"CODE: {詳細}"` を禁じるのもここ＝詳細は detail 引数へ分ける。
        assert isinstance(reason, ast.Constant) and isinstance(reason.value, str), (
            f"{name}:{lineno} 実行時エラーの reason は文字列リテラルで書くこと"
            "（詳細は detail 引数へ分ける。docs/flow-toml.md との突合テストが全数を拾えなくなる）"
        )
        reasons.add(reason.value)
    return reasons


def test_reason_codes_are_upper_snake_case():
    """理由コードは機械可読な体系。日本語の文章を入れない。"""
    bad = [r for r in implementation_reasons() if not re.fullmatch(r"[A-Z][A-Z0-9_]*", r)]
    assert not bad, f"大文字スネークでない理由コード: {bad}（日本語の文章は detail へ）"


def documented_rows() -> list[tuple[str, str, str, str]]:
    """docs/flow-toml.md の表を (reason, 出る場面, メッセージ, 直し方) で返す。"""
    lines = DOCS.read_text("utf-8").splitlines()
    start = lines.index(HEADING)
    rows: list[tuple[str, str, str, str]] = []
    for line in lines[start + 1 :]:
        if re.match(r"^#{2,4} ", line):
            break
        m = re.match(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|(.*)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        assert len(cells) == 3, f"{m.group(1)} の列数が 4 でない"
        rows.append((m.group(1), *cells))
    return rows


# --- 1. 抽出できていること ----------------------------------------------
def test_reasons_are_extractable():
    assert implementation_reasons(), "実装から実行時の理由コードを1つも抽出できていない"


# --- 2. 死んだエントリが無いこと -----------------------------------------
def test_no_dead_user_messages():
    implemented = implementation_reasons()
    dead = set(_USER_MESSAGES) - implemented
    assert not dead, f"raise されないのに文言だけある: {sorted(dead)}"


# --- 3. docs と完全一致すること ------------------------------------------
def test_user_messages_are_documented():
    documented = {r[0] for r in documented_rows()}
    assert documented == set(_USER_MESSAGES), (
        f"docs に無い実装コード: {sorted(set(_USER_MESSAGES) - documented)} / "
        f"実装に無い docs コード: {sorted(documented - set(_USER_MESSAGES))}"
    )


@pytest.mark.parametrize("row", documented_rows(), ids=lambda r: r[0])
def test_documented_row_is_complete(row):
    reason, situation, message, how_to_fix = row
    assert situation, f"{reason}: 出る場面が空"
    assert message == _USER_MESSAGES[reason], (
        f"{reason}: docs のメッセージが実装と違う "
        f"（docs={message!r} / 実装={_USER_MESSAGES[reason]!r}）"
    )
    assert len(how_to_fix) >= 10, f"{reason}: 直し方が空か短すぎる"


# --- 4. 汎用文言へ落とすものが分類済みであること -------------------------
def test_every_reason_is_classified():
    """新しい理由コードを足したら、文言を付けるか汎用と決めるかを選ばせる。"""
    implemented = implementation_reasons()
    classified = set(_USER_MESSAGES) | GENERIC_MESSAGE_REASONS
    unclassified = implemented - classified
    assert not unclassified, (
        f"分類されていない理由コード: {sorted(unclassified)}。"
        "利用者が自力で直せるなら app._USER_MESSAGES と docs/flow-toml.md の表に足す。"
        "フロー作成者向けの誤りなら本テストの GENERIC_MESSAGE_REASONS に足す。"
    )


def test_no_dead_runtime_exception_classes():
    """raise 0 件の例外クラスを、理由つきで登録しない限り許さない。

    `_USER_MESSAGES` 側には「死んだエントリが無い」検査があるのにクラス側には無く、
    `DataError`（raise 0 件）が誰にも気づかれないまま残っていた（2026-09-21）。
    その非対称を埋める。
    """
    raised = {
        (call.func.id if isinstance(call.func, ast.Name) else call.func.attr)
        for _name, _lineno, call in _raise_sites()
    }
    dead = RUNTIME_EXCEPTIONS - raised
    unexplained = sorted(dead - set(EXTENSION_POINT_EXCEPTIONS))
    assert not unexplained, (
        f"src で raise されない例外クラス: {unexplained}。"
        "拡張点として残すなら EXTENSION_POINT_EXCEPTIONS に理由つきで登録し、"
        "そうでなければ RUNTIME_EXCEPTIONS から外すこと"
    )
    stale = sorted(set(EXTENSION_POINT_EXCEPTIONS) - dead)
    assert not stale, f"raise されるようになったのに拡張点として登録されたまま: {stale}"


@pytest.mark.parametrize("name", sorted(EXTENSION_POINT_EXCEPTIONS))
def test_extension_point_registration_has_a_reason(name):
    """名前だけの登録では通さない（なぜ残すのかを書かせる）。"""
    reason = EXTENSION_POINT_EXCEPTIONS[name]
    assert isinstance(reason, str) and len(reason.strip()) >= 20, (
        f"{name}: 拡張点として残す理由を 20 文字以上で書くこと"
    )
    assert name in RUNTIME_EXCEPTIONS, f"{name}: RUNTIME_EXCEPTIONS に無いものは登録しない"


def test_generic_list_has_no_stale_entries():
    stale = GENERIC_MESSAGE_REASONS - implementation_reasons()
    assert not stale, f"実装に無いのに汎用一覧に残っている: {sorted(stale)}"


def test_generic_and_individual_do_not_overlap():
    overlap = GENERIC_MESSAGE_REASONS & set(_USER_MESSAGES)
    assert not overlap, f"個別文言と汎用一覧の両方にある: {sorted(overlap)}"


# --- 5. 汎用文言そのもの --------------------------------------------------
@pytest.mark.parametrize("reason", sorted(GENERIC_MESSAGE_REASONS))
def test_generic_reasons_fall_back_to_the_generic_message(reason):
    assert render_user_message(reason, None) == "処理を完了できませんでした。"


def test_individual_reasons_do_not_fall_back():
    for reason in _USER_MESSAGES:
        assert render_user_message(reason, None) != "処理を完了できませんでした。"
