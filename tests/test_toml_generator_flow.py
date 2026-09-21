"""examples/toml-generator.toml の統合テスト（案E＝生成器フローを gwr 自身で書く）。

生成 → flow.validate → branch → file.write の一連が Fake LLM で通ることを固定する。
外部 I/O は無い（LLM は Fake、検証はローカル処理）。
"""

import base64
from pathlib import Path

import pytest

from gwr.adapters.fakes import FakeLLMAdapter
from gwr.app import WorkflowApp
from gwr.assets import spec_markdown
from gwr.validate import validate_file

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "examples" / "toml-generator.toml"
HELLO = (ROOT / "examples" / "hello.toml").read_text("utf-8")

BROKEN = 'version = "1"\n[[steps]]\nid = "x"\ntype = "no.such"\nout = "vars.x"\n'
_CONFIG = '[default]\nmodel_id = "local"\n'


def _invoke(llm_output: str) -> dict:
    app = WorkflowApp.from_toml(
        FLOW.read_text("utf-8"),
        adapters={"llm": FakeLLMAdapter(output=llm_output)},
        config_defaults=_CONFIG,
    )
    return app.invoke({"request": "挨拶を返すだけのフロー", "spec": spec_markdown()})


def test_the_generator_flow_itself_validates():
    assert validate_file(FLOW).ok


def test_generated_flow_that_passes_comes_back_as_a_toml_artifact():
    result = _invoke(HELLO)

    assert result.get("error") is None
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert artifact["display_name"] == "flow.toml"
    assert base64.b64decode(artifact["contents"]).decode("utf-8") == HELLO
    assert '"ok": true' in result["outputs"]


def test_generated_flow_that_fails_returns_the_report_and_no_artifact():
    result = _invoke(BROKEN)

    assert result.get("error") is None
    assert result["artifacts"] == [], "検証に落ちたフローをファイルで返してはいけない"
    assert '"ok": false' in result["outputs"]
    assert "UNKNOWN_NODE_TYPE" in result["outputs"]


def test_report_is_rendered_as_json_not_python_repr():
    """検証レポートは dict なので、利用者向けには JSON で出す（'True' や "'ok'" を出さない）。"""
    outputs = _invoke(HELLO)["outputs"]
    assert "'ok'" not in outputs
    assert "True" not in outputs
    assert '"schema_version": 1' in outputs


def test_the_artifact_round_trips_back_through_validate():
    """返ってきた .toml をそのまま検証し直しても通る（案E の完成条件）。"""
    artifact = _invoke(HELLO)["artifacts"][0]
    regenerated = base64.b64decode(artifact["contents"]).decode("utf-8")

    from gwr.validate import validate_text_safe

    assert validate_text_safe(regenerated).ok


@pytest.mark.parametrize("missing", ["request", "spec"])
def test_required_inputs_are_enforced(missing):
    app = WorkflowApp.from_toml(
        FLOW.read_text("utf-8"),
        adapters={"llm": FakeLLMAdapter(output=HELLO)},
        config_defaults=_CONFIG,
    )
    inputs = {"request": "x", "spec": "y"}
    del inputs[missing]
    result = app.invoke(inputs)
    assert result["error"]["reason"] == "REQUIRED_MISSING"


def test_ui_spec_exposes_both_inputs():
    app = WorkflowApp.from_toml(FLOW.read_text("utf-8"))
    spec = app.ui_spec()
    assert spec["request"]["type"] == "textarea"
    assert spec["spec"]["type"] == "textarea"
