"""judge のフロー例（judge-smoke.toml・judge-structured.toml）統合テスト。

両方に共通して固定するのは 2 つ。

1. 警告なしで `validate` を通る（`other` がある・`criteria` のキー集合が揃っている）。
2. **分岐ごとに別の出力キーへ書く**＝倒れた先が出力から読める。同じキーへ書くと、LLM が
   固定文（モック）のとき出力から分岐を読み取れず、実機のログから導出するしかなくなる。

構造化版だけの不変条件（表・表の配列を畳まずに送る）は最後の 1 件。値の畳み込みそのものは
tests/test_judge.py が問い単位で見ている。ここで見るのは「同梱している例がその作法どおりに
書かれている」ことなので、例を崩すと落ちる。
"""

from pathlib import Path
from typing import Any

import pytest

from gwr.adapters.fakes import FakeLLMAdapter
from gwr.adapters.judge import JudgeRequest, JudgeResponse
from gwr.app import WorkflowApp
from gwr.validate import report_to_dict, validate_text_safe

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FLOWS = ("judge-smoke.toml", "judge-structured.toml")

BILLING = "## 請求窓口の下書き（billing へ倒れたときだけ入る）"
HUMAN = "## 人の確認への案内（human へ倒れたときだけ入る）"
DRAFT = "（モック下書き）"

INQUIRY = "先月の請求額がいつもより高いので内訳を教えてください。"


class RoutingJudgeAdapter:
    """`route` の答えだけ差し替える委譲先（分岐の行き先を決めるため）。"""

    def __init__(self, choice: str, confidence: float) -> None:
        self.choice = choice
        self.confidence = confidence
        self.calls: list[JudgeRequest] = []

    def judge(self, request: JudgeRequest) -> JudgeResponse:
        self.calls.append(request)
        labels = list(request.questions["route"]["criteria"])
        rest = 1.0 - self.confidence
        answers: dict[str, Any] = {
            "route": {
                "type": "choice",
                "choice": self.choice,
                "probabilities": {
                    label: (self.confidence if label == self.choice else rest / (len(labels) - 1))
                    for label in labels
                },
                "confidence": self.confidence,
            },
            "urgent": {"type": "noul", "noul": False},
            "severity": {
                "type": "score",
                "score": 1.5,
                "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5, "3": 0.0},
                "confidence": 0.5,
                "legend": {"0": "", "1": "", "2": "", "3": ""},
            },
        }
        return JudgeResponse(model=request.model, answers=answers, usage={})


def _run(name: str, choice: str = "billing", confidence: float = 1.0):
    judge = RoutingJudgeAdapter(choice, confidence)
    app = WorkflowApp.from_toml(
        (EXAMPLES / name).read_text("utf-8"),
        adapters={"judge": judge, "llm": FakeLLMAdapter(output=DRAFT)},
    )
    result = app.invoke({"inquiry": INQUIRY})
    assert result.get("error") is None, result.get("error")
    return result["outputs"], judge


def _assert_only(outputs: str, filled: str, empty: str) -> None:
    """倒れた先だけに下書きが入り、もう一方は見出しだけが残る。"""
    assert f"{filled}\n\n{DRAFT}" in outputs
    assert f"{empty}\n\n\n" in outputs


@pytest.mark.parametrize("name", FLOWS)
def test_examples_validate_without_warnings(name):
    doc = report_to_dict(validate_text_safe((EXAMPLES / name).read_text("utf-8")))
    assert doc["ok"] is True, doc["issues"]
    assert doc["warnings"] == []


@pytest.mark.parametrize("name", FLOWS)
def test_each_branch_writes_its_own_output_key(name):
    outputs, _ = _run(name, choice="billing")
    _assert_only(outputs, BILLING, HUMAN)

    outputs, _ = _run(name, choice="technical")
    _assert_only(outputs, HUMAN, BILLING)


@pytest.mark.parametrize("name", FLOWS)
def test_low_confidence_falls_to_human_even_for_billing(name):
    """答えが billing でも確信が閾値（0.55）を下回れば人の確認へ倒れる。"""
    outputs, _ = _run(name, choice="billing", confidence=0.5)
    _assert_only(outputs, HUMAN, BILLING)


def test_tables_and_arrays_reach_the_delegate_unchanged():
    """構造化版：例に書いた表・表の配列が、文字列へ畳まれずそのまま委譲先へ渡る。"""
    _, judge = _run("judge-structured.toml")

    questions = judge.calls[0].questions
    assert questions["route"]["instructions"] == {
        "task": "この問い合わせを扱うべき窓口を決める",
        "notes": ["本文に書かれていないことは推測しない", "どの窓口にも当てはまらなければ other"],
    }
    assert questions["route"]["criteria"]["billing"] == {
        "what": "請求・返金・支払い方法",
        "not_for": "新規契約やプラン変更の見積の依頼",
        "examples": ["請求額がいつもと違う", "返金してほしい"],
    }
    # 選択肢はキー集合が揃っている（揃っていないと validate が警告する）
    keys = {frozenset(entry) for entry in questions["route"]["criteria"].values()}
    assert keys == {frozenset({"what", "not_for", "examples"})}
    # score の criteria は「表の配列」のまま（順序も保つ）
    assert [entry["level"] for entry in questions["severity"]["criteria"]] == [
        "影響なし",
        "軽微",
        "中程度",
        "重大",
    ]
    assert all(not isinstance(entry, str) for entry in questions["severity"]["criteria"])
