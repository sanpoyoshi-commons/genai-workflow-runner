"""委譲アダプタの例外が、利用者に届くエラー契約（`error.reason`）に載ることを固定する。

**なぜ要るか。** `TransportError` / `EnvelopeError` は `app.invoke` が捕捉する
`ContractError` / `RunnerError` のどちらでもない。包まないと `/invoke` が 500 を返し、
呼び出し側（源内OSS の Web）に何が表示されるかが決まらない。

固定する不変条件は 3 つ。

1. 委譲先由来の 13 コードが、公開の 4 コードへ**決まった対応表どおり**に畳まれる。
2. 畳んだ結果が `app.invoke` の `error.reason` に載り、利用者向けの日本語が返る。
3. **応答に url・host・応答本文・委譲先のエラー文が現れない**（値は運用者が見る面にだけ残す）。
"""

import json
import socket

import pytest

from gwr.adapters.code_interpreter import CodeInterpreterAdapter, CodeInterpreterRequest
from gwr.adapters.delegate_errors import classify
from gwr.adapters.envelope_client import EnvelopeError
from gwr.adapters.llm import LLMRequest
from gwr.adapters.retrieval import RetrievalRequest
from gwr.adapters.transport import TransportError
from gwr.app import WorkflowApp

# 委譲先由来のコード（実装の全数）→ 畳み先。ここが対応表の正。
TRANSPORT_CODES = {
    "ENDPOINT_BAD_SCHEME": "DELEGATE_ENDPOINT_INVALID",
    "ENDPOINT_NO_HOST": "DELEGATE_ENDPOINT_INVALID",
    "ENDPOINT_NOT_HTTPS": "DELEGATE_ENDPOINT_INVALID",
    "ENDPOINT_NOT_PUBLIC": "DELEGATE_ENDPOINT_INVALID",
    "KEYCLOAK_TOKEN_ERROR": "DELEGATE_AUTH_FAILED",
    "KEYCLOAK_TOKEN_BAD_JSON": "DELEGATE_AUTH_FAILED",
    "KEYCLOAK_TOKEN_MISSING": "DELEGATE_AUTH_FAILED",
    "HTTP_TIMEOUT": "DELEGATE_TIMEOUT",
    "HTTP_TRANSPORT_ERROR": "DELEGATE_FAILED",
    "LLM_HTTP_ERROR": "DELEGATE_FAILED",
}
ENVELOPE_CODES = {
    "POLL_TIMEOUT": "DELEGATE_TIMEOUT",
    "ASYNC_NO_STATUS_URL": "DELEGATE_FAILED",
    "HTTP_ERROR": "DELEGATE_FAILED",
    "DELEGATE_ERROR": "DELEGATE_FAILED",
}
# HTTP ステータスからの分類（対応表の正）。
STATUS_MAP = {
    401: "DELEGATE_AUTH_FAILED",
    403: "DELEGATE_AUTH_FAILED",
    429: "DELEGATE_TIMEOUT",
    400: "DELEGATE_FAILED",
    404: "DELEGATE_FAILED",
    409: "DELEGATE_FAILED",
    500: "DELEGATE_FAILED",
    503: "DELEGATE_FAILED",
}

_LLM_FLOW = """
version = "1"

[[inputs]]
key = "q"
type = "text"
label = "質問"

[[steps]]
id = "ask"
type = "llm"
in = { prompt = "$.vars.q" }
out = "vars.answer"

[[outputs]]
key = "answer"
type = "text"
from = "$.vars.answer"
"""


class BoomLLMAdapter:
    """委譲先の失敗を再現するアダプタ（本番アダプタと同じ例外を投げる）。"""

    def __init__(self, error):
        self.error = error

    def predict(self, request: LLMRequest):
        raise self.error


class BoomRetrievalAdapter:
    def __init__(self, error):
        self.error = error

    def search(self, request: RetrievalRequest):
        raise self.error


class BoomCodeInterpreterAdapter(CodeInterpreterAdapter):
    def __init__(self, error):
        self.error = error

    def run(self, request: CodeInterpreterRequest):
        raise self.error


def _invoke_with(error):
    app = WorkflowApp.from_toml(_LLM_FLOW, adapters={"llm": BoomLLMAdapter(error)})
    return app.invoke({"q": "これは質問です"})


# --- 1. 対応表 -----------------------------------------------------------
@pytest.mark.parametrize(("reason", "expected"), sorted(TRANSPORT_CODES.items()))
def test_transport_codes_are_folded(reason, expected):
    assert classify(reason) == expected


@pytest.mark.parametrize(("reason", "expected"), sorted(ENVELOPE_CODES.items()))
def test_envelope_codes_are_folded(reason, expected):
    assert classify(reason) == expected


@pytest.mark.parametrize(("status", "expected"), sorted(STATUS_MAP.items()))
def test_http_status_decides_when_reason_is_generic(status, expected):
    """委譲先が応答を返した場合は status で分ける（401/403＝認証・429＝混雑）。"""
    assert classify("HTTP_ERROR", status) == expected
    assert classify("DELEGATE_ERROR", status) == expected


def test_reason_wins_over_status():
    """Keycloak は 401 以外でも認証の失敗。理由コードの方が確か。"""
    assert classify("KEYCLOAK_TOKEN_MISSING", 200) == "DELEGATE_AUTH_FAILED"
    assert classify("ENDPOINT_NOT_PUBLIC", 500) == "DELEGATE_ENDPOINT_INVALID"


def test_unknown_code_falls_back_to_failed():
    assert classify("SOMETHING_NEW") == "DELEGATE_FAILED"


# --- 2. 応答に載ること ---------------------------------------------------
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TransportError("ENDPOINT_NOT_PUBLIC", "host=10.0.0.5"), "DELEGATE_ENDPOINT_INVALID"),
        (TransportError("KEYCLOAK_TOKEN_ERROR", "status=401 body=xx", 401), "DELEGATE_AUTH_FAILED"),
        (TransportError("HTTP_TIMEOUT", "error=timed out"), "DELEGATE_TIMEOUT"),
        (TransportError("HTTP_TRANSPORT_ERROR", "error=refused"), "DELEGATE_FAILED"),
        (EnvelopeError("POLL_TIMEOUT", "attempts=30"), "DELEGATE_TIMEOUT"),
        (EnvelopeError("HTTP_ERROR", "status=429", None, 429), "DELEGATE_TIMEOUT"),
        (EnvelopeError("HTTP_ERROR", "status=503", None, 503), "DELEGATE_FAILED"),
        (EnvelopeError("DELEGATE_ERROR", "boom", {"trace": "x"}), "DELEGATE_FAILED"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__ + ":" + v.reason,
)
def test_invoke_returns_folded_reason(error, expected):
    result = _invoke_with(error)
    assert result["error"]["reason"] == expected
    assert result["outputs"], "利用者向けメッセージが空"
    assert result["artifacts"] == []


def test_user_message_is_rendered_not_generic():
    result = _invoke_with(TransportError("HTTP_TIMEOUT", "error=timed out"))
    assert result["outputs"] != "処理を完了できませんでした。"
    assert "再実行" in result["outputs"]


@pytest.mark.parametrize("node", ["retrieval", "code_interpreter"])
def test_other_delegate_nodes_are_wrapped_too(node):
    flow = _LLM_FLOW.replace('type = "llm"', f'type = "{node}"')
    if node == "retrieval":
        flow = flow.replace('in = { prompt = "$.vars.q" }', 'in = { query = "$.vars.q" }')
        adapter = BoomRetrievalAdapter(EnvelopeError("HTTP_ERROR", "status=500", None, 500))
    else:
        flow = flow.replace(
            'in = { prompt = "$.vars.q" }',
            'in = { files = "$.vars.q" }\ninstruction = "集計する"',
        )
        adapter = BoomCodeInterpreterAdapter(TransportError("HTTP_TIMEOUT", "error=timed out"))
    app = WorkflowApp.from_toml(flow, adapters={node: adapter})
    result = app.invoke({"q": "これは質問です"})
    assert result["error"]["reason"].startswith("DELEGATE_")


# --- 3. 値が漏れないこと -------------------------------------------------
SECRETS = [
    "https://internal.example.test/v1/chat",  # url
    "10.0.0.5",  # host
    "eyJhbGciOiJIUzI1NiJ9.secret.payload",  # トークン本文
    "model not found: gpt-internal",  # 委譲先のエラー本文
]


@pytest.mark.parametrize("secret", SECRETS)
def test_response_never_contains_delegate_values(secret):
    """応答全体を文字列化し、投入した値が 1 文字も含まれないことを見る。"""
    errors = [
        TransportError("LLM_HTTP_ERROR", f"status=500 body={secret}", 500),
        TransportError("ENDPOINT_NOT_PUBLIC", f"host={secret}"),
        TransportError("HTTP_TRANSPORT_ERROR", f"error=connect to {secret} failed"),
        EnvelopeError("DELEGATE_ERROR", secret, {"detail": secret}),
        EnvelopeError("HTTP_ERROR", f"status=500 {secret}", {"url": secret}, 500),
    ]
    for error in errors:
        result = _invoke_with(error)
        dumped = json.dumps(result, ensure_ascii=False)
        assert secret not in dumped, f"{error.reason} の応答に値が漏れている: {dumped}"


def test_wrapped_message_carries_only_the_origin_code():
    """包む際に持ち越すのは元のクラス名と理由コードだけ（運用者向けの手がかり）。"""
    from gwr.adapters.delegate_errors import delegate_errors
    from gwr.nodes.base import NodeError

    with pytest.raises(NodeError) as excinfo:
        with delegate_errors():
            raise TransportError("ENDPOINT_NOT_PUBLIC", "host=10.0.0.5")
    assert excinfo.value.message == "TransportError:ENDPOINT_NOT_PUBLIC"
    assert "10.0.0.5" not in excinfo.value.message
    assert isinstance(excinfo.value.__cause__, TransportError)  # 生値は連鎖にだけ残る


# --- 4. 実アダプタ＋塞いだソケット ---------------------------------------
def test_real_adapter_against_a_closed_socket():
    """本番アダプタ＋実トランスポートで、繋がらない相手に投げたときの応答を見る。

    ループバックの閉じたポートへ向ける（外部ネットワークには出ない）。
    """
    pytest.importorskip("httpx")
    from gwr.adapters.gennai import GennaiLLMAdapter
    from gwr.adapters.transport import HttpxTransport

    with socket.socket() as probe:  # 空きポートを 1 つ確保してすぐ閉じる
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    adapter = GennaiLLMAdapter(
        HttpxTransport(),
        f"http://127.0.0.1:{port}/v1/chat",
        api_key="k",
        allow_insecure=True,  # ループバックを許可（運用者注入の内部宛と同じ扱い）
    )
    app = WorkflowApp.from_toml(_LLM_FLOW, adapters={"llm": adapter})
    result = app.invoke({"q": "これは質問です"})

    assert result["error"]["reason"] in {"DELEGATE_FAILED", "DELEGATE_TIMEOUT"}
    assert "127.0.0.1" not in json.dumps(result, ensure_ascii=False)
