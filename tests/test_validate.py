"""validate: 静的・データフロー・型伝播の検証（fail-closed）。"""

from gwr.validate import validate_text

GOOD = """
version = "1"

[[inputs]]
key = "data"
type = "file"
label = "対象"
required = true

[[steps]]
id = "load"
type = "file.load"
in = { file = "$.files.data" }
out = "tables.raw"

[[steps]]
id = "agg"
type = "file.transform"
engine = "pandas"
in = { table = "$.tables.raw" }
out = "tables.summary"
ops = [{ op = "groupby", by = ["部署"], agg = { "売上" = "sum" } }]

[[outputs]]
key = "report"
from = "$.tables.summary"
type = "text"
label = "集計"
"""


def test_valid_flow_passes():
    report = validate_text(GOOD)
    assert report.ok, report.issues


def test_read_before_write_detected():
    bad = """
version = "1"
[[steps]]
id = "agg"
type = "file.transform"
in = { table = "$.tables.ghost" }
out = "tables.summary"
"""
    report = validate_text(bad)
    assert any(i.reason == "READ_BEFORE_WRITE" for i in report.by_category("dataflow"))


def test_type_mismatch_detected():
    # file.transform は table=TableRef を要求するが、llm の out は scalar
    bad = """
version = "1"
[[inputs]]
key = "q"
type = "text"
[[steps]]
id = "ask"
type = "llm"
config_type = "x"
in = { question = "$.vars.q" }
out = "vars.answer"
[[steps]]
id = "agg"
type = "file.transform"
in = { table = "$.vars.answer" }
out = "tables.summary"
"""
    report = validate_text(bad)
    assert any(i.reason == "TYPE_MISMATCH" for i in report.by_category("type"))


def test_unknown_node_and_duplicate_id():
    bad = """
version = "1"
[[steps]]
id = "a"
type = "no.such.node"
out = "vars.x"
[[steps]]
id = "a"
type = "file.write"
in = { table = "$.tables.t" }
out = "files.r"
"""
    report = validate_text(bad)
    reasons = {i.reason for i in report.by_category("static")}
    assert "UNKNOWN_NODE_TYPE" in reasons
    assert "DUPLICATE_ID" in reasons


def test_output_reference_unwritten_detected():
    bad = """
version = "1"
[[inputs]]
key = "data"
type = "file"
[[steps]]
id = "load"
type = "file.load"
in = { file = "$.files.data" }
out = "tables.raw"
[[outputs]]
key = "ghost"
from = "$.tables.nowhere"
type = "text"
"""
    report = validate_text(bad)
    assert any(i.reason == "OUTPUT_READ_BEFORE_WRITE" for i in report.by_category("dataflow"))
