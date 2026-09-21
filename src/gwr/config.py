"""Config — defaults+app TOML を優先順位マージする設定ローダ（自前実装）。

config_type（例: "answer_generation"）ごとに、以下の優先順で deep-merge する：

    defaults["default"]  <  defaults[type]  <  app["default"]  <  app[type]

app 側が default 側を上書きし、欠損キーは下位レイヤへフォールバックする。
源内OSS の LLM アダプタが model_id / system_prompt / inference_config を引くための窓口。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

BASE_KEY = "default"


class ConfigError(Exception):
    """不正な TOML や未知の config_type など、設定起因の失敗。"""


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """over を base にネスト方向で重ねる（over 優先）。base は変更しない。"""
    result = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"不正な TOML: {e}") from e


class ConfigManager:
    """1 つの config_type に対する解決済み設定ビュー。"""

    def __init__(
        self,
        config_type: str,
        defaults: dict[str, Any] | None = None,
        app: dict[str, Any] | None = None,
    ) -> None:
        self.config_type = config_type
        self._defaults = defaults or {}
        self._app = app or {}
        self._resolved = self._resolve()

    def _resolve(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for layer in (self._defaults, self._app):
            merged = _deep_merge(merged, layer.get(BASE_KEY, {}))
            merged = _deep_merge(merged, layer.get(self.config_type, {}))
        return merged

    # --- 読み出し I/F ---------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._resolved.get(key, default)

    def get_model_id(self) -> str:
        model_id = self._resolved.get("model_id")
        if not model_id:
            raise ConfigError(f"model_id 未設定: config_type={self.config_type!r}")
        return str(model_id)

    def get_system_prompt(self) -> str:
        return str(self._resolved.get("system_prompt", ""))

    def get_inference_config(self) -> dict[str, Any]:
        cfg = self._resolved.get("inference_config", {})
        if not isinstance(cfg, dict):
            raise ConfigError("inference_config は table である必要がある")
        return dict(cfg)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._resolved)

    # --- ファクトリ -----------------------------------------------------
    @classmethod
    def from_toml(
        cls, config_type: str, defaults_text: str = "", app_text: str = ""
    ) -> ConfigManager:
        return cls(
            config_type,
            defaults=_load_toml(defaults_text) if defaults_text else {},
            app=_load_toml(app_text) if app_text else {},
        )

    @classmethod
    def from_files(
        cls,
        config_type: str,
        defaults_path: str | Path | None = None,
        app_path: str | Path | None = None,
    ) -> ConfigManager:
        defaults = _load_toml(Path(defaults_path).read_text("utf-8")) if defaults_path else {}
        app = _load_toml(Path(app_path).read_text("utf-8")) if app_path else {}
        return cls(config_type, defaults=defaults, app=app)
