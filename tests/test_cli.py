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
