"""決定論的な Fake アダプタ群（テスト＋ローカル PoC 用・外部 I/O なし）。

源内OSS の API の代わりに固定／設定可能な応答を返す。実 LLM・実検索・実行環境は呼ばない。
"""

from __future__ import annotations

from typing import Any, cast

from gwr.adapters.code_interpreter import (
    CodeInterpreterRequest,
    CodeInterpreterResponse,
)
from gwr.adapters.llm import LLMRequest, LLMResponse
from gwr.adapters.retrieval import RetrievalRequest, RetrievalResponse
from gwr.datatypes import Doc, FileRef


class FakeLLMAdapter:
    """固定 output/usage を返す。schema 指定時は canned dict を返す想定で構成する。"""

    def __init__(self, output: Any = "（モック出力）", usage: dict[str, int] | None = None) -> None:
        self.output = output
        self.usage = usage or {"input_tokens": 1, "output_tokens": 1}
        self.calls: list[LLMRequest] = []

    def predict(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(output=self.output, usage=dict(self.usage))


class FakeRetrievalAdapter:
    def __init__(self, docs: list[Doc] | None = None, usage: dict[str, int] | None = None) -> None:
        self.docs = docs if docs is not None else _default_docs()
        self.usage = usage or {}
        self.calls: list[RetrievalRequest] = []

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        self.calls.append(request)
        docs = [cast(Doc, dict(d)) for d in self.docs]
        return RetrievalResponse(docs=docs, usage=dict(self.usage))


class FakeCodeInterpreterAdapter:
    def __init__(
        self,
        artifacts: list[FileRef] | None = None,
        output: str = "",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.artifacts = artifacts if artifacts is not None else _default_artifacts()
        self.output = output
        self.usage = usage or {}
        self.calls: list[CodeInterpreterRequest] = []

    def run(self, request: CodeInterpreterRequest) -> CodeInterpreterResponse:
        self.calls.append(request)
        artifacts = [cast(FileRef, dict(a)) for a in self.artifacts]
        return CodeInterpreterResponse(
            artifacts=artifacts,
            output=self.output,
            usage=dict(self.usage),
        )


def _default_docs() -> list[Doc]:
    return [
        {"id": "d1", "title": "文書1", "source": "kb", "content": "本文1", "score": 0.9},
        {"id": "d2", "title": "文書2", "source": "kb", "content": "本文2", "score": 0.8},
    ]


def _default_artifacts() -> list[FileRef]:
    return [
        {
            "display_name": "result.txt",
            "mime": "text/plain",
            "storage": "inline",
            "contents": "",
        }
    ]
