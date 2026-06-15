"""llm / retrieval / code_interpreter ノード＋アダプタ契約（すべて Fake・外部 I/O なし）。"""

import pytest

from gwr.adapters.code_interpreter import CodeInterpreterNode
from gwr.adapters.fakes import (
    FakeCodeInterpreterAdapter,
    FakeLLMAdapter,
    FakeRetrievalAdapter,
)
from gwr.adapters.llm import LLMNode, LLMRequest, LLMResponse
from gwr.adapters.retrieval import RetrievalNode
from gwr.config import ConfigManager
from gwr.nodes.base import NodeContext, NodeError


# --- llm ----------------------------------------------------------------
def test_llm_structured_output_and_usage():
    fake = FakeLLMAdapter(output={"answer": "42"}, usage={"input_tokens": 7, "output_tokens": 3})
    seen = []
    ctx = NodeContext(adapters={"llm": fake}, usage_sink=seen.append)
    cfg = {"config_type": "x", "schema": {"type": "object", "required": ["answer"]}}
    out = LLMNode().run({"question": "?"}, cfg, ctx)
    assert out["out"] == {"answer": "42"}
    assert seen == [{"input_tokens": 7, "output_tokens": 3}]


def test_llm_schema_mismatch_rejected():
    fake = FakeLLMAdapter(output={"wrong": "x"})
    ctx = NodeContext(adapters={"llm": fake})
    cfg = {"schema": {"type": "object", "required": ["answer"]}}
    with pytest.raises(NodeError) as ei:
        LLMNode().run({}, cfg, ctx)
    assert ei.value.reason == "SCHEMA_MISMATCH"


def test_llm_uses_config_manager_for_model():
    defaults = """
[answer_generation]
model_id = "m1"
system_prompt = "sys"
[answer_generation.inference_config]
temperature = 0.0
"""
    fake = FakeLLMAdapter(output="ok")
    ctx = NodeContext(
        adapters={"llm": fake},
        config=lambda t: ConfigManager.from_toml(t, defaults, ""),
    )
    LLMNode().run({"q": "x"}, {"config_type": "answer_generation"}, ctx)
    req: LLMRequest = fake.calls[0]
    assert req.model_id == "m1"
    assert req.system_prompt == "sys"
    assert req.inference_config == {"temperature": 0.0}
    assert req.inputs == {"q": "x"}


def test_llm_missing_adapter_rejected():
    with pytest.raises(NodeError) as ei:
        LLMNode().run({}, {}, NodeContext())
    assert ei.value.reason == "ADAPTER_MISSING"


def test_llm_response_dataclass_defaults():
    r = LLMResponse(output="x")
    assert r.usage == {}


# --- retrieval ----------------------------------------------------------
def test_retrieval_returns_normalized_docs():
    fake = FakeRetrievalAdapter()
    ctx = NodeContext(adapters={"retrieval": fake})
    out = RetrievalNode().run({"query": "予算"}, {"source": "kb", "top_k": 3}, ctx)
    assert len(out["out"]) == 2
    assert out["out"][0]["id"] == "d1"
    assert fake.calls[0].source == "kb"
    assert fake.calls[0].top_k == 3
    assert fake.calls[0].query == "予算"


def test_retrieval_missing_query_rejected():
    ctx = NodeContext(adapters={"retrieval": FakeRetrievalAdapter()})
    with pytest.raises(NodeError) as ei:
        RetrievalNode().run({}, {"source": "kb"}, ctx)
    assert ei.value.reason == "QUERY_MISSING"


# --- code_interpreter ---------------------------------------------------
def test_code_interpreter_collects_artifacts():
    arts = [
        {"display_name": "out.csv", "mime": "text/csv", "storage": "inline", "contents": "QQ=="}
    ]
    fake = FakeCodeInterpreterAdapter(artifacts=arts, output="done")
    ctx = NodeContext(adapters={"code_interpreter": fake})
    files = [{"display_name": "in.csv", "contents": "QQ=="}]
    out = CodeInterpreterNode().run({"files": files}, {"instruction": "集計して"}, ctx)
    assert out["artifacts"] == arts
    assert out["output"] == "done"
    assert fake.calls[0].instruction == "集計して"
    assert len(fake.calls[0].files) == 1


def test_code_interpreter_missing_instruction_rejected():
    ctx = NodeContext(adapters={"code_interpreter": FakeCodeInterpreterAdapter()})
    with pytest.raises(NodeError) as ei:
        CodeInterpreterNode().run({"files": []}, {}, ctx)
    assert ei.value.reason == "INSTRUCTION_MISSING"
