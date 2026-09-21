"""封筒クライアント＋トランスポート：同期/非同期/ERROR/SSRF/usage 正規化（モック）。"""

import json

import pytest

from gwr.adapters.envelope_client import EnvelopeClient, EnvelopeError
from gwr.adapters.transport import HttpResponse, TransportError, assert_public_https
from gwr.adapters.usage import normalize_usage


class FakeTransport:
    """スクリプト化した応答を順に返し、リクエストを記録するモックトランスポート。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers, json=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        return self._responses.pop(0)


def _json_resp(status, payload):
    return HttpResponse(status=status, body_text=json.dumps(payload, ensure_ascii=False))


def _client(responses):
    t = FakeTransport(responses)
    return EnvelopeClient(t, sleep=lambda _s: None), t  # sleep は無効化（テストで待たない）


# --- SSRF ガード --------------------------------------------------------
def test_assert_public_https_rejects_non_https():
    with pytest.raises(TransportError) as ei:
        assert_public_https("http://example.com/x")
    assert ei.value.reason == "ENDPOINT_NOT_HTTPS"


def test_assert_public_https_rejects_localhost_and_private_ip():
    for bad in ("https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x",
                "https://169.254.1.1/x"):
        with pytest.raises(TransportError) as ei:
            assert_public_https(bad)
        assert ei.value.reason == "ENDPOINT_NOT_PUBLIC"


def test_assert_public_https_allows_public_host():
    assert_public_https("https://api.example.gov.jp/rag")  # 例外が出ない


def test_assert_public_https_allow_insecure_permits_http_private():
    # 運用者注入の信頼済み委譲先（オンプレ版 の http://ollama:11434 等）を許可
    assert_public_https("http://ollama:11434/v1/chat/completions", allow_insecure=True)
    assert_public_https("http://10.0.0.5/x", allow_insecure=True)
    assert_public_https("http://localhost:8000/x", allow_insecure=True)
    # スキームは http/https のみ（それ以外は許可モードでも拒否）
    with pytest.raises(TransportError):
        assert_public_https("ftp://host/x", allow_insecure=True)


def test_invoke_rejects_private_endpoint():
    client, _ = _client([])
    with pytest.raises(TransportError):
        client.invoke("https://127.0.0.1/x", {"question": "q"}, api_key="k")


# --- 同期 ---------------------------------------------------------------
def test_sync_outputs_and_headers():
    client, t = _client([_json_resp(200, {"outputs": "答え"})])
    result = client.invoke(
        "https://api.example.com/rag", {"question": "q"}, api_key="KEY", user_id="u1"
    )
    assert result.outputs == "答え"
    req = t.requests[0]
    assert req["method"] == "POST"
    assert req["headers"]["x-api-key"] == "KEY"
    assert req["headers"]["x-user-id"] == "u1"
    assert req["json"] == {"inputs": {"question": "q"}}


def test_sync_completed_with_artifacts_both_content_keys():
    payload = {
        "status": "COMPLETED",
        "outputs": "done",
        "artifacts": [
            {"display_name": "a.png", "contents": "QQ=="},   # 封筒形（contents）
            {"display_name": "b.csv", "content": "Qg=="},    # CI 形（content）
        ],
    }
    client, _ = _client([_json_resp(200, payload)])
    result = client.invoke("https://api.example.com/ci", {"input_text": "x"}, api_key="k")
    assert [a["display_name"] for a in result.artifacts] == ["a.png", "b.csv"]
    assert result.artifacts[0]["contents"] == "QQ=="
    assert result.artifacts[0]["mime"] == "image/png"
    assert result.artifacts[1]["contents"] == "Qg=="


def test_session_id_included_when_given():
    client, t = _client([_json_resp(200, {"outputs": "x"})])
    client.invoke("https://api.example.com/x", {"q": 1}, api_key="k", session_id="s9")
    assert t.requests[0]["json"]["sessionId"] == "s9"


# --- 認証ヘッダ差し替え（オンプレ版 Bearer・本番無影響） ---------------------
def test_extra_headers_inject_bearer_and_omit_empty_api_key():
    """オンプレ版は Keycloak Bearer を extra_headers で載せる。空 api_key は送らない。"""
    client, t = _client([_json_resp(200, {"outputs": "ok"})])
    client.invoke(
        "https://api.example.com/x", {"question": "q"},
        api_key="", extra_headers={"authorization": "Bearer TOK"},
    )
    h = t.requests[0]["headers"]
    assert h["authorization"] == "Bearer TOK"
    assert "x-api-key" not in h  # 空キーは載せない


def test_extra_headers_carry_into_async_polling():
    """非同期ポーリングの GET にも認証ヘッダが引き継がれる。"""
    responses = [
        HttpResponse(202, json.dumps({"status": "PENDING", "status_url": "/status/r1"})),
        _json_resp(200, {"status": "COMPLETED", "outputs": "done"}),
    ]
    client, t = _client(responses)
    client.invoke(
        "https://api.example.com/ci", {"input_text": "x"},
        api_key="", extra_headers={"authorization": "Bearer TOK"},
    )
    assert t.requests[1]["method"] == "GET"
    assert t.requests[1]["headers"]["authorization"] == "Bearer TOK"


def test_extra_headers_callable_resolved_per_invoke():
    """extra_headers が callable のとき invoke 毎に解決される
    （オンプレ版 の失効再取得トークン用）。"""
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return {"authorization": f"Bearer T{calls['n']}"}

    client, t = _client([_json_resp(200, {"outputs": "a"}), _json_resp(200, {"outputs": "b"})])
    client.invoke("https://api.example.com/x", {"q": 1}, api_key="", extra_headers=provider)
    client.invoke("https://api.example.com/x", {"q": 2}, api_key="", extra_headers=provider)
    assert t.requests[0]["headers"]["authorization"] == "Bearer T1"
    assert t.requests[1]["headers"]["authorization"] == "Bearer T2"  # 毎回再解決
    assert calls["n"] == 2


def test_api_key_path_unchanged_without_extra_headers():
    """本番＝extra_headers なしなら従来どおり x-api-key のみ。"""
    client, t = _client([_json_resp(200, {"outputs": "x"})])
    client.invoke("https://api.example.com/x", {"q": 1}, api_key="KEY")
    h = t.requests[0]["headers"]
    assert h["x-api-key"] == "KEY"
    assert "authorization" not in h


# --- 非同期ポーリング ---------------------------------------------------
def test_async_polls_until_completed():
    responses = [
        HttpResponse(202, json.dumps({"status": "PENDING", "request_id": "r1",
                                      "status_url": "/status/r1"})),
        _json_resp(200, {"status": "IN_PROGRESS", "progress": "50%"}),
        _json_resp(200, {"status": "COMPLETED", "outputs": "完了",
                         "artifacts": [{"display_name": "r.png", "contents": "QQ=="}]}),
    ]
    client, t = _client(responses)
    result = client.invoke("https://api.example.com/ci", {"input_text": "x"}, api_key="k")
    assert result.outputs == "完了"
    assert result.status == "COMPLETED"
    # status_url を endpoint 基準で解決して GET ポーリングしている
    assert t.requests[1]["method"] == "GET"
    assert t.requests[1]["url"] == "https://api.example.com/status/r1"


def test_async_error_status_raises():
    responses = [
        HttpResponse(202, json.dumps({"status": "PENDING", "status_url": "/status/r1"})),
        _json_resp(200, {"status": "ERROR", "error": {"message": "失敗", "details": "x"}}),
    ]
    client, _ = _client(responses)
    with pytest.raises(EnvelopeError) as ei:
        client.invoke("https://api.example.com/ci", {"input_text": "x"}, api_key="k")
    assert ei.value.reason == "DELEGATE_ERROR"
    assert ei.value.details == "x"


def test_async_poll_timeout():
    responses = [HttpResponse(202, json.dumps({"status": "PENDING", "status_url": "/s"}))]
    responses += [_json_resp(200, {"status": "IN_PROGRESS"}) for _ in range(5)]
    t = FakeTransport(responses)
    client = EnvelopeClient(t, sleep=lambda _s: None, max_poll_attempts=3)
    with pytest.raises(EnvelopeError) as ei:
        client.invoke("https://api.example.com/x", {"q": 1}, api_key="k")
    assert ei.value.reason == "POLL_TIMEOUT"


# --- エラー -------------------------------------------------------------
def test_http_4xx_with_error_body():
    client, _ = _client([_json_resp(400, {"error": "Invalid request: input_text required"})])
    with pytest.raises(EnvelopeError) as ei:
        client.invoke("https://api.example.com/x", {}, api_key="k")
    assert ei.value.reason == "HTTP_ERROR"


def test_invalid_json_body_treated_as_outputs_text():
    client, _ = _client([HttpResponse(200, "プレーンテキスト応答")])
    result = client.invoke("https://api.example.com/x", {"q": 1}, api_key="k")
    assert result.outputs == "プレーンテキスト応答"


# --- usage 正規化 -------------------------------------------------------
def test_usage_openai_chat():
    u = normalize_usage(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    )
    assert u == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}


def test_usage_openai_responses():
    u = normalize_usage({"usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}})
    assert u == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


def test_usage_metadata_list_gcp_and_aws():
    gcp = normalize_usage({"usageMetadata": [
        {"tokens": {"promptTokenCount": 100, "candidatesTokenCount": 20}},
        {"tokens": {"promptTokenCount": 50, "candidatesTokenCount": 10}},
    ]})
    assert gcp == {"input_tokens": 150, "output_tokens": 30, "total_tokens": 180}
    aws = normalize_usage({"usageMetadata": [{"tokens": {"inputTokens": 5, "outputTokens": 2}}]})
    assert aws == {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}


def test_usage_absent_returns_empty():
    assert normalize_usage({"outputs": "x"}) == {}
