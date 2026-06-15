"""usage 正規化（委譲先ベンダ間でキー命名が非互換なため共通スキーマへ寄せる）。

正規化後は {input_tokens, output_tokens, total_tokens}。

対応する入力形：
- OpenAI Chat   : usage{prompt_tokens, completion_tokens, total_tokens}
- OpenAI Responses: usage{input_tokens, output_tokens, total_tokens}
- AWS/GCP        : usageMetadata[]{tokens:{inputTokens|promptTokenCount,
                                            outputTokens|candidatesTokenCount}}
"""

from __future__ import annotations

from typing import Any


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_usage(data: dict[str, Any]) -> dict[str, int]:
    """レスポンス dict から正規化済み usage を抽出する。無ければ空 dict。"""
    usage = data.get("usage")
    if isinstance(usage, dict):
        inp = _as_int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
        out = _as_int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
        tot = _as_int(usage.get("total_tokens", inp + out))
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": tot}

    meta = data.get("usageMetadata")
    if isinstance(meta, list):
        inp = out = 0
        for entry in meta:
            tokens = entry.get("tokens", {}) if isinstance(entry, dict) else {}
            inp += _as_int(tokens.get("inputTokens", tokens.get("promptTokenCount", 0)))
            out += _as_int(tokens.get("outputTokens", tokens.get("candidatesTokenCount", 0)))
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}

    return {}
