"""RAG スモークフロー（examples/rag-smoke.toml）統合テスト：該当あり/なし両分岐を fakes で。

retrieve(委譲先RAG) → gate(分岐) → hit(LLM回答) / none(該当なし) の多段＋分岐を、
実オンプレ版無しで検証する。実機 L1-RAG は env-gated（別途）。
"""

from pathlib import Path

from gwr.adapters.fakes import FakeLLMAdapter, FakeRetrievalAdapter
from gwr.app import WorkflowApp

RAG_TOML = (Path(__file__).resolve().parents[1] / "examples" / "rag-smoke.toml").read_text("utf-8")

CONFIG_DEFAULTS = """
[answer]
model_id = "mock-llm"
system_prompt = "文脈に基づき日本語で簡潔に答えてください。"
"""


def _app(docs):
    llm = FakeLLMAdapter(output="（モック回答）")
    app = WorkflowApp.from_toml(
        RAG_TOML,
        adapters={"llm": llm, "retrieval": FakeRetrievalAdapter(docs=docs)},
        config_defaults=CONFIG_DEFAULTS,
    )
    return app, llm


def test_rag_hit_branch_generates_answer_from_doc():
    """該当あり：docs 非空 → hit（LLM が質問＋文脈で回答）。"""
    docs = [{"id": "rag_answer", "title": "回答", "source": "rag",
             "content": "# 労働基準法 第20条\n解雇予告は30日前..."}]
    app, llm = _app(docs)
    result = app.invoke({"question": "解雇予告は何日前？"})
    assert result.get("error") is None
    assert "（モック回答）" in result["outputs"]
    # hit 分岐＝LLM へ question と文脈(context)の両方が渡る
    call = llm.calls[0]
    assert "context" in call.inputs and "question" in call.inputs
    assert "第20条" in call.inputs["context"]


def test_rag_none_branch_on_empty_docs():
    """該当なし：docs=[] → none（LLM が該当なしを伝える固定指示）。"""
    app, llm = _app([])
    result = app.invoke({"question": "存在しない架空の法令について"})
    assert result.get("error") is None
    assert "（モック回答）" in result["outputs"]
    # none 分岐＝固定の該当なし指示のみ（context は渡さない）
    call = llm.calls[0]
    assert "context" not in call.inputs
    assert "見つかりませんでした" in call.inputs["question"]
