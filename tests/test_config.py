"""Config: default単独／app上書き／欠損キーのフォールバック／不正TOML。"""

import pytest

from gwr.config import ConfigError, ConfigManager

DEFAULTS = """
[default]
model_id = "base-model"
system_prompt = "base prompt"
[default.inference_config]
temperature = 0.0
max_tokens = 1024

[answer_generation]
system_prompt = "answer prompt"
[answer_generation.inference_config]
max_tokens = 2048
"""

APP = """
[answer_generation]
model_id = "app-model"
[answer_generation.inference_config]
temperature = 0.7
"""


def test_defaults_only():
    cm = ConfigManager.from_toml("answer_generation", DEFAULTS, "")
    assert cm.get_model_id() == "base-model"
    assert cm.get_system_prompt() == "answer prompt"  # type が base を上書き
    assert cm.get_inference_config() == {"temperature": 0.0, "max_tokens": 2048}


def test_app_overrides_default():
    cm = ConfigManager.from_toml("answer_generation", DEFAULTS, APP)
    assert cm.get_model_id() == "app-model"  # app[type] > default[type]
    # inference_config はネストマージ（app の temperature 上書き＋default の max_tokens 残存）
    assert cm.get_inference_config() == {"temperature": 0.7, "max_tokens": 2048}


def test_missing_key_falls_back_to_base():
    cm = ConfigManager.from_toml("answer_generation", DEFAULTS, APP)
    # system_prompt は app に無い → default[type] → base の順でフォールバック
    assert cm.get_system_prompt() == "answer prompt"


def test_unknown_type_uses_base_only():
    cm = ConfigManager.from_toml("unknown_type", DEFAULTS, "")
    assert cm.get_model_id() == "base-model"
    assert cm.get_system_prompt() == "base prompt"


def test_missing_model_id_raises():
    cm = ConfigManager.from_toml("x", "[x]\nsystem_prompt='p'\n", "")
    with pytest.raises(ConfigError):
        cm.get_model_id()


def test_invalid_toml_raises():
    with pytest.raises(ConfigError):
        ConfigManager.from_toml("x", "this is = = not toml", "")
