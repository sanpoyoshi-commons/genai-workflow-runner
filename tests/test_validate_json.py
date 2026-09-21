"""`gwr validate --json`（機械可読出力）。

スキーマは後続（MCP）がそのまま返す前提なので、形をここで固定する。
併せて「`--json` 無しの出力は 1 文字も変わらない」ことを回帰で担保する。
"""

import json
from pathlib import Path

import pytest

from gwr.cli import main
from gwr.validate import SCHEMA_VERSION

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
POC = str(EXAMPLES / "poc.toml")


def _run_json(capsys, path: str) -> tuple[int, dict]:
    rc = main(["validate", path, "--json"])
    return rc, json.loads(capsys.readouterr().out)


# --- 正常系 --------------------------------------------------------------
def test_json_ok_schema(capsys):
    rc, doc = _run_json(capsys, POC)
    assert rc == 0
    assert doc == {"schema_version": SCHEMA_VERSION, "ok": True, "issues": []}


def test_json_indent_matches_ui_spec_style(capsys):
    """ui-spec と同じ書式（ensure_ascii=False, indent=2）で出す。"""
    main(["validate", POC, "--json"])
    out = capsys.readouterr().out
    assert out.startswith("{\n  ")
    assert out.endswith("}\n")


# --- 異常系（意図的に壊した 3 種） ---------------------------------------
def _broken(tmp_path: Path, name: str, transform) -> str:
    src = (EXAMPLES / "poc.toml").read_text("utf-8")
    dest = tmp_path / name
    dest.write_text(transform(src), "utf-8")
    return str(dest)


def test_json_unresolved_jump(tmp_path, capsys):
    path = _broken(tmp_path, "jump.toml", lambda s: s.replace('else = "write"', 'else = "wirte"'))
    rc, doc = _run_json(capsys, path)
    assert rc == 1 and doc["ok"] is False
    assert doc["issues"] == [
        {
            "category": "static",
            "step_id": "gate",
            "reason": "BRANCH_ELSE_UNRESOLVED",
            "detail": "wirte",
        }
    ]


def test_json_missing_out(tmp_path, capsys):
    path = _broken(
        tmp_path,
        "out.toml",
        lambda s: s.replace('out = "tables.summary"\n', "", 1),
    )
    rc, doc = _run_json(capsys, path)
    assert rc == 1
    reasons = {i["reason"] for i in doc["issues"]}
    assert reasons == {"READ_BEFORE_WRITE"}
    assert {i["step_id"] for i in doc["issues"]} == {"summarize", "gate", "write"}
    assert {i["detail"] for i in doc["issues"]} == {"tables.summary"}


def test_json_type_mismatch(tmp_path, capsys):
    path = _broken(
        tmp_path,
        "type.toml",
        lambda s: s.replace(
            'name = "summary.xlsx"\nin = { table = "$.tables.summary" }',
            'name = "summary.xlsx"\nin = { table = "$.files.data" }',
        ),
    )
    rc, doc = _run_json(capsys, path)
    assert rc == 1
    assert doc["issues"] == [
        {
            "category": "type",
            "step_id": "write",
            "reason": "TYPE_MISMATCH",
            "detail": "files.data:FileRef != table requires TableRef",
        }
    ]


# --- 読み取り/パース段階の失敗も JSON で返す ------------------------------
def test_json_toml_parse_error(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('version = "1"\nthis is not toml\n', "utf-8")
    rc, doc = _run_json(capsys, str(bad))
    assert rc == 1 and doc["ok"] is False
    issue = doc["issues"][0]
    assert issue["category"] == "static"
    assert issue["step_id"] is None
    assert issue["reason"] == "TOML_PARSE_ERROR"
    assert issue["detail"]


def test_json_file_unreadable(tmp_path, capsys):
    rc, doc = _run_json(capsys, str(tmp_path / "nope.toml"))
    assert rc == 1
    assert doc["issues"][0]["reason"] == "FILE_UNREADABLE"
    assert doc["issues"][0]["step_id"] is None


def test_json_missing_id_step_id_is_null(tmp_path, capsys):
    """step を特定できないときは step_id が null（空文字を出さない）。"""
    flow = tmp_path / "noid.toml"
    flow.write_text('version = "1"\n[[steps]]\ntype = "llm"\n', "utf-8")
    rc, doc = _run_json(capsys, str(flow))
    assert rc == 1
    assert [(i["reason"], i["step_id"]) for i in doc["issues"]] == [("MISSING_ID", None)]


# --- `--json` 無しの出力は 1 文字も変わらない -----------------------------
@pytest.mark.parametrize(
    ("flow_text", "expected"),
    [
        (None, "OK: フロー検証に問題なし\n"),
        (
            'version = "1"\n[[steps]]\nid = "x"\ntype = "no.such"\nout = "vars.x"\n',
            "[static] x: UNKNOWN_NODE_TYPE no.such\n",
        ),
    ],
    ids=["ok", "issue"],
)
def test_text_output_is_byte_identical(tmp_path, capsys, flow_text, expected):
    path = POC
    if flow_text is not None:
        f = tmp_path / "flow.toml"
        f.write_text(flow_text, "utf-8")
        path = str(f)
    rc = main(["validate", path])
    captured = capsys.readouterr()
    assert captured.out == expected
    assert captured.err == ""
    assert rc == (0 if flow_text is None else 1)


def test_text_output_still_raises_on_bad_toml(tmp_path):
    """`--json` 無しの経路は従来どおり例外を伝播する（挙動を変えない）。"""
    import tomllib

    bad = tmp_path / "bad.toml"
    bad.write_text("this is not toml\n", "utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        main(["validate", str(bad)])
