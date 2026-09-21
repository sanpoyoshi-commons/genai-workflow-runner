"""genai-web 共通プロトコル封筒のクライアント。

送信＝inputs 整形、受信＝outputs/artifacts/usage の取り出しを担う。

委譲先 ExApp（RAG / CI）の公開 HTTPS エンドポイントを直接叩く。
源内OSS の Web（invokeExApp）は介さない。
- リクエスト : POST {endpoint} body={inputs[, sessionId]} ヘッダ x-api-key + x-user-id
- 同期       : {outputs[, artifacts]}
- 非同期     : 202 → {request_id, status_url} を COMPLETED/ERROR までアダプタ内でポーリング
gwr は無常駐ステートレスなので外部キューを持たず、同期 /invoke 内でポーリングを完結させる。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from gwr.adapters.transport import HttpResponse, HttpTransport, assert_public_https
from gwr.adapters.usage import normalize_usage
from gwr.datatypes import FileRef, make_file_ref

PENDING = "PENDING"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
ERROR = "ERROR"

# アダプタ内ポーリングのバックオフ（秒）。同期 /invoke 内で完結する短めの既定。
DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 15.0)

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
}


class EnvelopeError(Exception):
    """委譲先からの ERROR／HTTP 失敗／ポーリング打ち切り。reason はコード。

    message / details は運用者が原因を特定するための情報で、`key=value` 形式に揃える。
    **利用者への応答には出さない**（ノード層で NodeError に包み替える際に捨てる
    → gwr.adapters.delegate_errors）。status は委譲先の HTTP ステータスで、
    包み替えの分類に使う（401/403 → 認証、429 → 混雑、他 → 失敗）。
    """

    def __init__(
        self, reason: str, message: str = "", details: Any = None, status: int | None = None
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason
        self.details = details
        self.status = status


@dataclass
class EnvelopeResult:
    outputs: str
    artifacts: list[FileRef] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    status: str = COMPLETED
    raw: dict[str, Any] = field(default_factory=dict)


def _mime_from_name(name: str) -> str:
    lower = name.lower()
    for ext, mime in _MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


def _parse_artifacts(data: dict[str, Any]) -> list[FileRef]:
    """artifacts[] を FileRef[] へ。contents（封筒）/ content（CI）双方を受理する。"""
    out: list[FileRef] = []
    for art in data.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        name = str(art.get("display_name", "artifact"))
        b64 = str(art.get("contents") or art.get("content") or "")
        out.append(make_file_ref(name, _mime_from_name(name), b64))
    return out


def _parse_body(resp: HttpResponse) -> dict[str, Any]:
    """JSON を取り出す。不正 JSON は {outputs: text} 扱い（invokeExApp の挙動に合わせる）。"""
    try:
        data = resp.json()
    except ValueError:
        return {"outputs": resp.body_text}
    if not isinstance(data, dict):
        return {"outputs": resp.body_text}
    return data


class EnvelopeClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        backoff: Sequence[float] = DEFAULT_BACKOFF,
        max_poll_attempts: int = 40,
        sleep: Callable[[float], None] = time.sleep,
        allow_insecure: bool = False,
    ) -> None:
        self.transport = transport
        self.backoff = tuple(backoff)
        self.max_poll_attempts = max_poll_attempts
        self.sleep = sleep
        self.allow_insecure = allow_insecure

    def invoke(
        self,
        endpoint: str,
        inputs: dict[str, Any],
        *,
        api_key: str,
        user_id: str = "",
        session_id: str | None = None,
        extra_headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> EnvelopeResult:
        assert_public_https(endpoint, allow_insecure=self.allow_insecure)
        headers = self._headers(api_key, user_id, extra_headers)
        body: dict[str, Any] = {"inputs": inputs}
        if session_id:
            body["sessionId"] = session_id
        resp = self.transport.request("POST", endpoint, headers=headers, json=body)
        return self._handle(endpoint, headers, resp)

    def _headers(
        self,
        api_key: str,
        user_id: str,
        extra_headers: dict[str, str] | Callable[[], dict[str, str]] | None = None,
    ) -> dict[str, str]:
        # 本番＝x-api-key（封筒の標準認証）。空キーは送らない。オンプレ版は Bearer を
        # extra_headers で
        # 載せる＝認証ヘッダの差し替え点で、本番の x-api-key 経路は無改変のまま。
        # extra_headers が callable のときは invoke 毎に解決する（オンプレ版は失効再取得するトークン
        # プロバイダを渡す＝起動時固定だと Bearer が失効して 401 になるため）。
        headers = {"content-type": "application/json"}
        if api_key:
            headers["x-api-key"] = api_key
        if user_id:
            headers["x-user-id"] = user_id
        resolved = extra_headers() if callable(extra_headers) else extra_headers
        if resolved:
            headers.update(resolved)
        return headers

    def _handle(
        self, endpoint: str, headers: dict[str, str], resp: HttpResponse
    ) -> EnvelopeResult:
        if resp.status >= 400:
            data = _parse_body(resp)
            err = data.get("error")
            if isinstance(err, dict):
                raise EnvelopeError(
                    "DELEGATE_ERROR",
                    str(err.get("message", "")),
                    err.get("details"),
                    status=resp.status,
                )
            raise EnvelopeError(
                "HTTP_ERROR", f"status={resp.status}", data.get("error", data), status=resp.status
            )

        data = _parse_body(resp)
        status = data.get("status")
        if resp.status == 202 or status in (PENDING, IN_PROGRESS):
            return self._poll(endpoint, headers, data)
        return self._finalize(data)

    def _poll(
        self, endpoint: str, headers: dict[str, str], initial: dict[str, Any]
    ) -> EnvelopeResult:
        status_url = initial.get("status_url")
        if not status_url:
            raise EnvelopeError("ASYNC_NO_STATUS_URL")
        url = urljoin(endpoint, status_url)
        for attempt in range(self.max_poll_attempts):
            self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
            resp = self.transport.request("GET", url, headers=headers)
            if resp.status >= 400:
                raise EnvelopeError("HTTP_ERROR", f"status={resp.status}", status=resp.status)
            data = _parse_body(resp)
            status = data.get("status")
            if status == COMPLETED:
                return self._finalize(data)
            if status == ERROR:
                self._raise_error(data)
        raise EnvelopeError("POLL_TIMEOUT", f"attempts={self.max_poll_attempts}")

    def _finalize(self, data: dict[str, Any]) -> EnvelopeResult:
        if data.get("status") == ERROR:
            self._raise_error(data)
        return EnvelopeResult(
            outputs=str(data.get("outputs", "")),
            artifacts=_parse_artifacts(data),
            usage=normalize_usage(data),
            status=str(data.get("status", COMPLETED)),
            raw=data,
        )

    def _raise_error(self, data: dict[str, Any]) -> None:
        err = data.get("error", {})
        if isinstance(err, dict):
            raise EnvelopeError("DELEGATE_ERROR", str(err.get("message", "")), err.get("details"))
        raise EnvelopeError("DELEGATE_ERROR", str(err))
