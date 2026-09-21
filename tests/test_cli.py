"""gwr CLI: validate / ui-spec / run。"""

import io
import json
from pathlib import Path

import pandas as pd

from gwr.cli import main

POC = str(Path(__file__).resolve().parents[1] / "examples" / "poc.toml")


def test_validate_ok(capsys):
    rc = main(["validate", POC])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_validate_reports_issues(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        'version = "1"\n[[steps]]\nid = "x"\ntype = "no.such"\nout = "vars.x"\n', "utf-8"
    )
    rc = main(["validate", str(bad)])
    assert rc == 1
    assert "UNKNOWN_NODE_TYPE" in capsys.readouterr().out


def test_ui_spec_outputs_json(capsys):
    rc = main(["ui-spec", POC])
    assert rc == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["data"]["type"] == "file"


def test_run_processes_real_xlsx(tmp_path, capsys):
    # 本物の xlsx を作成して gwr run でローカル実走
    src = tmp_path / "uriage.xlsx"
    pd.DataFrame({"部署": ["営業", "開発", "営業"], "売上": [100, 250, 50]}).to_excel(
        src, index=False, engine="openpyxl"
    )
    outdir = tmp_path / "out"
    rc = main(["run", POC, "--file", f"data={src}", "--llm-fake", "集計しました",
               "-o", str(outdir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "集計しました" in out
    # artifact が拡張子つきファイル名で書き出される
    artifact = outdir / "summary.xlsx"
    assert artifact.exists()
    df = pd.read_excel(io.BytesIO(artifact.read_bytes()), engine="openpyxl")
    records = json.loads(df.to_json(orient="records", force_ascii=False))
    assert records == [{"部署": "営業", "売上": 150}, {"部署": "開発", "売上": 250}]


def test_run_set_kv_parsing_error(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        main(["run", POC, "--set", "badpair"])


# --- smoke-rag / smoke-ci（HttpxTransport を差し替えてオフラインで配線確認） --------
from gwr.adapters.transport import HttpResponse  # noqa: E402


class _ScriptedTransport:
    """URL ごとに応答を返すフェイク。HttpxTransport の差し替え用（kwargs を吸収）。"""

    last = None

    def __init__(self, *_a, **_k):
        self.requests = []
        _ScriptedTransport.last = self

    def request(self, method, url, *, headers, json=None, data=None):
        self.requests.append({"method": method, "url": url, "headers": headers,
                              "json": json, "data": data})
        if "openid-connect/token" in url:
            return HttpResponse(200, '{"access_token": "TOK"}')
        # RAG/CI の応答は質問内容で出し分け
        if "law-rag" in url:
            body = (json or {}).get("inputs", {}).get("question", "")
            if "存在しない" in body:
                return HttpResponse(200, '{"outputs": "", "usageMetadata": []}')
            return HttpResponse(200, '{"outputs": "## 回答\\n労基法20条...", "usageMetadata": []}')
        return HttpResponse(200, '{"outputs": "分析しました", "artifacts": []}')


def test_smoke_rag_keycloak_bearer_and_answer(monkeypatch, capsys):
    monkeypatch.setattr("gwr.adapters.transport.HttpxTransport", _ScriptedTransport)
    rc = main([
        "smoke-rag", "--endpoint", "https://api:3443/api/law-rag/query",
        "--question", "解雇予告は何日前?", "--insecure-tls",
        "--kc-token-url", "http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token",
        "--kc-user", "admin", "--kc-pass", "pw",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "回答あり" in out and "労基法20条" in out
    # password grant → Bearer が api 呼び出しに載っている（本番 x-api-key は不在）
    reqs = _ScriptedTransport.last.requests
    api_req = [r for r in reqs if "law-rag" in r["url"]][0]
    assert api_req["headers"]["authorization"] == "Bearer TOK"
    assert "x-api-key" not in api_req["headers"]


def test_smoke_rag_empty_reported_as_no_match(monkeypatch, capsys):
    monkeypatch.setattr("gwr.adapters.transport.HttpxTransport", _ScriptedTransport)
    rc = main([
        "smoke-rag", "--endpoint", "https://api:3443/api/law-rag/query",
        "--question", "存在しない法令", "--api-key", "k",
    ])
    assert rc == 0
    assert "該当なし" in capsys.readouterr().out


def test_load_no_result_markers_file_ignores_comments(tmp_path):
    from gwr.cli import _load_no_result_markers
    f = tmp_path / "markers.txt"
    f.write_text("# comment\n\n該当なし文1\n  # another\n該当なし文2\n", "utf-8")
    assert _load_no_result_markers(file_path=str(f)) == ["該当なし文1", "該当なし文2"]


def test_load_no_result_markers_inline_newlines():
    from gwr.cli import _load_no_result_markers
    assert _load_no_result_markers(inline="A\n\nB\n") == ["A", "B"]


def test_delegate_extra_headers_provider_defaults_client_id_when_env_empty(monkeypatch):
    """GWR_KC_CLIENT_ID が空文字でも既定 genai-web-dev にフォールバックし、provider は callable。"""
    from gwr.adapters.transport import HttpResponse
    from gwr.cli import _delegate_extra_headers

    class T:
        def __init__(self):
            self.requests = []

        def request(self, method, url, *, headers, json=None, data=None):
            self.requests.append({"data": data})
            return HttpResponse(200, '{"access_token": "TOK", "expires_in": 60}')

    monkeypatch.setenv("GWR_KC_USER", "admin")
    monkeypatch.setenv(
        "GWR_KC_TOKEN_URL",
        "http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token",
    )
    monkeypatch.setenv("GWR_KC_CLIENT_ID", "")  # compose の ${VAR:-} で空文字がセットされるケース
    monkeypatch.setenv("GWR_KC_PASS", "pw")

    t = T()
    provider = _delegate_extra_headers(t)
    assert callable(provider)  # serve は invoke 毎に解決（起動時固定でない）
    assert provider() == {"authorization": "Bearer TOK"}
    assert "client_id=genai-web-dev" in t.requests[0]["data"]  # 空文字→既定にフォールバック


def test_delegate_extra_headers_none_without_kc_user(monkeypatch):
    """GWR_KC_USER 無しなら None（本番＝x-api-key 経路）。"""
    from gwr.cli import _delegate_extra_headers
    monkeypatch.delenv("GWR_KC_USER", raising=False)
    assert _delegate_extra_headers(object()) is None


def test_smoke_rag_marker_reports_no_match(monkeypatch, capsys):
    """smoke-rag に --no-result-marker を渡すと、その文が該当なし判定される。"""
    marker = "クエリから関連する法令を特定できませんでした。"

    class _T(_ScriptedTransport):
        def request(self, method, url, *, headers, json=None, data=None):
            self.requests.append({"method": method, "url": url, "headers": headers,
                                  "json": json, "data": data})
            if "openid-connect/token" in url:
                return HttpResponse(200, '{"access_token": "TOK"}')
            return HttpResponse(200, '{"outputs": "' + marker + '", "usageMetadata": []}')

    monkeypatch.setattr("gwr.adapters.transport.HttpxTransport", _T)
    rc = main([
        "smoke-rag", "--endpoint", "https://api:3443/api/law-rag/query",
        "--question", "架空の法令", "--api-key", "k",
        "--no-result-marker", marker,
    ])
    assert rc == 0
    assert "該当なし" in capsys.readouterr().out


def test_smoke_ci_api_key_path(monkeypatch, capsys):
    monkeypatch.setattr("gwr.adapters.transport.HttpxTransport", _ScriptedTransport)
    rc = main([
        "smoke-ci", "--endpoint", "https://api:3443/api/code-interpreter/responses",
        "--instruction", "平均を出して", "--api-key", "K", "--insecure-tls",
    ])
    assert rc == 0
    assert "分析しました" in capsys.readouterr().out
    ci_req = [r for r in _ScriptedTransport.last.requests if "code-interpreter" in r["url"]][0]
    assert ci_req["headers"]["x-api-key"] == "K"


def test_mcp_subcommand_is_listed_in_help(capsys):
    """`gwr --help` に mcp が並ぶ（extra の有無に関係なくサブコマンドは存在する）。"""
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "mcp" in capsys.readouterr().out


def test_mcp_subcommand_guides_when_extra_is_missing(monkeypatch, capsys):
    """extra 未導入の環境では案内を stderr に出して 1 で終わる（他のコマンドは無影響）。"""
    import builtins
    import sys

    real_import = builtins.__import__

    def refuse_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    for module in [m for m in list(sys.modules) if m == "mcp" or m.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.delitem(sys.modules, "gwr.mcp_server", raising=False)
    monkeypatch.setattr(builtins, "__import__", refuse_mcp)

    rc = main(["mcp"])

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout は JSON-RPC 専用なので汚さない
    assert "--extra mcp" in captured.err
    # 同じプロセスで他のサブコマンドは従来どおり動く
    assert main(["validate", POC]) == 0


def test_spec_outputs_the_bundled_one_pager(capsys):
    """`gwr spec` は同梱の仕様 1 枚をそのまま出す（docs/ の原本と一致）。"""
    from gwr.assets import spec_markdown

    rc = main(["spec"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == spec_markdown()
    assert out == (Path(__file__).resolve().parents[1] / "docs" / "flow-toml-for-ai.md").read_text(
        "utf-8"
    )


def test_startup_endpoint_error_is_one_line_not_a_traceback(monkeypatch, capsys):
    """委譲先の接続先が不正なら、起動時に 1 行のメッセージで終わる（終了コード 2）。

    ここは**運用者が見る面**なので、直せるように url / host を出す
    （利用者が見る `error.reason` 側には出さない → tests/test_delegate_errors.py）。
    """
    monkeypatch.setenv("GWR_LLM_ENDPOINT", "http://10.0.0.5/v1/chat")
    rc = main(["run", POC, "--set", "threshold=1"])
    err = capsys.readouterr().err
    assert rc == 2
    assert err.splitlines()[0].startswith("委譲先に接続できません: ENDPOINT_NOT_HTTPS")
    assert "10.0.0.5" in err  # 運用者が直すために必要
    assert "Traceback" not in err
