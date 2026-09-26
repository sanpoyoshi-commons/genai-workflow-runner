"""理由コード（Issue.reason）の実装と docs/cli.md の突合。

`gwr validate --json` の `reason` は後続（MCP）から機械的に参照されるため、
実装の定数集合と docs の一覧表が一致していることをテストで固定する。
片方だけ増減するとここが落ちる。

**種別（error / warning）も突合する。** 警告は `ok` を落とさない＝直さなくても実行できる
ので、docs で error と混ざると読み手が優先順位を誤る。実装の `severity=` 引数が正。
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gwr"
DOCS = ROOT / "docs" / "cli.md"

CATEGORIES = {"static", "dataflow", "type"}
SEVERITIES = {"error", "warning"}
HEADING = "### 理由コード一覧"


def _severity_of(call: ast.Call) -> str:
    """`report.add(..., severity="warning")` の指定を読む（既定は error）。

    キーワード専用引数なので位置引数の並びは動かない（第 3 引数＝reason のまま）。
    """
    for keyword in call.keywords:
        if keyword.arg == "severity":
            assert isinstance(keyword.value, ast.Constant), (
                "severity も文字列リテラルで書くこと（docs との突合が拾えなくなる）"
            )
            return str(keyword.value.value)
    return "error"


def _add_call_sites() -> list[tuple[str, int, ast.Call]]:
    """`<report>.add("<category>", ...)` の呼び出しを src 全体から拾う。"""
    sites: list[tuple[str, int, ast.Call]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in CATEGORIES:
                sites.append((path.name, node.lineno, node))
    return sites


def implementation_severities() -> dict[str, str]:
    """理由コード → 種別。同じコードを違う種別で書いていたらそこで落とす。"""
    severities: dict[str, str] = {}
    for name, lineno, call in _add_call_sites():
        assert len(call.args) >= 3, f"{name}:{lineno} report.add の引数が足りない"
        reason = call.args[2]
        # リテラルでない（f-string 等）と機械抽出できず docs 突合が抜ける。
        assert isinstance(reason, ast.Constant) and isinstance(reason.value, str), (
            f"{name}:{lineno} reason は文字列リテラルで書くこと"
            "（docs/cli.md との突合テストが全数を拾えなくなる）"
        )
        severity = _severity_of(call)
        previous = severities.setdefault(reason.value, severity)
        assert previous == severity, (
            f"{name}:{lineno} {reason.value} が error と warning の両方で使われている"
        )
    return severities


def implementation_reasons() -> set[str]:
    return set(implementation_severities())


def documented_rows() -> list[tuple[str, str, str, str, str]]:
    """docs/cli.md の理由コード一覧表を (reason, カテゴリ, 種別, 意味, 直し方) で返す。"""
    lines = DOCS.read_text("utf-8").splitlines()
    start = lines.index(HEADING)
    rows: list[tuple[str, str, str, str, str]] = []
    for line in lines[start + 1 :]:
        if re.match(r"^#{2,3} ", line):
            break
        m = re.match(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|(.*)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        assert len(cells) == 4, f"{m.group(1)} の列数が 5 でない"
        rows.append((m.group(1), *cells))
    return rows


def test_reason_codes_are_documented():
    documented = {r[0] for r in documented_rows()}
    implemented = implementation_reasons()
    assert implemented, "実装から理由コードを1つも抽出できていない"
    assert implemented == documented, (
        f"docs に無い実装コード: {sorted(implemented - documented)} / "
        f"実装に無い docs コード: {sorted(documented - implemented)}"
    )


@pytest.mark.parametrize("row", documented_rows(), ids=lambda r: r[0])
def test_documented_row_is_complete(row):
    reason, category, severity, meaning, how_to_fix = row
    assert category == f"`{category.strip('`')}`" and category.strip("`") in CATEGORIES
    assert severity.strip("`") in SEVERITIES, f"{reason}: 種別は error か warning"
    assert meaning, f"{reason}: 意味が空"
    # 「直し方」列は AI の自己修正の材料になるため必ず埋める。
    assert len(how_to_fix) >= 10, f"{reason}: 直し方が空か短すぎる"


def test_documented_severity_matches_the_implementation():
    """警告として実装したものが docs で error に見えていないこと（逆も）。"""
    implemented = implementation_severities()
    documented = {r[0]: r[2].strip("`") for r in documented_rows()}
    mismatched = {
        k: (v, documented.get(k)) for k, v in implemented.items() if v != documented.get(k)
    }
    assert not mismatched, f"実装と docs で種別が違う（実装, docs）: {mismatched}"
