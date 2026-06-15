"""HTTP トランスポート抽象＋SSRF ガード。

委譲先 API 接続の最下層。実装は注入式（テストはモックトランスポート）。
委譲先 URL は公開 HTTPS のみ許可する（genai-web の assertPublicEndpointUrl 相当）。
"""

from __future__ import annotations

import ipaddress
import json as _json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse


class TransportError(Exception):
    """接続層の失敗（SSRF 拒否・ネットワーク失敗など）。reason はコード。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def assert_public_https(url: str, allow_insecure: bool = False) -> None:
    """公開 HTTPS エンドポイントのみ許可（SSRF 対策）。

    https 以外・ホスト無し・localhost・プライベート/ループバック/リンクローカル等の
    IP リテラルは拒否する。ホスト名（非 IP）は DNS 解決せず形のみ検証する。

    allow_insecure=True は「運用者が設定した信頼済みの委譲先」向けの緩和（オンプレ版 等で
    http://ollama:11434 のような内部宛を許可）。フロー定義者が任意 URL を渡す経路は無い
    （http ノードを持たない）ため、対象は運用者注入の endpoint のみ。
    """
    parsed = urlparse(url)
    if allow_insecure:
        if parsed.scheme not in ("http", "https"):
            raise TransportError("ENDPOINT_BAD_SCHEME", url)
        if not parsed.hostname:
            raise TransportError("ENDPOINT_NO_HOST", url)
        return
    if parsed.scheme != "https":
        raise TransportError("ENDPOINT_NOT_HTTPS", url)
    host = parsed.hostname
    if not host:
        raise TransportError("ENDPOINT_NO_HOST", url)
    if host.lower() == "localhost":
        raise TransportError("ENDPOINT_NOT_PUBLIC", host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # ホスト名（IP リテラルでない）は許容
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise TransportError("ENDPOINT_NOT_PUBLIC", host)


@dataclass
class HttpResponse:
    status: int
    body_text: str
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return _json.loads(self.body_text)


@runtime_checkable
class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: Any | None = None,
        data: str | None = None,
    ) -> HttpResponse: ...


class HttpxTransport:
    """httpx ベースの本番トランスポート（任意依存・遅延 import）。

    タイムアウト既定 300s（Azure CI 実装の既定に合わせる）。テストでは使わない。
    """

    def __init__(self, timeout: float = 300.0, verify: bool = True) -> None:
        self.timeout = timeout
        # verify=False は自己署名 TLS（オンプレ版 の :8443 等）向け。既定は検証あり。
        self.verify = verify

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: Any | None = None,
        data: str | None = None,
    ) -> HttpResponse:
        import httpx  # 遅延 import（gwr[http] extra）

        # data= はフォーム body（application/x-www-form-urlencoded）。Keycloak token 端点向け。
        kwargs: dict[str, Any] = {"content": data} if data is not None else {"json": json}
        try:
            resp = httpx.request(
                method, url, headers=headers, timeout=self.timeout,
                verify=self.verify, **kwargs,
            )
        except httpx.HTTPError as e:  # 接続/タイムアウト等
            raise TransportError("HTTP_TRANSPORT_ERROR", str(e)) from e
        return HttpResponse(
            status=resp.status_code,
            body_text=resp.text,
            headers={k.lower(): v for k, v in resp.headers.items()},
        )
