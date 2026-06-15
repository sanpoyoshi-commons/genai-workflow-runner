"""委譲先（源内 ExApp）への本番アダプタ実装。

- LLM : Azure OpenAI 互換（Chat Completions / Responses）。封筒ではなく OpenAI ネイティブ形。
- RAG : genai-web 封筒（question 等 → outputs）。回答 Markdown を 1 件の Doc に写す。
- CI  : genai-web 封筒（input_text + files → artifacts）。

接続値（endpoint / api_key / user_id）は環境変数化して注入する想定（コードに秘匿値を持たない）。
源内の I/O 形（エンドポイント・JSON・キー）にのみ準拠するアダプタ。
プロンプト全文やアルゴリズムは持たない。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from gwr.adapters.code_interpreter import (
    CodeInterpreterRequest,
    CodeInterpreterResponse,
)
from gwr.adapters.envelope_client import EnvelopeClient
from gwr.adapters.llm import LLMRequest, LLMResponse
from gwr.adapters.retrieval import RetrievalRequest, RetrievalResponse
from gwr.adapters.transport import HttpTransport, TransportError, assert_public_https
from gwr.adapters.usage import normalize_usage
from gwr.datatypes import Doc, FileRef


def _user_text(inputs: dict[str, Any]) -> str:
    """inputs から user メッセージ本文を組み立てる。

    単一の文字列値ならそれを、複数/非文字列なら JSON 直列化を使う（アプリ非依存の既定）。
    """
    if len(inputs) == 1:
        (value,) = inputs.values()
        if isinstance(value, str):
            return value
    return json.dumps(inputs, ensure_ascii=False, sort_keys=True)


def file_refs_to_inputs_files(refs: list[FileRef], key: str = "files") -> list[dict[str, Any]]:
    """FileRef[] を源内の送出形式（同期/UI 生成形に固定）へ整形する。"""
    return [
        {
            "key": key,
            "files": [
                {"filename": r.get("display_name", "upload"), "content": r.get("contents", "")}
                for r in refs
            ],
        }
    ]


class GennaiLLMAdapter:
    """Azure OpenAI 互換 LLM。mode="chat"（既定）または "responses"。"""

    def __init__(
        self,
        transport: HttpTransport,
        endpoint: str,
        *,
        api_key: str,
        user_id: str = "",
        mode: str = "chat",
        allow_insecure: bool = False,
    ) -> None:
        assert_public_https(endpoint, allow_insecure=allow_insecure)
        self.transport = transport
        self.endpoint = endpoint
        self.api_key = api_key
        self.user_id = user_id
        self.mode = mode

    def _headers(self) -> dict[str, str]:
        headers = {"x-api-key": self.api_key, "content-type": "application/json"}
        if self.user_id:
            headers["x-user-id"] = self.user_id
        return headers

    def predict(self, request: LLMRequest) -> LLMResponse:
        body = self._build_body(request)
        resp = self.transport.request("POST", self.endpoint, headers=self._headers(), json=body)
        if resp.status >= 400:
            # 応答本文も載せる（model not found 等の診断のため・先頭 300 字）
            raise TransportError(
                "LLM_HTTP_ERROR", f"status={resp.status} body={resp.body_text[:300]}"
            )
        data = resp.json()
        text = self._extract_text(data)
        output: Any = text
        if request.schema is not None:
            output = _maybe_json(text)
        return LLMResponse(output=output, usage=normalize_usage(data))

    def _build_body(self, request: LLMRequest) -> dict[str, Any]:
        user_text = _user_text(request.inputs)
        if self.mode == "responses":
            body: dict[str, Any] = {
                "model": request.model_id,
                "instructions": request.system_prompt,
                "input": user_text,
            }
        else:  # chat completions（OpenAI 互換）
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.append({"role": "user", "content": user_text})
            body = {"model": request.model_id, "messages": messages}
        body.update(request.inference_config)  # temperature/max_tokens 等
        return body

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        # Chat Completions
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            return str(choices[0].get("message", {}).get("content", ""))
        # Responses API
        parts: list[str] = []
        for item in data.get("output", []) or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        parts.append(str(content.get("text", "")))
        return "".join(parts)


class GennaiRetrievalAdapter:
    """源内 RAG ExApp（封筒）。回答 Markdown を Doc に写像する。

    委譲先 RAG は「検索＋回答生成＋引用」を行い `{outputs(md), usageMetadata}` を返し、
    gwr の Doc[] には合成回答を 1 件の Doc（id="rag_answer"）として載せる。

    **no-result の判定（実機で確定した契約）**：源内 RAG は該当が無くても **空ではなく
    「該当なし」の文章を 200 で返す**（GCP lawsy 由来の固定文 3 種をオンプレ版も忠実ポート。
    AWS は LLM 生成文＋footer で常に非空）。よって「空＝該当なし」は成り立たない。

    no-result の判定は次の 2 段：
    - 回答が空/空白（真に空を返す委譲先向けの保険）。
    - 回答（strip 済み）が **運用注入の no-result マーカー**のいずれかに一致（源内の
      「該当なし」文）。マーカーは運用者が `no_result_markers` で注入する＝gwr 本体に
      源内固有の日本語を焼かない。日本語本文の意味解釈はしない。

    no-result なら `docs=[]` を返す（空内容の Doc を捏造しない）。フロー側は
    `branch when="len($.docs.X) == 0"` で「該当なし」分岐を書ける。
    """

    def __init__(
        self,
        client: EnvelopeClient,
        endpoint: str,
        *,
        api_key: str,
        user_id: str = "",
        extra_headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
        no_result_markers: list[str] | None = None,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.api_key = api_key
        self.user_id = user_id
        # オンプレ版は Keycloak Bearer を載せる（本番＝None で x-api-key のまま）。
        self.extra_headers = extra_headers
        # 運用注入の「該当なし」文（strip 済み集合）。空/None なら空判定のみ。
        self._no_result = {m.strip() for m in (no_result_markers or []) if m.strip()}

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        inputs: dict[str, Any] = {"question": request.query, "n_queries": request.top_k}
        tags = request.filters.get("tags")
        if tags:
            inputs["tags"] = tags
        result = self.client.invoke(
            self.endpoint,
            inputs,
            api_key=self.api_key,
            user_id=self.user_id,
            extra_headers=self.extra_headers,
        )
        answer = (result.outputs or "").strip()
        if not answer or answer in self._no_result:
            return RetrievalResponse(docs=[], usage=result.usage)  # 該当なし
        doc: Doc = {
            "id": "rag_answer",
            "title": "回答",
            "source": "rag",
            "content": result.outputs,
        }
        return RetrievalResponse(docs=[doc], usage=result.usage)


class GennaiCodeInterpreterAdapter:
    """源内 Code Interpreter ExApp（封筒）。input_text + files → artifacts。"""

    def __init__(
        self,
        client: EnvelopeClient,
        endpoint: str,
        *,
        api_key: str,
        user_id: str = "",
        extra_headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.api_key = api_key
        self.user_id = user_id
        # オンプレ版は Keycloak Bearer を載せる（本番＝None で x-api-key のまま）。
        self.extra_headers = extra_headers

    def run(self, request: CodeInterpreterRequest) -> CodeInterpreterResponse:
        inputs: dict[str, Any] = {"input_text": request.instruction}
        if request.files:
            inputs["files"] = file_refs_to_inputs_files(request.files)
        result = self.client.invoke(
            self.endpoint,
            inputs,
            api_key=self.api_key,
            user_id=self.user_id,
            extra_headers=self.extra_headers,
        )
        return CodeInterpreterResponse(
            artifacts=result.artifacts,
            output=result.outputs,
            usage=result.usage,  # CI は usage を返さない設計＝通常 {}
        )


def _maybe_json(text: str) -> Any:
    """構造化出力指定時、テキストが JSON オブジェクトなら dict にして返す。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except ValueError:
            return text
    return text
