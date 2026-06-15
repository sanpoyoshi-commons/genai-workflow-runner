"""Envelope — ステップ間データの共通入れ物。

スロット: vars / docs / tables / files / usage。
- get(path)/set(path,val): ドット区切りパス（先頭の "$." は任意で剥がす）。
- JSON 直列化往復が一致（＝ステップ境界がそのまま再開単位になりうる）。
- 深いコピーで独立性を担保（純粋寄りのリプレイを容易化）。

注意: 再開可能な"設計"は保つが、永続化の実体は持たない。失敗時は呼び出し側が
破棄する（fail-fast・完全ステートレス）。
"""

from __future__ import annotations

import copy
import json
from typing import Any

SLOTS = ("vars", "docs", "tables", "files", "usage")


class EnvelopePathError(KeyError):
    """存在しないパスを get した／不正なパスを set した。"""


def _strip_root(path: str) -> str:
    """先頭の "$." または "$" を剥がす（JMESPath/参照表記との整合）。"""
    if path.startswith("$."):
        return path[2:]
    if path == "$":
        return ""
    return path


class Envelope:
    """5 スロットを保持する直列化可能なコンテナ。"""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        base: dict[str, Any] = {slot: {} for slot in SLOTS}
        if data:
            for slot in SLOTS:
                if slot in data and data[slot] is not None:
                    base[slot] = copy.deepcopy(data[slot])
        self._data = base

    # --- スロットアクセス（読みやすさ用） -------------------------------
    @property
    def vars(self) -> dict[str, Any]:
        return self._data["vars"]

    @property
    def docs(self) -> dict[str, Any]:
        return self._data["docs"]

    @property
    def tables(self) -> dict[str, Any]:
        return self._data["tables"]

    @property
    def files(self) -> dict[str, Any]:
        return self._data["files"]

    @property
    def usage(self) -> dict[str, Any]:
        return self._data["usage"]

    # --- パスアクセス ---------------------------------------------------
    def get(self, path: str) -> Any:
        """ドットパスで値を取得。未定義パスは EnvelopePathError。"""
        parts = [p for p in _strip_root(path).split(".") if p != ""]
        node: Any = self._data
        for i, part in enumerate(parts):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                bad = ".".join(parts[: i + 1])
                raise EnvelopePathError(f"未定義パス: {bad}")
        return node

    def has(self, path: str) -> bool:
        try:
            self.get(path)
            return True
        except EnvelopePathError:
            return False

    def set(self, path: str, value: Any) -> None:
        """ドットパスで値を設定。中間 dict は自動生成。

        値は**参照のまま保存**する（ホットパスの全コピー回避）。前提＝ノードは入力を変更せず新規
        オブジェクトを返す（値は不変として扱う）。外向き隔離は to_dict()/from_dict()/__init__ の境界
        deepcopy で担保。別スロットやフロー定義を共有し得る書込元（runner の foreach item・on_error
        default、contract の default_value）では**呼び出し側が明示 deepcopy** する。"""
        parts = [p for p in _strip_root(path).split(".") if p != ""]
        if not parts:
            raise EnvelopePathError("空パスには set できない")
        if parts[0] not in SLOTS:
            raise EnvelopePathError(f"未知のスロット: {parts[0]}（許可: {', '.join(SLOTS)}）")
        node: dict[str, Any] = self._data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    # --- usage 集計 -----------------------------------------------------
    def add_usage(self, delta: dict[str, int]) -> None:
        """トークン使用量などを加算合算する。"""
        for k, v in delta.items():
            self._data["usage"][k] = self._data["usage"].get(k, 0) + v

    # --- 直列化 ---------------------------------------------------------
    def raw(self) -> dict[str, Any]:
        """内部データへの読み取り専用ビュー（変更厳禁）。非破壊読み取り（式評価等）の高速経路用で、
        to_dict() の全体 deepcopy を避ける。外部へ返す/隔離が要るときは to_dict() を使う。"""
        return self._data

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def to_json(self) -> str:
        return json.dumps(self._data, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Envelope:
        return cls(json.loads(text))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        return cls(data)

    def copy(self) -> Envelope:
        return Envelope(self._data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Envelope) and other._data == self._data

    def __repr__(self) -> str:
        return f"Envelope({self._data!r})"
