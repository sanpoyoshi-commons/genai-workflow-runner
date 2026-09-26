"""assets — パッケージに同梱した読み取り専用データへのアクセス。

同梱物は 2 つ：
- フロー TOML の仕様 1 枚（`docs/flow-toml-for-ai.md` の同梱コピー）
- フロー例（`examples/*.toml` の同梱コピー）

リポジトリの外（wheel からのインストール先）でも読めるよう `importlib.resources`
で `gwr/_data/` から取る。原本との一致は tests/test_assets.py が固定する
（片方だけ直すと落ちる）。

不変条件：**外から受けたパスでファイルを開かない。** 公開するのは
`example_names()` が返す名前だけで、名前→パスの解決はこのモジュール内に閉じる。
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

_DATA = "_data"
_EXAMPLES = "examples"

# 各例が「何をする例か」の 1 行説明。近い例を引いてから書けるようにするための索引で、
# 例を増やしたらここにも 1 行足す（tests/test_assets.py が欠落を落とす）。
EXAMPLE_DESCRIPTIONS: dict[str, str] = {
    "hello.toml": (
        "委譲なしの最小フロー。steps なしで hidden 入力の既定値をそのまま text 出力に返す。"
    ),
    "poc.toml": (
        "file.load → file.transform（groupby 集計）→ llm 要約 → branch → file.write（xlsx）。"
        "4 要素・3 スロット名前空間・分岐・artifacts 出力が一通り入った実用例。"
    ),
    "llm-smoke.toml": "質問 → llm → 回答 の最小 1 ステップ。llm ノードの in/out の形を見る用。",
    "rag-smoke.toml": (
        "retrieval → branch（該当あり/なし）→ llm の複数ノード例。"
        "検索結果が空かどうかで分岐する書き方。"
    ),
    "ci-smoke.toml": "指示＋入力ファイル → code_interpreter → 生成 artifacts と分析テキスト。",
    "judge-smoke.toml": (
        "judge → branch → llm。choice・noul・score の 3 型の問いを書き、答えと確信度で "
        "分岐する例。criteria の表形式、閾値を仮の値として書く作法、分岐ごとに別の出力キーへ"
        "書いて倒れた先を見せる作法が入っている。"
    ),
    "judge-structured.toml": (
        "judge-smoke.toml と同じ 3 問を、criteria と instructions を表・表の配列で書いた版。"
        "構造化した問いの書き方（表・入れ子の表・表の配列）を見る用。問い以外は smoke と同じ形。"
    ),
    "toml-generator.toml": (
        "フロー TOML 自体を作るフロー。llm → flow.validate → branch → file.write（text）で、"
        "検証を通った場合だけ .toml を artifacts として返す。flow.validate の使い方の実例。"
    ),
}


def _data_dir() -> Traversable:
    return files("gwr") / _DATA


def spec_markdown() -> str:
    """フロー TOML 仕様 1 枚（Markdown）を返す。"""
    return (_data_dir() / "flow-toml-for-ai.md").read_text("utf-8")


def example_names() -> list[str]:
    """同梱しているフロー例のファイル名を返す（`.toml` のみ・名前順）。"""
    return sorted(
        item.name
        for item in (_data_dir() / _EXAMPLES).iterdir()
        if item.is_file() and item.name.endswith(".toml")
    )


def example_text(name: str) -> str:
    """フロー例の本文を返す。

    `name` は `example_names()` が返した名前と完全一致でなければ `KeyError`。
    パスとして解釈しない（`..` やディレクトリ区切りを含む名前は一致しないため、
    同梱ディレクトリの外へ出ることが構造的に起きない）。
    """
    if name not in example_names():
        raise KeyError(name)
    return (_data_dir() / _EXAMPLES / name).read_text("utf-8")


def example_description(name: str) -> str:
    """フロー例の 1 行説明を返す（未登録なら空文字）。"""
    return EXAMPLE_DESCRIPTIONS.get(name, "")
