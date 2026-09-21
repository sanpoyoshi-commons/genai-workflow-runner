"""オンプレ版専用の認証足場：Keycloak password grant でアクセストークンを取得する。

**位置づけ（重要）**：源内OSS クラウド版の Web の委譲先（LLM/RAG/CI）は、源内OSS に
登録済みの ExApp 公開エンドポイント＋`x-api-key` 認証で叩く（封筒クライアントの本番経路）。
本モジュールは不要。オンプレ版は同じ封筒ルートを中央 `api` に集約し **Keycloak Bearer** で
ゲートしているため、**実機テスト（smoke / tests/real）でのみ**ここでトークンを取得し
`Authorization: Bearer ...` を `extra_headers` 経由で封筒に載せる。

本番アダプタ（`gwr.adapters.gennai`）にはこの依存を持ち込まない。認証はヘッダ1個の差で
あり、body・レスポンス契約は cloud/オンプレ版で同一（`{inputs:{...}}` → `{outputs, artifacts?}`）。

参照：オンプレ版 `genai-deploy-onpre/scripts/e2e-law-rag-http.mjs`（genai-web-dev password grant）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlencode

from gwr.adapters.transport import HttpTransport, TransportError


def fetch_keycloak_token_info(
    transport: HttpTransport,
    token_url: str,
    *,
    client_id: str,
    username: str,
    password: str,
) -> tuple[str, float]:
    """password grant し ``(access_token, 有効期間[秒])`` を返す。

    onpre 例：token_url=``http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token``、
    client_id=``genai-web-dev``（public・directAccessGrants 有効・audience=genai-ai-api）。
    返るトークンの audience が `genai-ai-api` なので api の requireAuth を通る。
    expires_in が無い/不正なら保守的に 60 秒とみなす（KeycloakTokenProvider の早め再取得用）。
    """
    body = urlencode(
        {
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        }
    )
    resp = transport.request(
        "POST",
        token_url,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data=body,
    )
    if resp.status >= 400:
        raise TransportError(
            "KEYCLOAK_TOKEN_ERROR",
            f"status={resp.status} body={resp.body_text[:200]}",
            status=resp.status,
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise TransportError(
            "KEYCLOAK_TOKEN_BAD_JSON", f"body={resp.body_text[:200]}", status=resp.status
        ) from e
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise TransportError(
            "KEYCLOAK_TOKEN_MISSING", f"body={resp.body_text[:200]}", status=resp.status
        )
    raw_ttl = payload.get("expires_in")
    ttl = float(raw_ttl) if isinstance(raw_ttl, (int, float)) and raw_ttl > 0 else 60.0
    return token, ttl


def fetch_keycloak_token(
    transport: HttpTransport,
    token_url: str,
    *,
    client_id: str,
    username: str,
    password: str,
) -> str:
    """Keycloak の token 端点で password grant し access_token を返す（単発・smoke 用）。

    有効期間を扱いたい常駐 serve は KeycloakTokenProvider を使う。
    """
    token, _ttl = fetch_keycloak_token_info(
        transport, token_url, client_id=client_id, username=username, password=password
    )
    return token


def bearer(token: str) -> dict[str, str]:
    """access_token を封筒の extra_headers 形（Authorization: Bearer）に整形する。"""
    return {"authorization": f"Bearer {token}"}


class KeycloakTokenProvider:
    """常駐 serve 向けの Bearer 供給源。token を expires_in でキャッシュし失効前に再取得する。

    serve は extra_headers にこの callable を渡し、invoke 毎に Bearer を解決する。
    起動時 1 回固定だと短寿命 access_token が失効し、時間経過後の委譲呼びが 401 になるため。
    refresh_margin 秒の余裕を持って先に再取得。clock はテスト差し替え可（既定 time.monotonic）。
    """

    def __init__(
        self,
        transport: HttpTransport,
        token_url: str,
        *,
        client_id: str,
        username: str,
        password: str,
        refresh_margin: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._token_url = token_url
        self._client_id = client_id
        self._username = username
        self._password = password
        self._refresh_margin = refresh_margin
        self._clock = clock
        self._token: str | None = None
        self._deadline = 0.0

    def token(self) -> str:
        now = self._clock()
        if self._token is None or now >= self._deadline:
            token, ttl = fetch_keycloak_token_info(
                self._transport,
                self._token_url,
                client_id=self._client_id,
                username=self._username,
                password=self._password,
            )
            self._token = token
            self._deadline = now + max(0.0, ttl - self._refresh_margin)
        return self._token

    def headers(self) -> dict[str, str]:
        return bearer(self.token())

    def __call__(self) -> dict[str, str]:
        return self.headers()
