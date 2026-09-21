"""同梱データ（gwr/_data）と原本（docs/ ・examples/）の一致を固定する。

仕様 1 枚と例は MCP から配るためパッケージに同梱するが、原本は docs/ と examples/ の
まま（そちらが正）。両方を直さないとここが落ちる。
"""

from pathlib import Path

import pytest

from gwr import assets

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "flow-toml-for-ai.md"
EXAMPLES = ROOT / "examples"

_FIX = "src/gwr/_data/ へ原本をコピーし直すこと（cp docs/flow-toml-for-ai.md examples/*.toml）"


def test_spec_matches_docs():
    assert assets.spec_markdown() == SPEC.read_text("utf-8"), _FIX


def test_example_names_match_repo():
    assert assets.example_names() == sorted(p.name for p in EXAMPLES.glob("*.toml")), _FIX


@pytest.mark.parametrize("name", assets.example_names())
def test_example_text_matches_repo(name):
    assert assets.example_text(name) == (EXAMPLES / name).read_text("utf-8"), _FIX


@pytest.mark.parametrize("name", assets.example_names())
def test_every_example_has_a_description(name):
    assert len(assets.example_description(name)) >= 10, f"{name} の 1 行説明が無いか短すぎる"


def test_descriptions_have_no_stale_entries():
    assert set(assets.EXAMPLE_DESCRIPTIONS) == set(assets.example_names())


# --- 名前の解決はモジュール内に閉じている（パスとして解釈しない） ---


@pytest.mark.parametrize(
    "name",
    [
        "../flow-toml-for-ai.md",
        "../../cli.py",
        "examples/poc.toml",
        "/etc/passwd",
        "poc.toml\x00",
        "",
        "poc",
        "POC.TOML",
    ],
)
def test_example_text_rejects_anything_but_a_listed_name(name):
    with pytest.raises(KeyError):
        assets.example_text(name)


def test_only_toml_is_listed():
    """examples/ には .toml 以外（.txt）もあるが列挙には出さない。"""
    assert any(p.suffix != ".toml" for p in EXAMPLES.iterdir()), "前提: 非 .toml が examples にある"
    assert all(n.endswith(".toml") for n in assets.example_names())
