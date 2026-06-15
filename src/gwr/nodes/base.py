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
from typing import TYPE_CHECKING, Any, Literal

from gwr.datatypes import TypeLevel

if TYPE_CHECKING:
    from gwr.config import ConfigManager

OnError = Literal["fail", "skip", "default"]


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
