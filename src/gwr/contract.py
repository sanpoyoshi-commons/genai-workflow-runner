"""contract — [[inputs]]／[[outputs]] 宣言の解釈。

- parse_inputs / parse_outputs：TOML 宣言を構造化。
- to_genai_ui_spec：入力契約から源内OSS のリクエスト形式 JSON（8 コンポーネント）を生成。
- bind_inputs：源内OSS のリクエストの inputs を検証して Envelope スロットへ束縛
  （file→files.<key>、scalar→vars.<key>）。
- bind_outputs：Envelope を源内OSS の {outputs(テキスト), artifacts(ファイル)} へ写像する
  （利用者自身のデータなので値を含んでよい）。

入力契約は「UI 生成・検証・Runner 束縛」の 3 用途を 1 宣言から導出する。
conversation_history は会話継続用の hidden 予約キー。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from gwr import expr
from gwr.datatypes import FileRef, make_file_ref
from gwr.envelope import Envelope

GENAI_COMPONENTS = frozenset(
    {"text", "number", "textarea", "file", "select", "checkbox", "radio", "hidden"}
)
CHOICE_COMPONENTS = frozenset({"select", "checkbox", "radio"})
CONVERSATION_HISTORY_KEY = "conversation_history"

# 源内OSS の UI JSON へ通すコンポーネント別の任意パラメータ。
_PASS_THROUGH = {
    "text": ("desc", "min_length", "max_length", "default_value"),
    "textarea": ("desc", "min_length", "max_length", "default_value"),
    "number": ("desc", "min", "max", "default_value"),
    "file": ("desc", "accept", "multiple", "max_size", "max_file_count"),
    "select": ("desc", "items", "default_value"),
    "checkbox": ("desc", "items", "default_value"),
    "radio": ("desc", "items", "default_value"),
    "hidden": ("default_value",),
}


class ContractError(Exception):
    """入力契約違反（reason はコード）。"""

    def __init__(self, reason: str, key: str = "", message: str = "") -> None:
        super().__init__(message or f"{reason}:{key}")
        self.reason = reason
        self.key = key


@dataclass
class InputSpec:
    key: str
    type: str
    label: str = ""
    required: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutputSpec:
    key: str
    from_ref: str
    type: str
    label: str = ""


def parse_inputs(flow: dict[str, Any]) -> list[InputSpec]:
    specs: list[InputSpec] = []
    for raw in flow.get("inputs", []):
        key = raw.get("key")
        if not key:
            raise ContractError("INPUT_MISSING_KEY")
        itype = raw.get("type", "text")
        if key == CONVERSATION_HISTORY_KEY:
            itype = "hidden"  # 予約キーは hidden 固定
        if itype not in GENAI_COMPONENTS:
            raise ContractError("INPUT_UNKNOWN_TYPE", key, f"unknown type: {itype}")
        params = {
            k: v
            for k, v in raw.items()
            if k not in ("key", "type", "label", "required")
        }
        if itype in CHOICE_COMPONENTS and "items" not in params:
            raise ContractError("INPUT_MISSING_ITEMS", key)
        specs.append(
            InputSpec(
                key=key,
                type=itype,
                label=raw.get("label", ""),
                required=bool(raw.get("required", False)),
                params=params,
            )
        )
    return specs


def parse_outputs(flow: dict[str, Any]) -> list[OutputSpec]:
    specs: list[OutputSpec] = []
    for raw in flow.get("outputs", []):
        key = raw.get("key")
        if not key:
            raise ContractError("OUTPUT_MISSING_KEY")
        if "from" not in raw:
            raise ContractError("OUTPUT_MISSING_FROM", key)
        specs.append(
            OutputSpec(
                key=key,
                from_ref=raw["from"],
                type=raw.get("type", "text"),
                label=raw.get("label", ""),
            )
        )
    return specs


def to_genai_ui_spec(inputs: list[InputSpec]) -> dict[str, Any]:
    """入力契約 → 源内OSS のリクエスト形式 JSON。"""
    spec: dict[str, Any] = {}
    for inp in inputs:
        comp: dict[str, Any] = {"type": inp.type}
        if inp.type != "hidden":
            comp["title"] = inp.label or inp.key
            if inp.required:
                comp["required"] = True
        for pkey in _PASS_THROUGH.get(inp.type, ()):  # type 固有の任意パラメータ
            if pkey in inp.params:
                comp[pkey] = inp.params[pkey]
        spec[inp.key] = comp
    return spec


# --- 入力束縛・検証 ------------------------------------------------------
def _extension_mime(filename: str) -> str:
    low = filename.lower()
    if low.endswith((".xlsx", ".xls")):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if low.endswith(".csv"):
        return "text/csv"
    if low.endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


def _file_entry_to_ref(entry: dict[str, Any]) -> FileRef:
    filename = entry.get("filename", "upload")
    # content（同期/UI生成形）と contents（非同期curl例）の双方を受理する。
    b64 = entry.get("content", entry.get("contents", ""))
    return make_file_ref(
        display_name=filename,
        mime=_extension_mime(filename),
        contents_b64=b64,
    )


def _to_file_refs(value: Any) -> list[FileRef]:
    """源内OSS のファイル送出形式を FileRef[] へ（受信は2系統を両対応）。

    - 同期/UI生成形 : [{key, files:[{filename, content}]}]
    - 非同期curl例   : [{key, contents, filename}]
    """
    refs: list[FileRef] = []
    groups = value if isinstance(value, list) else [value]
    for group in groups:
        if not isinstance(group, dict):
            continue
        nested = group.get("files")
        if isinstance(nested, list):  # ネスト形（{key, files:[...]}）
            refs.extend(_file_entry_to_ref(f) for f in nested if isinstance(f, dict))
        elif "filename" in group or "content" in group or "contents" in group:
            refs.append(_file_entry_to_ref(group))  # 平坦形（{key, contents, filename}）
    return refs


# `required` が「空」とみなす文字。半角/全角スペース・タブ・改行のほか、見た目が空白の
# Unicode 空白（NBSP 等）も含める。`str.strip()` は引数なしだと Unicode 空白を全て落とす
# （全角スペース U+3000 も対象）ので、追加の定義は要らない。
def _is_blank(value: Any) -> bool:
    """`required = true` に対して「値が入っていない」とみなすか。

    `[[inputs]]` はスキーマではなく UI 生成の宣言なので、利用者から見た `required` は
    HTML5 の required（空欄不可）と意味を揃える。JSON Schema の required（キーの存在）
    ではない。ブラウザ側の検証は `--set` や API 直叩きでは効かないため、ここで弾く。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _coerce_scalar(inp: InputSpec, value: Any) -> Any:
    if inp.type == "number":
        try:
            num = float(value)
        except (TypeError, ValueError) as e:
            raise ContractError("NOT_NUMERIC", inp.key) from e
        return int(num) if num.is_integer() else num
    if inp.type == "checkbox" and isinstance(value, str):
        return [v for v in value.split(",") if v != ""]  # 複数選択はカンマ区切り
    return value


def _check_constraints(inp: InputSpec, value: Any) -> None:
    p = inp.params
    if inp.type == "number":
        if "min" in p and value < p["min"]:
            raise ContractError("BELOW_MIN", inp.key)
        if "max" in p and value > p["max"]:
            raise ContractError("ABOVE_MAX", inp.key)
    if inp.type in ("text", "textarea") and isinstance(value, str):
        if "min_length" in p and len(value) < p["min_length"]:
            raise ContractError("TOO_SHORT", inp.key)
        if "max_length" in p and len(value) > p["max_length"]:
            raise ContractError("TOO_LONG", inp.key)


def _collect_file_refs(request_inputs: dict[str, Any], key: str) -> list[FileRef]:
    """源内OSS のリクエストから当該 key のファイルを集める。

    源内OSS 本来の送出形は **`inputs.files[]` に全ファイルを集約**し各 entry の `key` で識別する：
        inputs.files = [{ "key": "<fieldKey>", "files": [{filename, content}] }]
    これを優先し、後方互換として `inputs.<key>` 直下（per-key 形）も受理する。
    """
    groups: list[Any] = []
    bucket = request_inputs.get("files")
    if isinstance(bucket, list):
        groups += [g for g in bucket if isinstance(g, dict) and g.get("key") == key]
    if key != "files":  # field 名が "files" の場合は bucket と direct が同一になるため除外
        direct = request_inputs.get(key)
        if isinstance(direct, list):
            groups += direct
        elif isinstance(direct, dict):
            groups.append(direct)
    return _to_file_refs(groups)


def bind_inputs(
    inputs: list[InputSpec], request_inputs: dict[str, Any], envelope: Envelope
) -> Envelope:
    """源内OSS のリクエストの inputs を検証して Envelope へ束縛。fail-closed。"""
    for inp in inputs:
        if inp.type == "file":
            refs = _collect_file_refs(request_inputs, inp.key)
            if not refs:
                if inp.required:
                    raise ContractError("REQUIRED_MISSING", inp.key)
                continue
            multiple = bool(inp.params.get("multiple", False))
            envelope.set(f"files.{inp.key}", refs if multiple else refs[0])
            continue
        # scalar 系（源内OSS は inputs.<key> 直下で送る）
        if inp.key not in request_inputs:
            if inp.required:
                raise ContractError("REQUIRED_MISSING", inp.key)
            if "default_value" in inp.params:
                # default_value はパース済みフロー定義（serve は全リクエストで再利用）への参照。
                # set は参照保存になったため、複数 Envelope が定義を共有しないよう明示コピーする。
                envelope.set(f"vars.{inp.key}", copy.deepcopy(inp.params["default_value"]))
            continue
        raw = request_inputs[inp.key]
        if inp.required and _is_blank(raw):
            # キーはあるが値が空。「無い」とは原因が違うので別コードで返す
            # （利用者向け文言は app._USER_MESSAGES で分岐する）。
            raise ContractError("REQUIRED_EMPTY", inp.key)
        coerced = _coerce_scalar(inp, raw)
        _check_constraints(inp, coerced)
        envelope.set(f"vars.{inp.key}", coerced)
    return envelope


# --- 出力写像 ------------------------------------------------------------
def _render_text(value: Any) -> str:
    """非ファイル出力を表示用テキストにする。

    dict / list（flow.validate の検証レポート等）は Python の repr ではなく JSON にする。
    文字列・数値など従来からある値の見え方は変えない（バイト不変）。
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def bind_outputs(outputs: list[OutputSpec], envelope: Envelope) -> dict[str, Any]:
    """Envelope → 源内OSS の {outputs(Markdown テキスト), artifacts(FileRef[])}。"""
    text_parts: list[str] = []
    artifacts: list[dict[str, Any]] = []
    for out in outputs:
        value = expr.resolve(out.from_ref, envelope)
        if out.type == "file":
            if isinstance(value, dict) and "contents" in value:
                # ファイル名（拡張子つき）を優先し、無ければラベル/キーで補う
                artifacts.append(
                    {
                        "display_name": value.get("display_name") or out.label or out.key,
                        "contents": value["contents"],
                    }
                )
        else:
            rendered = _render_text(value)
            if out.label:
                text_parts.append(f"## {out.label}\n\n{rendered}")
            else:
                text_parts.append(rendered)
    return {"outputs": "\n\n".join(text_parts), "artifacts": artifacts}
