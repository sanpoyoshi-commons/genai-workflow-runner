"""L1 委譲先個別スモークの env-gated 実機テスト（オンプレ版）。

接続 env 未設定なら skip＝通常の `pytest` は緑のまま。実機ではオンプレ版を起動し、gwr
コンテナ内（同一 docker network）で接続 env を与えて実行する想定。

RAG（オンプレ版 law-rag）:
    GWR_RAG_ENDPOINT=https://api:3443/api/law-rag/query
    GWR_KC_TOKEN_URL=http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token
    GWR_KC_USER=<user> GWR_KC_PASS=<pass>  GWR_VERIFY_TLS=false
    （任意）GWR_RAG_OK_QUESTION / GWR_RAG_EMPTY_QUESTION で確認質問を上書き
CI（オンプレ版 code-interpreter）:
    GWR_CI_ENDPOINT=https://api:3443/api/code-interpreter/responses ＋ 上記 KC/TLS

自己署名 TLS は GWR_VERIFY_TLS=false、内部 http 宛は GWR_ALLOW_INSECURE=true。
"""

from __future__ import annotations

import base64
import io
import os

import pytest

from gwr.adapters.code_interpreter import CodeInterpreterRequest
from gwr.adapters.retrieval import RetrievalRequest
from gwr.cli import _wire_delegate_adapters
from gwr.datatypes import make_file_ref

pytestmark = pytest.mark.real

# 既定の確認質問（law-rag。OK＝法令を特定できる／EMPTY＝特定できず該当なし）。
OK_QUESTION = os.environ.get("GWR_RAG_OK_QUESTION", "解雇の予告は原則何日前に必要ですか？")
EMPTY_QUESTION = os.environ.get(
    "GWR_RAG_EMPTY_QUESTION", "ねこねこ星雲の超空間航法に関する架空の取り決めを教えて"
)


def _adapters() -> dict:
    adapters: dict = {}
    _wire_delegate_adapters(adapters)
    return adapters


def test_l1_rag_ok_returns_answer() -> None:
    if not os.environ.get("GWR_RAG_ENDPOINT"):
        pytest.skip("GWR_RAG_ENDPOINT 未設定（オンプレ版 実機 RAG なし）")
    rag = _adapters()["retrieval"]
    resp = rag.search(RetrievalRequest(source="law", query=OK_QUESTION, top_k=5))
    assert resp.docs, "法令を特定できる質問なのに docs が空"
    assert resp.docs[0]["content"].strip()


def test_l1_rag_empty_is_no_match() -> None:
    if not os.environ.get("GWR_RAG_ENDPOINT"):
        pytest.skip("GWR_RAG_ENDPOINT 未設定（オンプレ版 実機 RAG なし）")
    rag = _adapters()["retrieval"]
    resp = rag.search(RetrievalRequest(source="law", query=EMPTY_QUESTION, top_k=5))
    # 法令を特定できない＝空回答 → docs=[]（捏造しない）。フローは branch で該当なしへ。
    assert resp.docs == []


def test_l1_ci_runs_and_returns_output() -> None:
    if not os.environ.get("GWR_CI_ENDPOINT"):
        pytest.skip("GWR_CI_ENDPOINT 未設定（オンプレ版 実機 CI なし）")
    ci = _adapters()["code_interpreter"]
    buf = io.StringIO()
    buf.write("category,value\nA,10\nB,20\nA,5\n")
    b64 = base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")
    files = [make_file_ref("data.csv", "text/csv", b64)]
    resp = ci.run(
        CodeInterpreterRequest(
            instruction="category ごとの value 合計を表で示してください。", files=files
        )
    )
    assert resp.output.strip() or resp.artifacts
