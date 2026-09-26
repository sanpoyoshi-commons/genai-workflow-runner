"""judge ノード ＋ 判定 API アダプタ契約。

state（判定対象のテキストや表）と**型付きの問い**を委譲先へ渡し、答えと確信度を受け取る。
gwr 自身は推論しない（委譲の設計前提）。実体は genai-ai-api-onpre の judge ルートで、
1 トークン目の確率分布から答えを決める。

**問いは step の設定に静的に書き、式解決しない。** 式解決を受けるのは `in.state` だけで、
`questions` / `model` は `run` に渡る step 辞書（config）からそのまま読む。実行時の入力で
選択肢をすり替えられないのは、この境界（Runner は `step["in"]` の値しか解決しない）による。

**criteria と instructions の値は変形せずに送る。** 文字列・表・配列のいずれでも、TOML に
書いたままの形で委譲先へ渡す。gwr が畳むと、委譲先が受け取る問いと書き手が書いた問いが
食い違う。

**応答は契約に書いてあるキーだけを通す。** 委譲先の応答をそのまま envelope へ載せると、
契約に無いキーに入った自由文がフローへ流れ込み、後続の `llm` のプロンプトや `file.write`
の本文へ運ばれ得る。判定の答えは分岐の材料であって、委譲先からの自由な伝言路ではない。

本モジュールが持つのはリクエスト整形・応答検査・usage の回収だけで、プロンプトも
確率の計算も持たない。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol

from gwr.adapters.delegate_errors import delegate_errors
from gwr.nodes.base import IssueSink, Node, NodeContext, NodeError, NodeSignature

# --- 委譲先と揃える上限 --------------------------------------------------
# いずれも委譲先（judge ルート）が入口で掛けている値と同じにする。ここで先に落とすのは
# 「実行してから 400 を貰う」より「書いた時点で validate が落とす」方が直しやすいため。
# ズレると validate が通ったフローが実行時に落ちるので、**片方だけ変えない**。
QUESTION_TYPES = ("noul", "choice", "score")
MAX_QUESTIONS = 16
MIN_CHOICES = 2
# 20 は記号の都合ではなく、委譲先が使う上位候補の数の上限による。
MAX_CHOICES = 20
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
MAX_LABEL_LENGTH = 64
QUESTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 「どれにも当たらない」選択肢の名前。本文の意味は読まず、**ラベル名だけ**を見る
# （委譲先の日本語を gwr 本体に焼かないのと同じ方針）。
FALLBACK_LABEL = "other"

# 確率の合計の許容幅。委譲先は候補集合の中で再正規化してから 6 桁に丸めるので、
# ずれるのは丸めの分だけ（候補 20 件でも 1e-5 程度）。
PROBABILITY_SUM_TOLERANCE = 1e-3

# 応答から回収する usage のキー。委譲先は**返せない値をわざと欠落させる**ので、
# 無いものを 0 で埋めない（埋めると同じ嘘を gwr 側で作り直すことになる）。
USAGE_KEYS = ("input_tokens", "output_tokens")


@dataclass
class JudgeRequest:
    state: Any
    questions: dict[str, Any]
    model: str = ""  # 空なら送らない（委譲先の既定モデルに委ねる）


@dataclass
class JudgeResponse:
    model: str  # 実際に応答したモデル名
    answers: dict[str, Any]
    usage: dict[str, int] = field(default_factory=dict)


class JudgeAdapter(Protocol):
    def judge(self, request: JudgeRequest) -> JudgeResponse: ...


# --- 問いの読み取り ------------------------------------------------------
def _criteria_entries(criteria: Any) -> list[Any]:
    """criteria（配列／表）の値を宣言順に並べる。"""
    if isinstance(criteria, dict):
        return list(criteria.values())
    if isinstance(criteria, list):
        return list(criteria)
    return []


def answer_labels(question: dict[str, Any]) -> list[str]:
    """応答で返るラベル（＝`choice` の値・`probabilities` のキー）を求める。

    - choice：criteria が表ならそのキー、配列なら委譲先が振る `A`, `B`, … の記号。
    - score ：段階の添字 `"0"`, `"1"`, …（criteria は配列のみ）。
    - noul  ：`probabilities` を返さないので空。
    """
    qtype = question.get("type")
    criteria = question.get("criteria")
    if qtype == "choice":
        if isinstance(criteria, dict):
            return [str(k) for k in criteria]
        return [chr(ord("A") + i) for i in range(len(_criteria_entries(criteria)))]
    if qtype == "score":
        return [str(i) for i in range(len(_criteria_entries(criteria)))]
    return []


def _shape(entry: Any) -> Any:
    """criteria の 1 要素の「形」。表ならキー集合、それ以外は型名。

    本家推奨の「全選択肢で同じキー集合に揃える」を、値を読まずに比べるための鍵。
    """
    if isinstance(entry, dict):
        return frozenset(str(k) for k in entry)
    return type(entry).__name__


def _is_blank(value: Any) -> bool:
    """委譲先が空文字として弾く値か（前後の空白を落として空になる文字列）。"""
    return isinstance(value, str) and not value.strip()


# --- 静的検査 ------------------------------------------------------------
class _FirstError:
    """`static_check` の結果を実行時に使い回すための受け口（IssueSink を満たす）。

    `run` は validate と**同じ検査**を通してから委譲する。検査を 2 回書くと必ずずれるので、
    実装は 1 つにして記録先だけ差し替える。警告は実行を止めないので捨てる。
    """

    def __init__(self) -> None:
        self.reason: str | None = None

    def add(
        self,
        category: str,
        step_id: str,
        reason: str,
        detail: str = "",
        *,
        severity: str = "error",
    ) -> None:
        if severity != "warning" and self.reason is None:
            self.reason = reason


def _check_one_question(
    name: str, question: Any, step_id: str, report: IssueSink
) -> None:
    """問い 1 件を検査する。理由コードはこの中に文字列リテラルで書く。"""
    if not isinstance(question, dict):
        report.add("static", step_id, "JUDGE_QUESTION_TYPE_INVALID", f"question={name}")
        return

    qtype = question.get("type")
    if qtype not in QUESTION_TYPES:
        report.add("static", step_id, "JUDGE_QUESTION_TYPE_INVALID", f"question={name}")
        return

    instructions = question.get("instructions")
    if instructions is None or _is_blank(instructions):
        report.add("static", step_id, "JUDGE_INSTRUCTIONS_MISSING", f"question={name}")

    criteria = question.get("criteria")
    if qtype == "noul":
        if criteria is not None:
            report.add(
                "static", step_id, "JUDGE_CRITERIA_INVALID",
                f"question={name} reason=noul_has_criteria",
            )
        return

    if criteria is None:
        report.add("static", step_id, "JUDGE_CRITERIA_INVALID", f"question={name} reason=missing")
        return
    if qtype == "score" and not isinstance(criteria, list):
        # score の段階に呼び出し側のラベルは付かない（委譲先が配列しか受けない）。
        report.add(
            "static", step_id, "JUDGE_CRITERIA_INVALID",
            f"question={name} reason=score_not_a_list",
        )
        return
    if not isinstance(criteria, (list, dict)):
        report.add(
            "static", step_id, "JUDGE_CRITERIA_INVALID",
            f"question={name} reason=not_a_list_or_table",
        )
        return

    entries = _criteria_entries(criteria)
    if isinstance(criteria, dict):
        for label in criteria:
            if not str(label).strip() or len(str(label)) > MAX_LABEL_LENGTH:
                report.add(
                    "static", step_id, "JUDGE_CRITERIA_INVALID",
                    f"question={name} reason=bad_label",
                )
                break
    if any(_is_blank(entry) for entry in entries):
        report.add(
            "static", step_id, "JUDGE_CRITERIA_INVALID", f"question={name} reason=blank_entry"
        )

    low, high = (
        (MIN_CHOICES, MAX_CHOICES) if qtype == "choice" else (MIN_SCORE_LEVELS, MAX_SCORE_LEVELS)
    )
    if not low <= len(entries) <= high:
        report.add(
            "static", step_id, "JUDGE_CRITERIA_COUNT",
            f"question={name} count={len(entries)} allowed={low}..{high}",
        )

    # --- 以下は警告（直さなくても実行できる） ---
    shapes = {_shape(entry) for entry in entries}
    if len(shapes) > 1:
        report.add(
            "static", step_id, "JUDGE_CRITERIA_KEYS_UNEVEN", f"question={name}",
            severity="warning",
        )
    if qtype == "choice" and isinstance(criteria, dict) and FALLBACK_LABEL not in criteria:
        report.add(
            "static", step_id, "JUDGE_CHOICE_NO_FALLBACK", f"question={name}",
            severity="warning",
        )


def check_questions(step: dict[str, Any], step_id: str, report: IssueSink) -> None:
    """step の `questions` と `model` を検査して report に記録する。"""
    questions = step.get("questions")
    if not isinstance(questions, dict) or not questions:
        report.add("static", step_id, "JUDGE_QUESTIONS_MISSING", "param=questions")
        return
    if len(questions) > MAX_QUESTIONS:
        report.add(
            "static", step_id, "JUDGE_QUESTIONS_TOO_MANY",
            f"count={len(questions)} max={MAX_QUESTIONS}",
        )

    for name, question in questions.items():
        if not QUESTION_NAME_PATTERN.match(str(name)):
            report.add("static", step_id, "JUDGE_QUESTION_NAME_INVALID", f"question={name}")
            continue
        _check_one_question(str(name), question, step_id, report)

    # 閾値は「あるモデルで自分のデータを流して決めた値」なので、モデルを固定しないと前提が崩れる。
    if not str(step.get("model", "")).strip():
        report.add("static", step_id, "JUDGE_MODEL_MISSING", "param=model", severity="warning")


# --- 応答の検査 ----------------------------------------------------------
def _fail(kind: str) -> NoReturn:
    """応答が契約から外れたときの停止。**値は載せない**（どこが外れたかの別だけ）。

    `return NodeError(...)` ではなく**ここで raise する**。理由コードの突合テストは
    `raise <例外>("<コード>", ...)` を AST で拾うので、返り値にすると全数から漏れる
    （tests/test_user_messages.py）。
    """
    raise NodeError("JUDGE_ANSWER_INVALID", f"kind={kind}")


def _check_distribution(answer: dict[str, Any], labels: list[str]) -> None:
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        _fail("probabilities_missing")
    if {str(k) for k in probabilities} != set(labels):
        _fail("probability_labels")
    try:
        total = sum(float(v) for v in probabilities.values())
    except (TypeError, ValueError) as e:
        raise NodeError("JUDGE_ANSWER_INVALID", "kind=probability_not_numeric") from e
    if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
        _fail("probability_sum")
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        _fail("confidence_missing")
    if not 0.0 <= float(confidence) <= 1.0:
        _fail("confidence_out_of_range")


def _clean_answer(question: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """検査済みの答えを、**契約に書いてあるキーだけ**で組み直す。

    委譲先の応答をそのまま envelope へ載せると、契約に無いキー（例 `note`）に入った
    自由文がフローへ流れ込み、後続の `llm` のプロンプトや `file.write` の本文へ運ばれ得る。
    判定の答えは**分岐の材料**であって委譲先からの自由な伝言路ではないので、通すものを
    こちらで決める。`legend` も鍵は選択肢の記号に限る（値はフロー TOML に書いた説明が
    返ってくる想定）。
    """
    qtype = question["type"]
    if qtype == "noul":
        return {"type": "noul", "noul": bool(answer["noul"])}

    labels = answer_labels(question)
    probabilities = {label: float(answer["probabilities"][label]) for label in labels}
    confidence = float(answer["confidence"])
    if qtype == "choice":
        return {
            "type": "choice",
            "choice": str(answer["choice"]),
            "probabilities": probabilities,
            "confidence": confidence,
        }

    raw_legend = answer.get("legend")
    legend: dict[str, Any] = {}
    if raw_legend is not None:
        if not isinstance(raw_legend, dict) or {str(k) for k in raw_legend} != set(labels):
            _fail("legend_labels")
        legend = {str(k): v for k, v in raw_legend.items()}
    return {
        "type": "score",
        "score": float(answer["score"]),
        "probabilities": probabilities,
        "confidence": confidence,
        "legend": legend,
    }


def check_answers(questions: dict[str, Any], response: JudgeResponse) -> dict[str, Any]:
    """応答が送った問いと噛み合っているかを見て、**組み直した答え**を返す。

    委譲先が壊れた／別の版になった／モデルが想定外の形を返した場合に、**確からしく見える
    嘘の判定結果をフローへ流さない**ための関門。`branch` の条件はこの値で決まる。
    外れたら `JUDGE_ANSWER_INVALID`。
    """
    answers = response.answers
    if not isinstance(answers, dict):
        _fail("answers_not_a_table")
    if set(answers) != set(questions):
        _fail("question_names")

    cleaned: dict[str, Any] = {}
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict):
            _fail("answer_not_a_table")
        qtype = question.get("type")
        if answer.get("type") != qtype:
            _fail("answer_type")

        if qtype == "noul":
            if not isinstance(answer.get("noul"), bool):
                _fail("noul_not_boolean")
            cleaned[name] = _clean_answer(question, answer)
            continue

        labels = answer_labels(question)
        _check_distribution(answer, labels)

        if qtype == "choice":
            if str(answer.get("choice")) not in labels:
                _fail("unknown_choice")
            cleaned[name] = _clean_answer(question, answer)
            continue

        score = answer.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            _fail("score_not_numeric")
        if not 0.0 <= float(score) <= len(labels) - 1:
            _fail("score_out_of_range")
        cleaned[name] = _clean_answer(question, answer)
    return cleaned


# --- ノード --------------------------------------------------------------
class JudgeNode(Node):
    TYPE = "judge"
    OUT_NAMES = ("model", "answers", "usage")

    @classmethod
    def signature(cls, step: dict[str, Any]) -> NodeSignature:
        # 判定結果は JSON ネイティブな dict（型レベルは scalar）。`in.state` は型を宣言しない
        # ＝文字列・表・配列のどれでも受ける（llm / retrieval と同じ流儀）。
        return NodeSignature(outputs={"out": "scalar"})

    def static_check(self, step: dict[str, Any], step_id: str, report: IssueSink) -> None:
        check_questions(step, step_id, report)

    def run(
        self, inputs: dict[str, Any], config: dict[str, Any], ctx: NodeContext
    ) -> dict[str, Any]:
        adapter: JudgeAdapter | None = ctx.adapters.get("judge")
        if adapter is None:
            raise NodeError("ADAPTER_MISSING", "adapter=judge")

        # validate と同じ検査を通す（検査の実装を 2 つ持たない）。`validate` を通していない
        # フローでも、委譲先へ壊れた問いを送らずここで止まる。
        collected = _FirstError()
        check_questions(config, str(config.get("id", "")), collected)
        if collected.reason is not None:
            raise NodeError("JUDGE_QUESTIONS_INVALID", f"reason={collected.reason}")

        state = inputs.get("state")
        if not isinstance(state, (str, dict, list)):
            raise NodeError("STATE_MISSING", "input=state expected=str|table|list")

        questions: dict[str, Any] = dict(config["questions"])
        model = str(config.get("model", "")).strip()
        request = JudgeRequest(state=state, questions=questions, model=model)

        started = time.monotonic()
        with delegate_errors():
            response = adapter.judge(request)
        duration_ms = (time.monotonic() - started) * 1000.0

        answers = check_answers(questions, response)
        if model and response.model != model:
            # 想定と違うモデルが答えた＝閾値の前提が崩れている。モデル名は載せない。
            raise NodeError("JUDGE_MODEL_MISMATCH", "mismatch=model")

        usage = {k: int(v) for k, v in response.usage.items() if k in USAGE_KEYS}
        if usage:
            ctx.add_usage(usage)
        ctx.log(
            {
                "step_id": str(config.get("id", "")),
                "node_type": self.TYPE,
                "status": "OK",
                "question_count": len(questions),
                "duration_ms": duration_ms,
                "usage": usage,
            }
        )

        bundle = {"model": response.model, "answers": answers, "usage": usage}
        # 単一 `out` でも、`out = { answers = "...", model = "..." }` のマップ形でも書ける。
        return {"out": bundle, "model": response.model, "answers": answers, "usage": usage}
