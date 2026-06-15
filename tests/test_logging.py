"""logging: 値フリー（値を渡すAPIが無い）・座標+コードのみ・redactor・sink差替。"""

import json

from gwr.logging import ALLOWED_FIELDS, LogEvent, Logger, redact_columns


def _capturing_logger(**kw):
    lines = []
    return Logger(sink=lines.append, **kw), lines


def test_emit_meta_only_no_value_field():
    logger, lines = _capturing_logger()
    logger.emit(
        LogEvent(
            step_id="agg",
            node_type="file.transform",
            status="ok",
            in_slots=["tables.raw"],
            out_slots=["tables.summary"],
            row_count=3,
            col_count=2,
            dtypes={"売上": "int64"},
            usage={"input_tokens": 10},
        )
    )
    rec = json.loads(lines[0])
    # 構造的に値フィールドが無い＝セル値が載る余地がない
    assert set(rec) <= ALLOWED_FIELDS
    assert "value" not in rec and "data" not in rec
    assert rec["row_count"] == 3
    assert rec["dtypes"] == {"売上": "int64"}


def test_data_error_is_coordinates_and_code_only():
    logger, lines = _capturing_logger()
    logger.data_error("agg", reason="NOT_NUMERIC", row=42, col=2)
    rec = json.loads(lines[0])
    assert rec == {"step_id": "agg", "status": "ERROR", "reason": "NOT_NUMERIC",
                   "row": 42, "col": 2, "level": "ERROR"}


def test_unknown_fields_are_structurally_dropped():
    logger, lines = _capturing_logger()
    # 万一 dict 経由で値を混入させようとしても許可外フィールドは落ちる
    logger._write({"step_id": "x", "secret_value": "個人情報", "status": "ok"}, "INFO")
    rec = json.loads(lines[0])
    assert "secret_value" not in rec
    assert rec["step_id"] == "x"


def test_redactor_omits_column_info():
    logger, lines = _capturing_logger(redactor=redact_columns)
    logger.data_error("agg", reason="NOT_NUMERIC", row=42, col=2)
    rec = json.loads(lines[0])
    assert "col" not in rec
    assert rec["row"] == 42 and rec["reason"] == "NOT_NUMERIC"


def test_level_threshold_filters():
    logger, lines = _capturing_logger(level="ERROR")
    logger.emit(LogEvent(step_id="a", status="ok"), level="INFO")  # 抑制される
    assert lines == []
    logger.emit(LogEvent(step_id="a", status="bad"), level="ERROR")
    assert len(lines) == 1


def test_custom_formatter_and_sink():
    logger, lines = _capturing_logger(formatter=lambda r: f"{r['step_id']}:{r['status']}")
    logger.emit(LogEvent(step_id="a", status="ok"))
    assert lines == ["a:ok"]
