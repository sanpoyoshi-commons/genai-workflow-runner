"""決定論的な Fake アダプタ群（テスト＋ローカル PoC 用・外部 I/O なし）。

源内OSS の API の代わりに固定／設定可能な応答を返す。実 LLM・実検索・実行環境は呼ばない。
"""

from __future__ import annotations

from typing import Any, cast

from gwr.adapters.code_interpreter import (
    CodeInterpreterRequest,
    CodeInterpreterResponse,
)
from gwr.adapters.judge import JudgeRequest, JudgeResponse, answer_labels
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


class FakeJudgeAdapter:
    """問いの形から決定論的な答えを組み立てる（**判定していない**）。

    choice は先頭のラベル、noul は True、score は 0 を返す。確率は先頭に寄せ、
    confidence は本家式（候補が 2 つ以上なら 1.0）。実際の推論は一切行わないので、
    **この答えを判断の根拠にしてはいけない**（MCP の dry_run・ローカル確認の足場）。
    """

    def __init__(self, model: str = "fake-judge", usage: dict[str, int] | None = None) -> None:
        self.model = model
        # 既定は空＝「委譲先が返さなかった」状態を再現する（0 で埋めない方針の確認用）。
        self.usage = usage or {}
        self.calls: list[JudgeRequest] = []

    def judge(self, request: JudgeRequest) -> JudgeResponse:
        self.calls.append(request)
        answers: dict[str, Any] = {}
        for name, question in request.questions.items():
            qtype = question.get("type")
            if qtype == "noul":
                answers[name] = {"type": "noul", "noul": True}
                continue
            labels = answer_labels(question)
            probabilities = {label: (1.0 if i == 0 else 0.0) for i, label in enumerate(labels)}
            if qtype == "choice":
                answers[name] = {
                    "type": "choice",
                    "choice": labels[0],
                    "probabilities": probabilities,
                    "confidence": 1.0,
                }
            else:
                answers[name] = {
                    "type": "score",
                    "score": 0.0,
                    "probabilities": probabilities,
                    "confidence": 1.0,
                    "legend": {label: "" for label in labels},
                }
        # 想定モデルが書かれていれば、それに合わせて返す（model の突合を通すため）。
        return JudgeResponse(
            model=request.model or self.model, answers=answers, usage=dict(self.usage)
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
