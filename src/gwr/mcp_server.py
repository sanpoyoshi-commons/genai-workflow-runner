"""mcp_server — フロー TOML の作成を支援する MCP サーバ（stdio）。

`gwr mcp` で起動する。公開するのはツール 4 本（`gwr_spec` / `gwr_validate` /
`gwr_dry_run` / `gwr_ui_spec`）と、同梱フロー例の Resources。

設計の不変条件（崩すとセキュリティ境界が動く。tests/test_mcp_server.py が固定する）：

1. **トランスポートは stdio のみ。** HTTP/SSE は出さない＝ネットワーク面を増やさない。
2. **ツールはファイルパスを受けない。** フロー TOML は本文の文字列で受け、この層は
   ファイルシステムに触らない。Resources も外からパスを受け取らず、同梱名の完全一致のみ。
3. **`gwr_dry_run` は委譲アダプタを Fake に固定し、`GWR_*_ENDPOINT` を読まない。**
   実エンドポイントが設定済みの環境でも MCP 経由では委譲が起きない。
4. **`gwr_validate` の応答は `gwr.validate.report_to_dict()` の戻りそのまま。**
   新しい形を作らない（`schema_version` の契約を MCP 側で分岐させない）。
5. **artifacts は本体を返さない。** 名前・バイト数・行列数のメタのみ。
6. **ログ系統に利用者データを流さない。** Runner への `emit` は渡さない（＝ログを出さない）。
   ついでに stdout は JSON-RPC 専用のため、ここから print してはならない。
"""

from __future__ import annotations

import base64
import binascii
import io
import tomllib
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any

import anyio
import anyio.to_thread
import pandas as pd
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from gwr import assets
from gwr.adapters.fakes import FakeCodeInterpreterAdapter, FakeLLMAdapter, FakeRetrievalAdapter
from gwr.app import WorkflowApp
from gwr.contract import ContractError, parse_inputs, to_genai_ui_spec
from gwr.validate import report_to_dict, validate_text_safe

SERVER_NAME = "gwr"

# 受け取るフロー TOML の上限。大きな本文で CEL/JMESPath 評価を膨らませられないようにする。
MAX_FLOW_TOML_BYTES = 1024 * 1024  # 1 MiB
# dry_run 全体の上限。式単位の 2.0 秒制限とは別に、foreach の反復で伸びる分を止める。
DRY_RUN_TIMEOUT_SECONDS = 60.0
# artifacts の行列数を数える上限（自前生成物だが、念のため大きすぎるものは数えない）。
ARTIFACT_SHAPE_MAX_BYTES = 32 * 1024 * 1024

# dry_run の LLM config を解決するための最小既定（どの config_type でも model_id を返す）。
_DEFAULT_CONFIG = '[default]\nmodel_id = "local"\n'

# フロー TOML を受ける 3 ツール共通の引数型。
# 説明文を型に付けるのは、ツールの description だけだとクライアント側の AI が
# ファイルパスを渡そうとするため（受け入れテストで 2 回中 2 回観測）。入力スキーマに
# 載せておくと引数を組み立てる時点で目に入る。
FlowToml = Annotated[
    str,
    Field(
        description=(
            "フロー TOML の**本文そのもの**（ファイルの中身の文字列）。"
            "**ファイルパスを渡すとエラーになる。**ファイルから読む場合は先に中身を読み出して"
            "その文字列を渡すこと。上限 1 MiB。"
        )
    ),
]

DryRunInputs = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "源内OSS のリクエストと同じ形の inputs。スカラ入力は {\"<key>\": <値>} を直に置く。"
            "フローの [[inputs]] で required = true のキーを落とすと REQUIRED_MISSING で止まる。"
        )
    ),
]

_TOO_LARGE = (
    f"flow_toml が大きすぎます（上限 {MAX_FLOW_TOML_BYTES} バイト）。"
    "フローを分割するか不要なコメントを削ってください。"
)
_NOT_TOML = (
    "flow_toml を TOML として解釈できませんでした。"
    "gwr_validate を呼ぶと TOML_PARSE_ERROR として位置つきで返ります。"
)
_FILE_INPUT_UNSUPPORTED = (
    "このフローは実ファイル入力（type = \"file\" の [[inputs]]）を必要とするため、"
    "MCP 経由では dry_run できません（ツールはファイルパスを受け取らないため）。"
    "gwr_validate による検証までは本文だけで完結します。実ファイルでの実走は "
    "`gwr run <flow.toml> --file <key>=<path>` を手元で使ってください。"
)
_TIMED_OUT = (
    f"dry_run が {DRY_RUN_TIMEOUT_SECONDS:.0f} 秒を超えたため打ち切りました。"
    "foreach の反復数を減らすか、inputs を小さくして再実行してください。"
)


# --- 入力の門番 ----------------------------------------------------------
def _checked_text(flow_toml: str) -> str:
    """サイズだけを見る門番。ここを通った本文はファイルとして開かれない。"""
    if len(flow_toml.encode("utf-8")) > MAX_FLOW_TOML_BYTES:
        raise ToolError(_TOO_LARGE)
    return flow_toml


def _parse_flow(flow_toml: str) -> dict[str, Any]:
    try:
        return tomllib.loads(_checked_text(flow_toml))
    except tomllib.TOMLDecodeError as e:
        raise ToolError(f"{_NOT_TOML}（{e}）") from e


# --- artifacts のメタ化（本体は返さない） --------------------------------
def _table_format(display_name: str) -> str | None:
    """名前から表形式を判定する。表として読めない名前なら None。

    `bind_outputs` は artifacts に display_name と contents だけを載せる（mime は
    載らない）ため、判定材料は名前しかない。file.load の `_detect_format` と違って
    既定で csv に寄せず、分からなければ数えない。
    """
    lower = display_name.lower()
    if lower.endswith((".xlsx", ".xls")):
        return "xlsx"
    if lower.endswith(".csv"):
        return "csv"
    return None


def _shape(raw: bytes, display_name: str) -> tuple[int | None, int | None]:
    """artifact の (行数, 列数) を返す。数えられない形式なら (None, None)。"""
    if len(raw) > ARTIFACT_SHAPE_MAX_BYTES:
        return None, None
    fmt = _table_format(display_name)
    if fmt is None:
        return None, None
    try:
        if fmt == "xlsx":
            df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
        else:
            df = pd.read_csv(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 — 行列数は付加情報。数えられなくても dry_run は成功扱い
        return None, None
    return int(len(df)), int(len(df.columns))


def _artifact_meta(artifact: dict[str, Any]) -> dict[str, Any]:
    """FileRef → メタ（名前・バイト数・行列数）。`contents` は載せない。"""
    display_name = str(artifact.get("display_name") or "")
    try:
        raw = base64.b64decode(artifact.get("contents") or "", validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    rows, cols = _shape(raw, display_name)
    return {
        "display_name": display_name,
        "size_bytes": len(raw),
        "row_count": rows,
        "col_count": cols,
    }


def _fake_adapters() -> dict[str, Any]:
    """委譲アダプタを Fake に固定する。env は一切読まない（実接続が起きない）。"""
    return {
        "llm": FakeLLMAdapter(),
        "retrieval": FakeRetrievalAdapter(),
        "code_interpreter": FakeCodeInterpreterAdapter(),
    }


def _run_flow(flow_toml: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """同期の実走本体（スレッドで動かす）。emit は渡さない＝ログを出さない。"""
    app = WorkflowApp.from_toml(
        flow_toml,
        adapters=_fake_adapters(),
        config_defaults=_DEFAULT_CONFIG,
    )
    result = app.invoke(inputs)
    meta = [_artifact_meta(a) for a in result.get("artifacts", [])]
    response: dict[str, Any] = {"outputs": result.get("outputs", ""), "artifacts_meta": meta}
    if result.get("error"):
        response["error"] = result["error"]
    return response


# --- サーバ組み立て ------------------------------------------------------
def _package_version() -> str:
    """クライアントに見せるサーバ版（gwr の版）。未インストールなら空。"""
    try:
        return version("gwr")
    except PackageNotFoundError:  # pragma: no cover — ソース直実行のときだけ
        return ""


def build_server() -> MCPServer:
    """ツール 4 本と Resources を登録した MCPServer を返す（起動はしない）。"""
    server = MCPServer(
        name=SERVER_NAME,
        version=_package_version(),
        instructions=(
            "gwr のフロー TOML を書く／直すための道具立て。手順は "
            "gwr_spec で仕様を読む → 例を Resources から引く → TOML を書く → "
            "gwr_validate が ok:true になるまで直す → 必要なら gwr_dry_run で挙動を見る。"
        ),
        # stdout は JSON-RPC 専用。SDK のログは stderr に出るが、既定より静かにしておく。
        log_level="WARNING",
    )

    @server.tool(
        description=(
            "フロー TOML の仕様 1 枚（Markdown）を返す。ツールは無引数。"
            "4 要素（version / [[inputs]] / [[steps]] / [[outputs]]）、スロット 3 名前空間"
            "（files. / vars. / tables.）、ノード 7 種＋制御 2 種、file.transform の ops 8 種、"
            "参照は JMESPath・when は CEL という式言語の使い分け、"
            "理由コードと直し方の表が入っている。"
            "フローを書く前にこれを読むこと。"
        )
    )
    def gwr_spec() -> str:
        return assets.spec_markdown()

    @server.tool(
        description=(
            "フロー TOML を検証する。**flow_toml にはファイルパスではなく TOML の本文を渡すこと**"
            "（パスを渡すとエラーになる）。"
            "戻りは {schema_version, ok, issues[]}。"
            "**ok が true になるまで issues を潰して直すこと。**"
            "issues[].reason は gwr_spec が返す仕様 1 枚の理由コード表に対応し、"
            "各コードに『典型的な直し方』が書いてある。issues[].step_id は該当 step の id"
            "（特定できない場合は null、[[outputs]] 由来は \"outputs\"）、"
            "issues[].category は static / dataflow / type。"
            "TOML として壊れている場合も例外ではなく TOML_PARSE_ERROR として返る。"
        )
    )
    def gwr_validate(flow_toml: FlowToml) -> dict[str, Any]:
        return report_to_dict(validate_text_safe(_checked_text(flow_toml)))

    @server.tool(
        description=(
            "フロー TOML を手元で実走して挙動を見る（検証ではなく実行）。先に gwr_validate を"
            "通してから使うこと。inputs は源内OSS のリクエストと同じ形で、スカラ入力は"
            "{\"<key>\": <値>} を直に置く。"
            "LLM / 検索 / Code Interpreter は Fake に固定されており、実エンドポイントへは"
            "一切接続しない（環境変数が設定されていても委譲は起きない）。"
            "戻りは {outputs, artifacts_meta}（失敗時のみ error が付く）。"
            "artifacts_meta は名前・バイト数・行列数（表として読めた場合）のメタだけで、"
            "ファイル本体は返らない。"
            "type = \"file\" の [[inputs]] を持つフローは実ファイルが必要なため対象外"
            "（その場合は gwr_validate までで確認する）。"
        )
    )
    async def gwr_dry_run(
        flow_toml: FlowToml, inputs: DryRunInputs = None
    ) -> dict[str, Any]:
        flow = _parse_flow(flow_toml)
        try:
            specs = parse_inputs(flow)
        except ContractError as e:
            raise ToolError(f"[[inputs]] の宣言が不正です: {e.reason}") from e
        if any(spec.type == "file" for spec in specs):
            raise ToolError(_FILE_INPUT_UNSUPPORTED)

        result: dict[str, Any] | None = None
        with anyio.move_on_after(DRY_RUN_TIMEOUT_SECONDS):
            result = await anyio.to_thread.run_sync(
                _run_flow, flow_toml, inputs or {}, abandon_on_cancel=True
            )
        if result is None:
            raise ToolError(_TIMED_OUT)
        return result

    @server.tool(
        description=(
            "フロー TOML の [[inputs]] から源内OSS のリクエスト形式の JSON（UI 定義）を生成する。"
            "フローがどんな入力欄を要求することになるかを確認する用。"
            "入力はフロー TOML の本文の文字列。"
        )
    )
    def gwr_ui_spec(flow_toml: FlowToml) -> dict[str, Any]:
        flow = _parse_flow(flow_toml)
        try:
            return to_genai_ui_spec(parse_inputs(flow))
        except ContractError as e:
            raise ToolError(f"[[inputs]] の宣言が不正です: {e.reason}") from e

    _register_examples(server)
    return server


def _register_examples(server: MCPServer) -> None:
    """同梱フロー例を Resources として登録する。

    1 例につき 1 つの静的 URI を登録するだけで、URI からパスを組み立てない。
    したがって `gwr://examples/../x` のような要求は「登録されていない URI」として
    落ちる＝同梱ディレクトリの外へ出ることが構造的に起きない。
    """
    for name in assets.example_names():
        server.resource(
            f"gwr://examples/{name}",
            name=name,
            description=f"フロー例 {name}: {assets.example_description(name)}",
            mime_type="text/plain",
        )(_example_reader(name))


def _example_reader(name: str):
    """同梱名を閉じ込めた読み出し関数を作る（静的 Resource は引数を取れない）。"""

    def read_example() -> str:
        return assets.example_text(name)

    return read_example


def serve_stdio() -> None:
    """stdio で MCP サーバを動かす（ブロッキング）。"""
    build_server().run(transport="stdio")
