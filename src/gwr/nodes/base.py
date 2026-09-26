"""ノード共通プロトコル＋型シグネチャ宣言。

各ノードは以下を備える：
- `TYPE`: TOML の `type` に対応する識別子（例 "file.load"）。
- `signature(step)`: 入力要求型（論理入力名→型レベル）と産出型（出力名→型レベル）を宣言。
  型伝播チェックがこれを参照して「スロット→型」を伝播・照合する。
- `run(inputs, ctx)`: 解決済みの入力を受け、出力名→値の dict を返す。

エラー契約：
- データ起因の失敗は DataError（row, col, reason コードのみ・値を持たない）。
- それ以外の実行失敗は NodeError。
- step 単位の on_error は Runner 側で fail/skip/default を解釈する（既定 fail）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from gwr.datatypes import TypeLevel

if TYPE_CHECKING:
    from gwr.config import ConfigManager

OnError = Literal["fail", "skip", "default"]

# 検証レポートの分類。`gwr.runner` がこれを import して `Issue` に載せる
# （逆向きに import すると runner → nodes.base の依存と循環するため、ここが定義元）。
IssueCategory = Literal["static", "dataflow", "type"]
# `error` は `ok=False` にする。`warning` は別の配列（ValidationReport.warnings）へ入り、
# `ok` を落とさない＝直さなくても実行できる書き方の指摘。
IssueSeverity = Literal["error", "warning"]


class IssueSink(Protocol):
    """静的検査の記録先。`gwr.runner.ValidationReport` が満たす構造的な型。

    ノードが `gwr.runner` を import すると循環するため、受け口を型として切り出す。
    理由コードは **`add` の第 3 引数に文字列リテラルで書くこと**（`docs/cli.md` との突合が
    AST で全数を拾えなくなる。tests/test_reason_codes.py）。
    """

    def add(
        self,
        category: IssueCategory,
        step_id: str,
        reason: str,
        detail: str = "",
        *,
        severity: IssueSeverity = "error",
    ) -> None: ...


class NodeError(Exception):
    """ノード実行失敗（一般）。reason はコード文字列を推奨。"""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


class DataError(NodeError):
    """データ起因の失敗。座標（行・列）＋理由コードのみを持ち、セル値は持たない。

    列番号は 0 始まりの内部表現。利用者向け表示は呼び出し側で 1 始まりへ変換する。
    """

    def __init__(self, reason: str, row: int | None = None, col: int | None = None) -> None:
        super().__init__(reason)
        self.row = row
        self.col = col

    def coordinates(self) -> dict[str, Any]:
        return {"row": self.row, "col": self.col, "reason": self.reason}


@dataclass(frozen=True)
class NodeSignature:
    """型シグネチャ。inputs/outputs は 論理名→型レベル。

    out_columns: opt-in で宣言された出力カラム名（静的カラム照合用・宣言分のみ）。
    """

    inputs: dict[str, TypeLevel] = field(default_factory=dict)
    outputs: dict[str, TypeLevel] = field(default_factory=dict)
    out_columns: dict[str, list[str]] | None = None


@dataclass
class NodeContext:
    """run に渡す実行コンテキスト。adapters/config/logger/usage を束ねる。"""

    config: Callable[[str], ConfigManager] | None = None
    adapters: dict[str, Any] = field(default_factory=dict)
    emit: Callable[[dict[str, Any]], None] | None = None
    usage_sink: Callable[[dict[str, int]], None] | None = None

    def log(self, event: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(event)

    def add_usage(self, delta: dict[str, int]) -> None:
        if self.usage_sink is not None:
            self.usage_sink(delta)


class Node(ABC):
    """全ノードの基底。"""

    TYPE: str = ""
    OUT_NAMES: tuple[str, ...] = ("out",)

    @classmethod
    @abstractmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        """step 設定に応じた型シグネチャを返す。"""

    @abstractmethod
    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        """解決済み入力・step 設定（ops/format/config_type 等）・実行コンテキストを受け、
        出力名→値の dict を返す。"""

    def static_check(  # noqa: B027 — 既定「検査なし」が正しい。強制すると全ノードに空実装が要る
        self, step: dict[str, Any], step_id: str, report: IssueSink
    ) -> None:
        """step 設定のノード固有な静的検査。既定は何もしない（多くのノードは不要）。

        `Runner._static_checks` が、型が既知のノードについて 1 度だけ呼ぶ。実行前に落としたい
        「書き方の誤り」をここに置く（`judge` の questions など）。Runner にノード固有の分岐を
        増やさないための入口で、**理由コードはこのメソッドの中に文字列リテラルで書く**
        （`docs/cli.md` との突合テストは `src/gwr` 全体を走査するので、ノード側に書いても拾える）。
        """
