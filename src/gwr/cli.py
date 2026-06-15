"""gwr CLI — validate / ui-spec / run（ローカル動作確認）。

外部 I/O は持たない。adapters は注入式のため、CLI 単体では file.* ノード中心に検証/実行する。
`run` は LLM ステップを含むフローでも Fake LLM で実走できる（Excel/CSV 加工の実機確認用）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gwr.adapters.fakes import FakeLLMAdapter
from gwr.app import WorkflowApp
from gwr.validate import validate_file

# run で LLM config_type が解決できるよう、どんな型でも model_id を返す最小既定。
_DEFAULT_CONFIG = '[default]\nmodel_id = "local"\n'


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_file(args.flow)
    if report.ok:
        print("OK: フロー検証に問題なし")
        return 0
    for issue in report.issues:
        print(f"[{issue.category}] {issue.step_id}: {issue.reason} {issue.detail}".rstrip())
    return 1


def _cmd_ui_spec(args: argparse.Namespace) -> int:
    app = WorkflowApp.from_file(args.flow)
    print(json.dumps(app.ui_spec(), ensure_ascii=False, indent=2))
    return 0


def _parse_kv(pairs: list[str], what: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--{what} は key=value 形式で指定してください: {item!r}")
        key, value = item.split("=", 1)
        out[key] = value
    return out


def _build_request_inputs(sets: dict[str, str], files: dict[str, str]) -> dict[str, Any]:
    """--set/--file から源内リクエストの inputs を組み立てる（ファイルは同期/UI 生成形）。"""
    inputs: dict[str, Any] = dict(sets)
    for key, path in files.items():
        raw = Path(path).read_bytes()
        content = base64.b64encode(raw).decode("ascii")
        inputs[key] = [
            {"key": key, "files": [{"filename": Path(path).name, "content": content}]}
        ]
    return inputs


def _write_artifacts(artifacts: list[Any], outdir: Path) -> list[Path]:
    written: list[Path] = []
    if artifacts:
        outdir.mkdir(parents=True, exist_ok=True)
    for art in artifacts:
        # display_name は basename のみ採用（パストラバーサル防止）
        name = Path(str(art.get("display_name", "artifact"))).name or "artifact"
        dest = outdir / name
        dest.write_bytes(base64.b64decode(art.get("contents", "")))
        written.append(dest)
    return written


def _llm_setup(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    """(adapters, config_defaults) を返す。

    GWR_LLM_ENDPOINT が設定されていれば実 LLM（GennaiLLMAdapter＋HttpxTransport）、無ければ Fake。
    実 LLM のモデル/system は GWR_LLM_MODEL / GWR_LLM_SYSTEM_PROMPT（--config 指定時はそれを優先）。
    """
    endpoint = os.environ.get("GWR_LLM_ENDPOINT")
    if args.config:
        config = Path(args.config).read_text("utf-8")
    elif endpoint:
        model = os.environ.get("GWR_LLM_MODEL", "local")
        system = os.environ.get("GWR_LLM_SYSTEM_PROMPT", "")
        config = (
            f"[default]\nmodel_id = {json.dumps(model)}\n"
            f"system_prompt = {json.dumps(system)}\n"
        )
    else:
        config = _DEFAULT_CONFIG

    if endpoint:
        from gwr.adapters.gennai import GennaiLLMAdapter
        from gwr.adapters.transport import HttpxTransport

        transport = HttpxTransport(verify=os.environ.get("GWR_LLM_VERIFY_TLS", "true") != "false")
        adapters: dict[str, Any] = {
            "llm": GennaiLLMAdapter(
                transport,
                endpoint,
                api_key=os.environ.get("GWR_LLM_API_KEY", ""),
                user_id=os.environ.get("GWR_USER_ID", ""),
                mode=os.environ.get("GWR_LLM_MODE", "chat"),
                allow_insecure=os.environ.get("GWR_LLM_ALLOW_INSECURE", "") == "true",
            )
        }
    else:
        adapters = {"llm": FakeLLMAdapter(output=args.llm_fake)}
    _wire_delegate_adapters(adapters)
    return adapters, config


def _delegate_extra_headers(
    transport: Any,
) -> dict[str, str] | Callable[[], dict[str, str]] | None:
    """委譲先の認証ヘッダ。GWR_KC_USER があればオンプレ版 Keycloak Bearer のプロバイダ、無ければ None。

    常駐 serve 向けに **invoke 毎に失効再取得する** KeycloakTokenProvider（callable）を返す。
    起動時 1 回固定だと access_token が数分で失効し、時間経過後の委譲呼びが 401 になるため。
    本番（クラウド版）は委譲先 ExApp の x-api-key（GWR_RAG_API_KEY 等）で叩くため None。
    """
    if os.environ.get("GWR_KC_USER"):
        from gwr.onpre_auth import KeycloakTokenProvider

        # GWR_KC_CLIENT_ID が空文字（compose の ${VAR:-} 上書き）でも既定にフォールバックする。
        # os.environ.get(k, default) はキーが空文字で存在すると default を返さないため or で受ける。
        return KeycloakTokenProvider(
            transport,
            os.environ["GWR_KC_TOKEN_URL"],
            client_id=os.environ.get("GWR_KC_CLIENT_ID") or "genai-web-dev",
            username=os.environ["GWR_KC_USER"],
            password=os.environ.get("GWR_KC_PASS", ""),
        )
    return None


def _load_no_result_markers(
    inline: str | None = None, file_path: str | None = None
) -> list[str]:
    """RAG「該当なし」マーカー文を読み込む（運用注入・gwr 本体に焼かない）。

    源内 RAG は no-match でも非空の「該当なし」文を返すため、運用者がその文を注入して
    `docs=[]`（該当なし分岐）へ落とす。優先順は引数 > 環境変数。
    - file（`GWR_RAG_NO_RESULT_MARKERS_FILE`）：1 行 1 マーカー。`#` 始まり・空行は無視
      （＝既定でコメントアウトしておけば無効。運用者が外して有効化）。長い日本語向き。
    - inline（`GWR_RAG_NO_RESULT_MARKERS`）：改行区切りの簡易指定。
    """
    markers: list[str] = []
    path = file_path or os.environ.get("GWR_RAG_NO_RESULT_MARKERS_FILE")
    if path:
        for line in Path(path).read_text("utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                markers.append(s)
    text = inline if inline is not None else os.environ.get("GWR_RAG_NO_RESULT_MARKERS")
    if text:
        markers.extend(s.strip() for s in text.splitlines() if s.strip())
    return markers


def _wire_delegate_adapters(adapters: dict[str, Any]) -> None:
    """RAG / CI 委譲先を env から配線する（serve/run の WebUI E2E 用）。

    GWR_RAG_ENDPOINT / GWR_CI_ENDPOINT が設定された時のみ注入。オンプレ版は GWR_KC_* で Bearer、
    本番は GWR_<RAG|CI>_API_KEY で x-api-key。自己署名は GWR_VERIFY_TLS=false / 内部宛は
    GWR_ALLOW_INSECURE=true。未設定なら従来どおり LLM のみ（後方互換）。
    """
    rag_ep = os.environ.get("GWR_RAG_ENDPOINT")
    ci_ep = os.environ.get("GWR_CI_ENDPOINT")
    if not rag_ep and not ci_ep:
        return
    from gwr.adapters.envelope_client import EnvelopeClient
    from gwr.adapters.transport import HttpxTransport

    # law-rag/CI は CPU で数分かかり得る。GWR_TIMEOUT（秒）で可変・既定 600。
    timeout = float(os.environ.get("GWR_TIMEOUT", "600"))
    transport = HttpxTransport(
        timeout=timeout, verify=os.environ.get("GWR_VERIFY_TLS", "true") != "false"
    )
    extra = _delegate_extra_headers(transport)
    allow_insecure = os.environ.get("GWR_ALLOW_INSECURE", "") == "true"
    client = EnvelopeClient(transport, allow_insecure=allow_insecure)
    user_id = os.environ.get("GWR_USER_ID", "")
    if rag_ep:
        from gwr.adapters.gennai import GennaiRetrievalAdapter

        adapters["retrieval"] = GennaiRetrievalAdapter(
            client, rag_ep, api_key=os.environ.get("GWR_RAG_API_KEY", ""),
            user_id=user_id, extra_headers=extra,
            no_result_markers=_load_no_result_markers(),
        )
    if ci_ep:
        from gwr.adapters.gennai import GennaiCodeInterpreterAdapter

        adapters["code_interpreter"] = GennaiCodeInterpreterAdapter(
            client, ci_ep, api_key=os.environ.get("GWR_CI_API_KEY", ""),
            user_id=user_id, extra_headers=extra,
        )


def _cmd_run(args: argparse.Namespace) -> int:
    sets = _parse_kv(args.set, "set")
    files = _parse_kv(args.file, "file")
    adapters, defaults = _llm_setup(args)

    app = WorkflowApp.from_toml(
        Path(args.flow).read_text("utf-8"),
        adapters=adapters,
        config_defaults=defaults,
    )
    result = app.invoke(_build_request_inputs(sets, files))

    print("=== outputs ===")
    print(result.get("outputs", ""))
    paths = _write_artifacts(result.get("artifacts", []), Path(args.out))
    if paths:
        print("\n=== artifacts ===")
        for p in paths:
            print(f"  {p}")
    err = result.get("error")
    if err:
        print(f"\n=== error ===\n  {err}")
        return 1
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """/invoke を HTTP 公開する（genai-web に ExApp 登録して WebUI から確認する用）。

    認証キーは環境変数 GWR_API_KEY（設定時のみ x-api-key を検証）。
    LLM は GWR_LLM_ENDPOINT があれば実 LLM、無ければ Fake。
    """
    import uvicorn  # 遅延 import（gwr[serve] extra）

    from gwr.serve import create_app

    adapters, defaults = _llm_setup(args)
    app = WorkflowApp.from_toml(
        Path(args.flow).read_text("utf-8"),
        adapters=adapters,
        config_defaults=defaults,
    )
    api = create_app(app, api_key=os.environ.get("GWR_API_KEY"))
    uvicorn.run(api, host=args.host, port=args.port)
    return 0


def _cmd_smoke_llm(args: argparse.Namespace) -> int:
    """委譲先 LLM を 1 回だけ実叩きして応答を表示する（実機スモーク）。"""
    from gwr.adapters.gennai import GennaiLLMAdapter
    from gwr.adapters.llm import LLMRequest
    from gwr.adapters.transport import HttpxTransport

    transport = HttpxTransport(timeout=args.timeout, verify=not args.insecure_tls)
    adapter = GennaiLLMAdapter(
        transport,
        args.endpoint,
        api_key=args.api_key or "",
        mode=args.mode,
        allow_insecure=args.allow_insecure,
    )
    resp = adapter.predict(
        LLMRequest(
            model_id=args.model,
            system_prompt=args.system or "",
            inputs={"prompt": args.prompt},
        )
    )
    print("=== output ===")
    print(resp.output)
    print("=== usage ===")
    print(resp.usage)
    return 0


def _smoke_auth(args: argparse.Namespace, transport: Any) -> tuple[str, dict[str, str] | None]:
    """smoke の認証を解決する。

    Keycloak オプション（--kc-user 等）が揃えばオンプレ版用に password grant → Bearer を
    extra_headers で返す（本番＝x-api-key は空）。無ければ --api-key を x-api-key として返す。
    """
    if args.kc_user:
        from gwr.onpre_auth import bearer, fetch_keycloak_token

        token = fetch_keycloak_token(
            transport,
            args.kc_token_url,
            client_id=args.kc_client_id,
            username=args.kc_user,
            password=args.kc_pass,
        )
        return "", bearer(token)
    return (args.api_key or ""), None


def _cmd_smoke_rag(args: argparse.Namespace) -> int:
    """委譲先 RAG を 1 回実叩きして docs（該当あり/なし）を表示する（実機スモーク）。"""
    from gwr.adapters.envelope_client import EnvelopeClient
    from gwr.adapters.gennai import GennaiRetrievalAdapter
    from gwr.adapters.retrieval import RetrievalRequest
    from gwr.adapters.transport import HttpxTransport

    transport = HttpxTransport(timeout=args.timeout, verify=not args.insecure_tls)
    api_key, extra = _smoke_auth(args, transport)
    client = EnvelopeClient(transport, allow_insecure=args.allow_insecure)
    markers = _load_no_result_markers(file_path=args.no_result_markers_file)
    markers.extend(args.no_result_marker or [])
    adapter = GennaiRetrievalAdapter(
        client, args.endpoint, api_key=api_key, user_id=args.user_id or "",
        extra_headers=extra, no_result_markers=markers,
    )
    resp = adapter.search(
        RetrievalRequest(source=args.source or "", query=args.question, top_k=args.top_k)
    )
    if not resp.docs:
        print("=== 該当なし（docs=[]） ===")
        print("回答が空＝法令を特定できず。フローは branch len($.docs.X)==0 で分岐できる。")
    else:
        print(f"=== 回答あり（docs={len(resp.docs)}） ===")
        for doc in resp.docs:
            print(str(doc.get("content", ""))[:1500])
    print("\n=== usage ===")
    print(resp.usage)
    return 0


def _cmd_smoke_ci(args: argparse.Namespace) -> int:
    """委譲先 Code Interpreter を 1 回実叩きして output/artifacts を表示する（実機スモーク）。"""
    from gwr.adapters.code_interpreter import CodeInterpreterRequest
    from gwr.adapters.envelope_client import EnvelopeClient
    from gwr.adapters.gennai import GennaiCodeInterpreterAdapter
    from gwr.adapters.transport import HttpxTransport
    from gwr.datatypes import make_file_ref

    transport = HttpxTransport(timeout=args.timeout, verify=not args.insecure_tls)
    api_key, extra = _smoke_auth(args, transport)
    client = EnvelopeClient(transport, allow_insecure=args.allow_insecure)
    adapter = GennaiCodeInterpreterAdapter(
        client, args.endpoint, api_key=api_key, user_id=args.user_id or "", extra_headers=extra
    )
    files = []
    for path in args.file or []:
        name = Path(path).name
        b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        files.append(make_file_ref(name, "application/octet-stream", b64))
    resp = adapter.run(CodeInterpreterRequest(instruction=args.instruction, files=files))
    print("=== output ===")
    print(resp.output)
    if resp.artifacts:
        print("\n=== artifacts ===")
        paths = _write_artifacts(resp.artifacts, Path(args.out))
        for p in paths:
            print(f"  {p}")
    print("\n=== usage ===")
    print(resp.usage)
    return 0


def _add_smoke_auth_args(parser: argparse.ArgumentParser) -> None:
    """smoke-rag/-ci 共通の認証・接続オプション。"""
    parser.add_argument("--api-key", default=None, help="本番＝委譲先 ExApp の x-api-key")
    parser.add_argument("--user-id", default=None, help="x-user-id（監査用・任意）")
    parser.add_argument("--kc-token-url", default=None,
                        help="オンプレ版: Keycloak token 端点 例 http://keycloak:8080/realms/"
                             "genai-realm/protocol/openid-connect/token")
    parser.add_argument("--kc-client-id", default="genai-web-dev",
                        help="オンプレ版: password grant の client_id（既定 genai-web-dev）")
    parser.add_argument("--kc-user", default=None, help="オンプレ版: Keycloak ユーザー名")
    parser.add_argument("--kc-pass", default=None, help="オンプレ版: Keycloak パスワード")
    parser.add_argument("--allow-insecure", action="store_true",
                        help="http/内部宛を許可（オンプレ版 等の信頼済み委譲先）")
    parser.add_argument("--insecure-tls", action="store_true", help="自己署名 TLS 検証を無効化")
    # オンプレ版の law-rag は gemma 等を逐次に複数回呼ぶため CPU では数分かかる。既定を長めに。
    parser.add_argument("--timeout", type=float, default=600.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gwr", description="汎用AIワークフローランナー")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="フロー TOML を検証する")
    p_validate.add_argument("flow", type=Path)
    p_validate.set_defaults(func=_cmd_validate)

    p_ui = sub.add_parser("ui-spec", help="源内リクエスト形式 JSON を出力する")
    p_ui.add_argument("flow", type=Path)
    p_ui.set_defaults(func=_cmd_ui_spec)

    p_run = sub.add_parser("run", help="フローをローカル実走（Excel/CSV 加工の実機確認）")
    p_run.add_argument("flow", type=Path)
    p_run.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                       help="スカラ入力（数値/文字列）。複数指定可")
    p_run.add_argument("--file", action="append", default=[], metavar="KEY=PATH",
                       help="ファイル入力（csv/xlsx 等）。複数指定可")
    p_run.add_argument("-o", "--out", default="gwr-out", metavar="DIR",
                       help="artifacts の出力先ディレクトリ（既定 gwr-out）")
    p_run.add_argument("--llm-fake", default="(LLMモック出力)", metavar="TEXT",
                       help="LLM ステップに返す固定テキスト（決定論）")
    p_run.add_argument("--config", default=None, metavar="TOML",
                       help="LLM 設定 TOML（model_id/system_prompt 等）。省略時は最小既定")
    p_run.set_defaults(func=_cmd_run)

    p_serve = sub.add_parser("serve", help="/invoke を HTTP 公開（genai-web から WebUI 確認）")
    p_serve.add_argument("flow", type=Path)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--llm-fake", default="(LLMモック出力)", metavar="TEXT")
    p_serve.add_argument("--config", default=None, metavar="TOML")
    p_serve.set_defaults(func=_cmd_serve)

    p_smoke = sub.add_parser("smoke-llm", help="委譲先 LLM を 1 回実叩き（実機スモーク）")
    p_smoke.add_argument("--endpoint", required=True,
                         help="例 http://ollama:11434/v1/chat/completions")
    p_smoke.add_argument("--model", required=True, help="例 gemma2:2b")
    p_smoke.add_argument("--prompt", required=True, help="ユーザープロンプト")
    p_smoke.add_argument("--system", default=None, help="system プロンプト")
    p_smoke.add_argument("--mode", default="chat", choices=["chat", "responses"])
    p_smoke.add_argument("--api-key", default=None)
    p_smoke.add_argument("--allow-insecure", action="store_true",
                         help="http/内部宛を許可（オンプレ版 等の信頼済み委譲先）")
    p_smoke.add_argument("--insecure-tls", action="store_true", help="自己署名 TLS 検証を無効化")
    p_smoke.add_argument("--timeout", type=float, default=120.0)
    p_smoke.set_defaults(func=_cmd_smoke_llm)

    p_rag = sub.add_parser("smoke-rag", help="委譲先 RAG を 1 回実叩き（該当あり/なしを表示）")
    p_rag.add_argument("--endpoint", required=True,
                       help="例 https://api:3443/api/law-rag/query")
    p_rag.add_argument("--question", required=True, help="質問")
    p_rag.add_argument("--source", default=None, help="検索対象（任意）")
    p_rag.add_argument("--top-k", type=int, default=5)
    p_rag.add_argument("--no-result-markers-file", default=None,
                       help="「該当なし」文を 1 行 1 つ書いたファイル（# はコメント）")
    p_rag.add_argument("--no-result-marker", action="append", default=[], metavar="TEXT",
                       help="「該当なし」と見なす outputs 文（複数指定可）")
    _add_smoke_auth_args(p_rag)
    p_rag.set_defaults(func=_cmd_smoke_rag)

    p_ci = sub.add_parser("smoke-ci", help="委譲先 Code Interpreter を 1 回実叩き")
    p_ci.add_argument("--endpoint", required=True,
                      help="例 https://api:3443/api/code-interpreter/responses")
    p_ci.add_argument("--instruction", required=True, help="指示文（input_text）")
    p_ci.add_argument("--file", action="append", default=[], metavar="PATH",
                      help="入力ファイル（csv/xlsx）。複数指定可")
    p_ci.add_argument("-o", "--out", default="gwr-out", metavar="DIR",
                      help="artifacts 出力先（既定 gwr-out）")
    _add_smoke_auth_args(p_ci)
    p_ci.set_defaults(func=_cmd_smoke_ci)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
