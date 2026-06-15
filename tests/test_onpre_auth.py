"""onpre 認証足場（Keycloak password grant）：トークン取得・整形・失敗（モック）。"""

import json

import pytest

from gwr.adapters.transport import HttpResponse, TransportError
from gwr.onpre_auth import (
    KeycloakTokenProvider,
    bearer,
    fetch_keycloak_token,
    fetch_keycloak_token_info,
)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, method, url, *, headers, json=None, data=None):
        self.requests.append(
            {"method": method, "url": url, "headers": headers, "json": json, "data": data}
        )
        return self.response


TOKEN_URL = "http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token"


def test_fetch_token_password_grant_form_body():
    t = FakeTransport(HttpResponse(200, json.dumps({"access_token": "TOK", "expires_in": 60})))
    tok = fetch_keycloak_token(
        t, TOKEN_URL, client_id="genai-web-dev", username="admin", password="pw"
    )
    assert tok == "TOK"
    req = t.requests[0]
    assert req["method"] == "POST"
    assert req["headers"]["content-type"] == "application/x-www-form-urlencoded"
    # フォーム body（urlencoded）に grant_type/client_id/credentials が載る
    assert "grant_type=password" in req["data"]
    assert "client_id=genai-web-dev" in req["data"]
    assert "username=admin" in req["data"]
    assert req["json"] is None  # JSON ではなくフォーム送信


def test_fetch_token_http_error_raises():
    t = FakeTransport(HttpResponse(401, json.dumps({"error": "invalid_grant"})))
    with pytest.raises(TransportError) as ei:
        fetch_keycloak_token(t, TOKEN_URL, client_id="c", username="u", password="bad")
    assert ei.value.reason == "KEYCLOAK_TOKEN_ERROR"


def test_fetch_token_missing_access_token_raises():
    t = FakeTransport(HttpResponse(200, json.dumps({"token_type": "Bearer"})))
    with pytest.raises(TransportError) as ei:
        fetch_keycloak_token(t, TOKEN_URL, client_id="c", username="u", password="p")
    assert ei.value.reason == "KEYCLOAK_TOKEN_MISSING"


def test_bearer_shape():
    assert bearer("TOK") == {"authorization": "Bearer TOK"}


class SeqTransport:
    """キューした応答を順に返すモック（プロバイダの再取得回数を検証する用）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers, json=None, data=None):
        self.requests.append({"method": method, "url": url, "data": data})
        return self._responses.pop(0)


def _token_resp(token, expires_in=None):
    payload = {"access_token": token}
    if expires_in is not None:
        payload["expires_in"] = expires_in
    return HttpResponse(200, json.dumps(payload))


def test_fetch_token_info_returns_token_and_ttl():
    t = SeqTransport([_token_resp("TOK", 120)])
    tok, ttl = fetch_keycloak_token_info(
        t, TOKEN_URL, client_id="genai-web-dev", username="admin", password="pw"
    )
    assert tok == "TOK"
    assert ttl == 120.0


def test_fetch_token_info_defaults_ttl_when_missing():
    t = SeqTransport([_token_resp("TOK")])  # expires_in 無し
    _tok, ttl = fetch_keycloak_token_info(
        t, TOKEN_URL, client_id="c", username="u", password="p"
    )
    assert ttl == 60.0


def test_provider_caches_then_refetches_after_expiry():
    """provider はトークンを expires_in でキャッシュし、失効（margin 考慮）後に再取得する。"""
    now = {"t": 0.0}
    t = SeqTransport([_token_resp("T1", 100), _token_resp("T2", 100)])
    p = KeycloakTokenProvider(
        t, TOKEN_URL, client_id="genai-web-dev", username="admin", password="pw",
        refresh_margin=30.0, clock=lambda: now["t"],
    )
    assert p() == {"authorization": "Bearer T1"}  # 初回 fetch（deadline=0+100-30=70）
    now["t"] = 50.0
    assert p() == {"authorization": "Bearer T1"}  # まだ有効＝キャッシュ
    assert len(t.requests) == 1
    now["t"] = 80.0  # deadline 70 超過 → 再取得
    assert p() == {"authorization": "Bearer T2"}
    assert len(t.requests) == 2
    # password grant の client_id がフォーム body に載る（provider 経由でも正しい）
    assert "client_id=genai-web-dev" in t.requests[0]["data"]
