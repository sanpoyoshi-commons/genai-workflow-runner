"""委譲アダプタの例外を、利用者に届くエラー契約（理由コード）へ載せ替える。

**なぜ要るか。** `TransportError`（接続層）と `EnvelopeError`（委譲先の応答）は、
`app.invoke` が捕捉する `ContractError` / `RunnerError` のどちらでもない。包まないと
`/invoke` が 500 を返し、**呼び出し側（源内OSS の Web）に何が表示されるかが決まらない**。
ノード層で `NodeError` に包めば `Runner` が `RunnerError` へ載せ替え、`error.reason` と
利用者向けの日本語メッセージが確実に返る。

**何を落とすか。** 委譲先由来のコードは 13 種あるが、そのまま公開の体系へ入れない。
**利用者・運用者が何をすれば直るか**の 4 つに畳む。

| 畳み先 | 誰が直すか |
|---|---|
| `DELEGATE_ENDPOINT_INVALID` | 運用者（接続先の環境変数） |
| `DELEGATE_AUTH_FAILED` | 運用者（資格情報） |
| `DELEGATE_TIMEOUT` | 利用者（時間をおいて再実行） |
| `DELEGATE_FAILED` | 利用者は再実行、運用者はログ |

**値を持ち越さない。** 元例外の detail には url・host・応答本文が入る。包む際に運び込むのは
**元の例外クラス名と理由コードだけ**で、生値は `raise ... from e` の連鎖（トレースバック＝
運用者が見る面）にのみ残す。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from gwr.adapters.envelope_client import EnvelopeError
from gwr.adapters.transport import TransportError
from gwr.nodes.base import NodeError

# 接続先の設定誤り（SSRF ガードが弾いたものを含む）。
_ENDPOINT_PREFIX = "ENDPOINT_"
# 委譲先の認証（オンプレ版の Keycloak トークン取得）。
_AUTH_PREFIX = "KEYCLOAK_"
# 時間内に終わらなかったもの。
_TIMEOUT_REASONS = frozenset({"HTTP_TIMEOUT", "POLL_TIMEOUT"})
# HTTP ステータスからの分類（委譲先が応答を返した場合のみ status が入る）。
_AUTH_STATUSES = frozenset({401, 403})
_BUSY_STATUSES = frozenset({429})


def classify(reason: str, status: int | None = None) -> str:
    """委譲先由来の理由コード＋HTTP ステータスを、公開の 4 コードへ畳む。

    理由コードによる分類を先に見る（Keycloak は 401 以外でも認証の失敗なので、
    ステータスより理由コードの方が確か）。ステータスは委譲先が応答を返した場合のみ。
    """
    if reason.startswith(_ENDPOINT_PREFIX):
        return "DELEGATE_ENDPOINT_INVALID"
    if reason.startswith(_AUTH_PREFIX):
        return "DELEGATE_AUTH_FAILED"
    if reason in _TIMEOUT_REASONS:
        return "DELEGATE_TIMEOUT"
    if status in _AUTH_STATUSES:
        return "DELEGATE_AUTH_FAILED"
    if status in _BUSY_STATUSES:
        return "DELEGATE_TIMEOUT"
    return "DELEGATE_FAILED"


@contextmanager
def delegate_errors() -> Iterator[None]:
    """委譲アダプタの呼び出しを囲み、失敗を `NodeError` に包み替える。

    囲むのは**アダプタ呼び出しの 1 行だけ**にする（前段の `ADAPTER_MISSING` などを
    巻き込まないため）。理由コードは文字列リテラルで書く
    （`tests/test_user_messages.py` の AST 抽出が全数を拾えるように）。
    """
    try:
        yield
    except (TransportError, EnvelopeError) as e:
        origin = f"{type(e).__name__}:{e.reason}"  # 値は載せない（url/host/本文を運ばない）
        code = classify(e.reason, e.status)
        if code == "DELEGATE_ENDPOINT_INVALID":
            raise NodeError("DELEGATE_ENDPOINT_INVALID", origin) from e
        if code == "DELEGATE_AUTH_FAILED":
            raise NodeError("DELEGATE_AUTH_FAILED", origin) from e
        if code == "DELEGATE_TIMEOUT":
            raise NodeError("DELEGATE_TIMEOUT", origin) from e
        raise NodeError("DELEGATE_FAILED", origin) from e
