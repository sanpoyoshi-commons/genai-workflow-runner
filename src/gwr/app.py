"""app — フロー 1 本を入力契約から出力写像まで通す実行アプリ。

invoke()：源内リクエストの inputs を受け、bind_inputs → Runner.run → bind_outputs を行い
源内レスポンス {outputs, artifacts} を返す。未捕捉エラーは利用者向けメッセージ（座標＋理由）と
error フィールドへ写像する（値は出さない）。

外部 I/O は持たない。LLM/検索/CI はアダプタ注入（本番＝源内／テスト＝Fake）。
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gwr.config import ConfigManager
from gwr.contract import (
    ContractError,
    bind_inputs,
    bind_outputs,
    parse_inputs,
    parse_outputs,
    to_genai_ui_spec,
)
from gwr.envelope import Envelope
from gwr.nodes.base import NodeContext
from gwr.registry import build_default_registry
from gwr.runner import Runner, RunnerError, ValidationReport
from gwr.validate import validate_flow

# 理由コード → 利用者向けメッセージ。値はエコーせず座標で特定させる。
_USER_MESSAGES = {
    "REQUIRED_MISSING": "必須の入力が不足しています。",
    "NOT_NUMERIC": "数値であるべき項目が数値ではありません。",
    "BELOW_MIN": "入力値が下限を下回っています。",
    "ABOVE_MAX": "入力値が上限を超えています。",
    "TOO_SHORT": "入力が短すぎます。",
    "TOO_LONG": "入力が長すぎます。",
    "ROW_LIMIT_EXCEEDED": "ファイルの行数が上限を超えています。",
    "FILE_PARSE_ERROR": "ファイルを読み込めませんでした。",
    "FILE_DECODE_ERROR": "ファイルを読み込めませんでした。",
}


def render_user_message(reason: str, coordinates: dict[str, Any] | None) -> str:
    """理由コード＋座標から利用者向けメッセージを組み立てる（1 始まり列表記）。"""
    base = _USER_MESSAGES.get(reason, "処理を完了できませんでした。")
    if coordinates and coordinates.get("col") is not None:
        col1 = coordinates["col"] + 1  # 0 始まり内部 → 1 始まり表示
        return f"{col1}列目: {base}"
    return base


class WorkflowApp:
    def __init__(
        self,
        flow: dict[str, Any],
        registry: dict[str, Any],
        adapters: dict[str, Any] | None = None,
        config_factory: Callable[[str], ConfigManager] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.flow = flow
        self.inputs = parse_inputs(flow)
        self.outputs = parse_outputs(flow)
        self.steps = flow.get("steps", [])
        self.runner = Runner(registry)
        self.adapters = adapters or {}
        self.config_factory = config_factory
        self.emit = emit

    @classmethod
    def from_toml(
        cls,
        text: str,
        adapters: dict[str, Any] | None = None,
        config_defaults: str = "",
        config_app: str = "",
        registry: dict[str, Any] | None = None,
    ) -> WorkflowApp:
        flow = tomllib.loads(text)
        registry = registry or build_default_registry()
        config_factory: Callable[[str], ConfigManager] | None = None
        if config_defaults or config_app:
            config_factory = lambda t: ConfigManager.from_toml(t, config_defaults, config_app)  # noqa: E731
        return cls(flow, registry, adapters=adapters, config_factory=config_factory)

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> WorkflowApp:
        return cls.from_toml(Path(path).read_text("utf-8"), **kwargs)

    def ui_spec(self) -> dict[str, Any]:
        """源内リクエスト形式 JSON を生成する。"""
        return to_genai_ui_spec(self.inputs)

    def validate(self) -> ValidationReport:
        return validate_flow(self.flow, self.runner.registry)

    def invoke(self, request_inputs: dict[str, Any]) -> dict[str, Any]:
        """源内リクエスト inputs → 源内レスポンス {outputs, artifacts}。"""
        env = Envelope()
        ctx = NodeContext(config=self.config_factory, adapters=self.adapters, emit=self.emit)
        try:
            bind_inputs(self.inputs, request_inputs, env)
            self.runner.run(self.steps, env, ctx)
            return bind_outputs(self.outputs, env)
        except (ContractError, RunnerError) as e:
            return self._error_response(e)

    def _error_response(self, error: ContractError | RunnerError) -> dict[str, Any]:
        reason = getattr(error, "reason", "ERROR")
        coordinates = getattr(error, "coordinates", None)
        message = render_user_message(reason, coordinates)
        # 中間 Envelope は破棄済み（fail-fast）。利用者にはメッセージのみ返す。
        return {
            "outputs": message,
            "artifacts": [],
            "error": {"reason": reason, "coordinates": coordinates},
        }
