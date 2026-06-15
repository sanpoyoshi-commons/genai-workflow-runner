"""logging — 値フリーの構造化ログ。

不変条件：ログ API にセル値を渡す経路を作らない。イベントは固定スキーマのメタ情報のみ
（step_id / node_type / in・out スロット名 / row_count / col_count / dtypes / duration /
usage / status）。データ起因エラーは座標（行・列番号）＋理由コードのみ。

プラガブル：sink（出力先）／formatter（整形）／level（閾値）／redactor（任意のフィールド削減）。
既定は「構造化 JSON・メタのみ・stdout」。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

# ログイベントに載せてよいフィールドの許可リスト（これ以外は構造的に通さない）。
ALLOWED_FIELDS = frozenset(
    {
        "step_id",
        "node_type",
        "in_slots",
        "out_slots",
        "row_count",
        "col_count",
        "dtypes",
        "duration_ms",
        "usage",
        "status",
        "level",
        # データ起因エラー用（座標＋コードのみ・値は含まない）
        "row",
        "col",
        "reason",
    }
)

_LEVELS = {"DEBUG": 10, "INFO": 20, "ERROR": 40}


@dataclass
class LogEvent:
    """固定スキーマのログイベント。value 系フィールドは存在しない＝値を持てない。"""

    step_id: str = ""
    node_type: str = ""
    status: str = ""
    in_slots: list[str] = field(default_factory=list)
    out_slots: list[str] = field(default_factory=list)
    row_count: int | None = None
    col_count: int | None = None
    dtypes: dict[str, str] | None = None
    duration_ms: float | None = None
    usage: dict[str, int] | None = None


def _default_formatter(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _default_sink(line: str) -> None:
    print(line, file=sys.stdout)


def redact_columns(record: dict[str, Any]) -> dict[str, Any]:
    """列情報（col/col_count/dtypes）を伏せる redactor の例。"""
    return {k: v for k, v in record.items() if k not in ("col", "col_count", "dtypes")}


class Logger:
    def __init__(
        self,
        sink: Callable[[str], None] | None = None,
        formatter: Callable[[dict[str, Any]], str] | None = None,
        level: str = "INFO",
        redactor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.sink = sink or _default_sink
        self.formatter = formatter or _default_formatter
        self.level = level
        self.redactor = redactor

    def _enabled(self, level: str) -> bool:
        return _LEVELS.get(level, 20) >= _LEVELS.get(self.level, 20)

    def _filter_allowed(self, record: dict[str, Any]) -> dict[str, Any]:
        # 許可フィールド以外（万一の値混入）は構造的に落とす。None も落として簡潔に。
        return {k: v for k, v in record.items() if k in ALLOWED_FIELDS and v is not None}

    def _write(self, record: dict[str, Any], level: str) -> None:
        if not self._enabled(level):
            return
        clean = self._filter_allowed(record)
        clean["level"] = level
        if self.redactor is not None:
            clean = self.redactor(clean)
        self.sink(self.formatter(clean))

    def emit(self, event: LogEvent, level: str = "INFO") -> None:
        """ノード実行のメタイベントを記録する。"""
        self._write(asdict(event), level)

    def data_error(
        self, step_id: str, reason: str, row: int | None = None, col: int | None = None
    ) -> None:
        """データ起因エラーを座標＋理由コードのみで記録（値は載らない）。"""
        record = {
            "step_id": step_id,
            "status": "ERROR",
            "reason": reason,
            "row": row,
            "col": col,
        }
        self._write(record, "ERROR")
