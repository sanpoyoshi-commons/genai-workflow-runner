"""llm ノード ＋ 源内OSS の LLM API アダプタ契約。

ノードは config_type から model_id/system_prompt/inference_config を引き、入力と任意の
schema を載せてアダプタへ委譲する。構造化出力の最小検証と usage 加算を行う。
実体（推論）は源内OSS 側。本モジュールはリクエスト整形・レスポンス解釈・usage 回収のみ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from gwr.adapters.delegate_errors import delegate_errors
from gwr.nodes.base import Node, NodeContext, NodeError, NodeSignature


@dataclass
class LLMRequest:
    model_id: str
    system_prompt: str
    inputs: dict[str, Any]
    inference_config: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    output: Any  # schema 指定時は dict、無指定時は str を想定
    usage: dict[str, int] = field(default_factory=dict)


class LLMAdapter(Protocol):
    def predict(self, request: LLMRequest) -> LLMResponse: ...


def _validate_schema(output: Any, schema: dict[str, Any]) -> None:
    """JSON Schema の最小サブセット（type=object＋required）だけ検証する。"""
    if schema.get("type") == "object":
        if not isinstance(output, dict):
            raise NodeError("SCHEMA_MISMATCH", "expected=object")
        for key in schema.get("required", []):
            if key not in output:
                raise NodeError("SCHEMA_MISMATCH", f"missing_key={key}")


class LLMNode(Node):
    TYPE = "llm"

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        # schema 指定時は構造化（scalar 扱い）、無指定でも scalar 文字列を産出。
        return NodeSignature(outputs={"out": "scalar"})

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        adapter: LLMAdapter | None = ctx.adapters.get("llm")
        if adapter is None:
            raise NodeError("ADAPTER_MISSING", "adapter=llm")

        config_type = config.get("config_type", "default")
        if ctx.config is not None:
            cm = ctx.config(config_type)
            model_id = cm.get_model_id()
            system_prompt = cm.get_system_prompt()
            inference_config = cm.get_inference_config()
        else:
            model_id = str(config.get("model_id", "mock-model"))
            system_prompt = str(config.get("system_prompt", ""))
            inference_config = dict(config.get("inference_config", {}))

        schema = config.get("schema")
        req = LLMRequest(
            model_id=model_id,
            system_prompt=system_prompt,
            inputs=inputs,
            inference_config=inference_config,
            schema=schema,
        )
        with delegate_errors():
            resp = adapter.predict(req)
        if schema is not None:
            _validate_schema(resp.output, schema)
        if resp.usage:
            ctx.add_usage(resp.usage)
        return {"out": resp.output}
