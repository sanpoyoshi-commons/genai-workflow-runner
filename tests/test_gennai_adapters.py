"""本番アダプタ（LLM/RAG/CI）：仕様どおりのリクエスト整形・レスポンス解釈（モック）。"""

import json

import pytest

from gwr.adapters.code_interpreter import CodeInterpreterRequest
from gwr.adapters.envelope_client import EnvelopeClient
from gwr.adapters.gennai import (
    GennaiCodeInterpreterAdapter,
    GennaiLLMAdapter,
    GennaiRetrievalAdapter,
    file_refs_to_inputs_files,
)
from gwr.adapters.llm import LLMRequest
from gwr.adapters.retrieval import RetrievalRequest
from gwr.adapters.transport import HttpResponse, TransportError


class FakeTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers, json=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        return self._responses.pop(0)


def _resp(status, payload):
    return HttpResponse(status=status, body_text=json.dumps(payload, ensure_ascii=False))


def _client(responses):
    return EnvelopeClient(FakeTransport(responses), sleep=lambda _s: None)


# --- LLM: Chat Completions ----------------------------------------------
def test_llm_chat_request_and_response():
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "こんにちは"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }
    t = FakeTransport([_resp(200, payload)])
    adapter = GennaiLLMAdapter(t, "https://llm.example.com/openai/v1/chat/completions",
                               api_key="K", user_id="u1", mode="chat")
    out = adapter.predict(LLMRequest(model_id="gpt-4o", system_prompt="sys",
                                     inputs={"question": "やあ"},
                                     inference_config={"temperature": 0.0}))
    assert out.output == "こんにちは"
    assert out.usage == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    body = t.requests[0]["json"]
    assert body["model"] == "gpt-4o"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "やあ"},
    ]
    assert body["temperature"] == 0.0
    assert t.requests[0]["headers"]["x-api-key"] == "K"


# --- LLM: Responses API （構造化） --------------------------------------
def test_llm_responses_mode_structured():
    payload = {
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": '{"answer": "42"}'}]}
        ],
        "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
        "status": "completed",
    }
    t = FakeTransport([_resp(200, payload)])
    adapter = GennaiLLMAdapter(t, "https://llm.example.com/openai/v1/responses",
                               api_key="K", mode="responses")
    out = adapter.predict(LLMRequest(model_id="gpt-4o", system_prompt="instr",
                                     inputs={"q": "?"},
                                     schema={"type": "object", "required": ["answer"]}))
    assert out.output == {"answer": "42"}  # schema 指定で JSON を dict 化
    body = t.requests[0]["json"]
    assert body["instructions"] == "instr"
    assert body["input"] == "?"


# --- LLM: SSRF allow_insecure（オンプレ版 ollama 直叩き） ---------------------
def test_llm_allow_insecure_http_endpoint_ollama():
    payload = {
        "choices": [{"message": {"content": "やあ"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    t = FakeTransport([_resp(200, payload)])
    adapter = GennaiLLMAdapter(
        t, "http://ollama:11434/v1/chat/completions", api_key="", allow_insecure=True
    )
    out = adapter.predict(LLMRequest(model_id="gemma2:2b", system_prompt="s",
                                     inputs={"prompt": "hello"}))
    assert out.output == "やあ"
    assert t.requests[0]["json"]["model"] == "gemma2:2b"
    assert t.requests[0]["url"] == "http://ollama:11434/v1/chat/completions"


def test_llm_http_endpoint_rejected_without_allow_insecure():
    with pytest.raises(TransportError):
        GennaiLLMAdapter(FakeTransport([]), "http://ollama:11434/v1", api_key="")


# --- RAG -----------------------------------------------------------------
def test_rag_request_shaping_and_answer_doc():
    payload = {"outputs": "## 回答\n\n根拠付き回答", "usageMetadata": [
        {"tokens": {"inputTokens": 30, "outputTokens": 10}}]}
    client = _client([_resp(200, payload)])
    t = client.transport
    adapter = GennaiRetrievalAdapter(client, "https://rag.example.com/", api_key="K", user_id="u")
    out = adapter.search(RetrievalRequest(source="kb", query="予算は?", top_k=3,
                                          filters={"tags": "2026"}))
    assert len(out.docs) == 1
    assert out.docs[0]["id"] == "rag_answer"
    assert "根拠付き回答" in out.docs[0]["content"]
    assert out.usage == {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40}
    body = t.requests[0]["json"]["inputs"]
    assert body == {"question": "予算は?", "n_queries": 3, "tags": "2026"}


def test_rag_empty_answer_returns_no_docs():
    # 法令略称が特定できない等で回答が空 → docs=[]（空内容の Doc を捏造しない）
    client = _client([_resp(200, {"outputs": "", "usageMetadata": []})])
    adapter = GennaiRetrievalAdapter(client, "https://rag.example.com/", api_key="K")
    out = adapter.search(RetrievalRequest(source="kb", query="○○特措法"))
    assert out.docs == []


def test_rag_whitespace_answer_treated_as_empty():
    client = _client([_resp(200, {"outputs": "   \n  "})])
    adapter = GennaiRetrievalAdapter(client, "https://rag.example.com/", api_key="K")
    out = adapter.search(RetrievalRequest(source="kb", query="不明な略称"))
    assert out.docs == []


def test_rag_no_result_marker_maps_to_empty_docs():
    """源内 RAG は no-match でも非空の「該当なし」文を返す→運用注入マーカーで docs=[]。"""
    marker = "クエリから関連する法令を特定できませんでした。法令名を含めて再構成してください。"
    client = _client([_resp(200, {"outputs": marker, "usageMetadata": []})])
    adapter = GennaiRetrievalAdapter(
        client, "https://rag.example.com/", api_key="K", no_result_markers=[marker]
    )
    out = adapter.search(RetrievalRequest(source="law", query="架空の法令"))
    assert out.docs == []  # マーカー一致＝該当なし（文章を回答として載せない）


def test_rag_marker_uses_strip_and_does_not_false_positive():
    marker = "該当する条文が見つかりませんでした。"
    # 前後空白ありでも strip 一致。通常回答はマーカーに一致しない。
    client = _client([_resp(200, {"outputs": "  " + marker + "\n"})])
    adapter = GennaiRetrievalAdapter(
        client, "https://rag.example.com/", api_key="K",
        no_result_markers=["別の文", marker],
    )
    assert adapter.search(RetrievalRequest(source="law", query="q")).docs == []

    client2 = _client([_resp(200, {"outputs": "第20条は解雇予告を定める。"})])
    adapter2 = GennaiRetrievalAdapter(
        client2, "https://rag.example.com/", api_key="K", no_result_markers=[marker]
    )
    assert len(adapter2.search(RetrievalRequest(source="law", query="q")).docs) == 1


# --- CI ------------------------------------------------------------------
def test_ci_request_shaping_and_artifacts():
    payload = {"outputs": "分析しました", "artifacts": [
        {"display_name": "chart.png", "content": "QQ=="}]}
    client = _client([_resp(200, payload)])
    t = client.transport
    adapter = GennaiCodeInterpreterAdapter(client, "https://ci.example.com/responses", api_key="K")
    files = [{"display_name": "data.xlsx", "mime": "x", "storage": "inline", "contents": "Qg=="}]
    out = adapter.run(CodeInterpreterRequest(instruction="平均を出して", files=files))
    assert out.output == "分析しました"
    assert out.artifacts[0]["display_name"] == "chart.png"
    inputs = t.requests[0]["json"]["inputs"]
    assert inputs["input_text"] == "平均を出して"
    # files は同期/UI 生成形 {key, files:[{filename, content}]} に整形
    assert inputs["files"] == [
        {"key": "files", "files": [{"filename": "data.xlsx", "content": "Qg=="}]}
    ]


def test_rag_adapter_forwards_extra_headers_bearer():
    """オンプレ版用：RAG アダプタに渡した Bearer が封筒リクエストに載る（本番＝x-api-key のまま）。"""
    client = _client([_resp(200, {"outputs": "答え"})])
    t = client.transport
    adapter = GennaiRetrievalAdapter(
        client, "https://api.example.com/api/law-rag/query",
        api_key="", extra_headers={"authorization": "Bearer TOK"},
    )
    adapter.search(RetrievalRequest(source="law", query="解雇予告は何日前?"))
    h = t.requests[0]["headers"]
    assert h["authorization"] == "Bearer TOK"
    assert "x-api-key" not in h


def test_ci_adapter_forwards_extra_headers_bearer():
    client = _client([_resp(200, {"outputs": "ok"})])
    t = client.transport
    adapter = GennaiCodeInterpreterAdapter(
        client, "https://api.example.com/api/code-interpreter/responses",
        api_key="", extra_headers={"authorization": "Bearer TOK"},
    )
    adapter.run(CodeInterpreterRequest(instruction="平均を出して"))
    assert t.requests[0]["headers"]["authorization"] == "Bearer TOK"


def test_file_refs_to_inputs_files_shape():
    refs = [{"display_name": "a.csv", "contents": "QQ=="},
            {"display_name": "b.csv", "contents": "Qg=="}]
    shaped = file_refs_to_inputs_files(refs)
    assert shaped == [{"key": "files", "files": [
        {"filename": "a.csv", "content": "QQ=="},
        {"filename": "b.csv", "content": "Qg=="},
    ]}]
