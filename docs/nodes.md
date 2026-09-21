# ノードリファレンス

各ステップの `type` に指定できるノードの一覧です。`in`（入力）・`out`（出力）・ノード固有
パラメータと、産出する値の型を示します。制御ステップ `branch` / `foreach` は
[フロー TOML → 制御フロー](flow-toml.md#制御フローbranch--foreach) を参照。

| `type` | 役割 | 委譲 | 入力 | 出力型 |
|---|---|---|---|---|
| [`file.load`](#fileload) | csv/xlsx を表にする | ローカル | `file` | `TableRef` |
| [`file.transform`](#filetransform) | 表を集計・整形 | ローカル | `table` | `TableRef` |
| [`file.write`](#filewrite) | 表を csv/xlsx に、文字列を text に出力 | ローカル | `table` / `text` | `FileRef` |
| [`flow.validate`](#flowvalidate) | フロー TOML の本文を検証 | ローカル | `flow_toml` | scalar（レポート） |
| [`llm`](#llm) | LLM 推論 | 源内OSS の API | 任意 | scalar |
| [`retrieval`](#retrieval) | RAG 検索 | 源内OSS の API | `query` | `Doc[]` |
| [`code_interpreter`](#code_interpreter) | コード実行・可視化 | 源内OSS の API | `files` | `FileRef` + scalar |

`file.*` と `flow.validate` は外部に出ない**ローカル処理**、`llm` / `retrieval` /
`code_interpreter` はガバメントAI 源内 OSS の API への**委譲**で、gwr 自身はサンドボックスも LLM も持ちません。委譲ノードを動かすには
アダプタの注入（＝`GWR_*_ENDPOINT` 等）が必要です（[委譲先への接続](delegates.md)）。

---

## file.load

csv/xlsx を読み込んで表（`TableRef`）にします。

```toml
[[steps]]
id = "load"
type = "file.load"
in = { file = "$.files.data" }   # inline FileRef（base64 contents を持つこと）
out = "tables.raw"
format = "xlsx"                  # 任意。省略時は拡張子/MIME から推定（csv/xlsx）
max_rows = 100000                # 任意。行数上限（既定 100,000）
max_uncompressed_bytes = 209715200  # 任意。xlsx 解凍後サイズ上限（既定 200 MiB）
```

| キー | 種別 | 説明 |
|---|---|---|
| `in.file` | 入力 | `files.<key>` の `FileRef`。`contents`（base64）必須。 |
| `out` | 出力 | `TableRef` を書くスロット。 |
| `format` | 任意 | `"csv"` / `"xlsx"`。省略時は名前・MIME から推定（不明は csv）。 |
| `max_rows` | 任意 | 超過で `ROW_LIMIT_EXCEEDED`。 |
| `max_uncompressed_bytes` | 任意 | xlsx の zip-bomb 保護。中央ディレクトリの宣言サイズで事前判定。 |

エラー：base64 不正 `FILE_DECODE_ERROR` / 解析失敗 `FILE_PARSE_ERROR` /
xlsx 過大 `XLSX_TOO_LARGE` / 入力が FileRef でない `FILE_MISSING`。

---

## file.transform

`ops` を上から順に適用して表を加工します（`TableRef` → `TableRef`）。表計算エンジンで処理し
**決定論**を死守します（`groupby` / `sort` はキー順に安定整列）。

```toml
[[steps]]
id = "agg"
type = "file.transform"
engine = "pandas"                 # 現状 pandas のみ（明示は任意）
in = { table = "$.tables.raw" }
out = "tables.summary"
ops = [
  { op = "groupby", by = ["部署"], agg = { "売上" = "sum" } },
  { op = "sort", by = ["売上"], ascending = false },
]
```

> TOML のベアキーは ASCII のみ。非 ASCII の列名はキーとして引用符で囲みます
> （例 `agg = { "売上" = "sum" }`）。

### 対応する op

| `op` | パラメータ | 動作 |
|---|---|---|
| `select` | `columns = [...]` | 指定列だけ残す。 |
| `rename` | `columns = { 旧 = 新 }` | 列名を改名。 |
| `filter` | `column`, ＋ `eq`/`ne`/`gt`/`ge`/`lt`/`le` のいずれか | 条件で行を絞る。 |
| `dropna` | `columns = [...]`（任意） | 欠損行を除去（列指定可）。 |
| `sort` | `by = [...]`, `ascending`（既定 true） | 安定ソート（index リセット）。 |
| `head` | `n` | 先頭 n 行。 |
| `groupby` | `by = [...]`, `agg = { 列 = 集計 }` | キー順に集約（`sum`/`mean` 等）。 |
| `sanitize` | （なし） | 数式インジェクション無害化（先頭 `= + - @` を `'` で退避）。 |

エラー：入力が `TableRef` でない `TABLE_MISSING` / 未知 op `UNKNOWN_OP` /
存在しない列参照 `MISSING_COLUMN` / `filter` の比較子欠落 `BAD_OP`。

---

## file.write

値を base64 の `FileRef`（artifacts）にします。`format` で**入力の種類が変わります**。

- `"csv"` / `"xlsx"`：`in.table`（`TableRef`）を書き出す。**出力前に必ず数式インジェクション
  無害化**を適用します（先頭 `= + - @` を `'` で退避）。
- `"text"`：`in.text`（文字列）をそのまま UTF-8 で書く。表計算ソフトで開くものではないため
  **無害化は行いません**（TOML や Markdown をそのまま返す用途）。そのため `name` に
  表計算ソフトが開く拡張子（`.csv` / `.tsv` / `.xls` / `.xlsx` / `.xlsm` / `.slk` / `.dif`）を
  付けることはできません。付けると `validate` が `TEXT_FORMAT_SPREADSHEET_NAME` で落ちます
  （[理由コード一覧](cli.md#理由コード一覧)）。表を出したいときは `"csv"` / `"xlsx"` を使ってください。

```toml
[[steps]]
id = "write"
type = "file.write"
format = "xlsx"                  # "csv" / "xlsx" / "text"（既定 csv）
name = "summary.xlsx"           # 出力ファイル名（既定 output.<fmt>、text は output.txt）
in = { table = "$.tables.summary" }
out = "files.report"
```

```toml
# 文字列をファイルにする場合
[[steps]]
id = "write"
type = "file.write"
format = "text"
name = "flow.toml"
in = { text = "$.vars.toml" }
out = "files.flow"
```

| キー | 種別 | 説明 |
|---|---|---|
| `in.table` | 入力 | 書き出す `TableRef`（`format` が csv / xlsx のとき）。 |
| `in.text` | 入力 | 書き出す文字列（`format = "text"` のとき）。 |
| `out` | 出力 | `FileRef`（`contents` = base64）を書くスロット。 |
| `format` | 任意 | `"csv"` / `"xlsx"` / `"text"`。既定 csv。 |
| `name` | 任意 | ファイル名（`[[outputs]]` の artifact 名に使われる）。 |

MIME は csv → `text/csv`、xlsx → スプレッドシート、text → `text/plain`。

エラー：入力が `TableRef` でない `TABLE_MISSING` / 入力が文字列でない `TEXT_MISSING` /
非対応形式 `UNSUPPORTED_FORMAT`。`validate` は `format = "text"` と表計算の拡張子の
組み合わせを `TEXT_FORMAT_SPREADSHEET_NAME` で落とします。

---

## flow.validate

フロー TOML の**本文**を検証し、`gwr validate --json` と同じレポートを返します。
生成したフローをフロー自身の中で検証するためのノードです（外部に出ないローカル処理）。

```toml
[[steps]]
id = "check"
type = "flow.validate"
in = { flow_toml = "$.vars.toml" }
out = "vars.report"

# 通ったかどうかで分岐できる
[[steps]]
id = "gate"
type = "branch"
when = "$.vars.report.ok"
then = "write"
else = "__end__"
```

| キー | 種別 | 説明 |
|---|---|---|
| `in.flow_toml` | 入力 | 検証するフロー TOML の**本文**（文字列）。ファイルパスは受け付けません。 |
| `out` | 出力 | `{schema_version, ok, issues[]}` を書くスロット（型レベルは scalar）。 |
| `max_bytes` | 任意 | 受け取る本文の上限バイト数。既定 1 MiB。 |

レポートの形と `issues[].reason` の一覧は
[CLI リファレンスの `--json`](cli.md#--json機械可読出力) と
[理由コード一覧](cli.md#理由コード一覧) が正です（同じものを返します）。
TOML として壊れている場合も例外ではなく `TOML_PARSE_ERROR` としてレポートに入ります。

エラー：入力が文字列でない `FLOW_TOML_MISSING` / 上限超過 `FLOW_TOML_TOO_LARGE`。

> [!note]
> このノードは**ファイルシステムに触りません**。`in.flow_toml` にパス文字列を渡しても
> ファイルとして開かれず、その文字列自体が TOML として解釈されます。

---

## llm

LLM 推論を源内OSS の API へ委譲します。`config_type` で model_id / system_prompt /
inference_config を設定から引き、`in` の中身をそのまま入力として渡します。

```toml
[[steps]]
id = "summarize"
type = "llm"
config_type = "answer_generation"   # 設定の参照キー（後述）
in = { table = "$.tables.summary" } # 渡したい値を任意の名前で
out = "vars.msg"
schema = { type = "object", required = ["title"] }  # 任意：構造化出力の最小検証
```

| キー | 種別 | 説明 |
|---|---|---|
| `in.*` | 入力 | 任意の名前で値を渡せる（プロンプト材料）。 |
| `out` | 出力 | scalar。`schema` 指定時は dict、無指定時は文字列を想定。 |
| `config_type` | 任意 | 設定の参照キー（既定 `"default"`）。 |
| `schema` | 任意 | `{type:"object", required:[...]}` の最小 JSON Schema。欠落で `SCHEMA_MISMATCH`。 |

### config_type と設定の解決

`config_type` は LLM 設定（model_id / system_prompt / inference_config）を引く窓口です。
設定は **defaults と app の 2 レイヤ**を `config_type` ごとに deep-merge します。

```
defaults["default"]  <  defaults[type]  <  app["default"]  <  app[type]
```

- CLI `run` / `serve` では `GWR_LLM_MODEL` / `GWR_LLM_SYSTEM_PROMPT`（または `--config`）が
  `[default]` として渡ります（[CLI](cli.md) 参照）。本番（クラウド版）では源内OSS 側の設定が使われます。
- `model_id` が解決できないと `ConfigError`。
- 設定を注入しない場合は、ステップに直接 `model_id` / `system_prompt` /
  `inference_config` を書くフォールバックもあります。

エラー：アダプタ未注入 `ADAPTER_MISSING` / schema 不一致 `SCHEMA_MISMATCH` /
委譲先の失敗 `DELEGATE_*`（→ [委譲先の失敗](#委譲先の失敗)）。

---

## retrieval

RAG 検索を源内OSS の API へ委譲し、正規化済みの `Doc[]` を返します。

```toml
[[steps]]
id = "retrieve"
type = "retrieval"
source = "law"                   # 検索対象（委譲先依存。任意）
top_k = 5                        # 取得件数（既定 5）
in = { query = "$.vars.question" }  # query は文字列必須
out = "vars.docs"                # Doc[] を書く
filters = { }                    # 任意：委譲先に渡す絞り込み
```

| キー | 種別 | 説明 |
|---|---|---|
| `in.query` | 入力 | 検索クエリ（文字列必須、でなければ `QUERY_MISSING`）。 |
| `out` | 出力 | `Doc[]`。各 Doc は `content` 等を持つ dict。 |
| `source` | 任意 | 検索対象の識別子（委譲先依存）。 |
| `top_k` | 任意 | 取得件数（既定 5）。 |
| `filters` | 任意 | 委譲先へ渡す追加フィルタ。 |

> **「該当なし」の扱い**：源内OSS の RAG は該当が無くても**空ではなく「該当なし」の文章**を
> 返します。そのため「空 = 該当なし」は成り立たず、運用注入のマーカーで判定します。
> 詳細は [委譲先 → RAG の該当なし判定](delegates.md#rag-の該当なし判定)。

エラー：アダプタ未注入 `ADAPTER_MISSING` / query が文字列でない `QUERY_MISSING` /
委譲先の失敗 `DELEGATE_*`（→ [委譲先の失敗](#委譲先の失敗)）。

---

## code_interpreter

指示（`instruction`）と入力ファイルを源内OSS の Code Interpreter へ委譲し、生成された
artifacts（グラフ等の `FileRef[]`）と分析テキストを回収します。実行環境（サンドボックス）は
委譲先にあり、gwr は持ちません。

```toml
[[steps]]
id = "analyze"
type = "code_interpreter"
instruction = "カテゴリごとの合計を計算し横棒グラフ（PNG）で可視化してください。"
in = { files = "$.files.data" }                 # FileRef または FileRef[]
out = { artifacts = "files.charts", output = "vars.msg" }
```

| キー | 種別 | 説明 |
|---|---|---|
| `instruction` | パラメータ | 実行指示（必須、空なら `INSTRUCTION_MISSING`）。 |
| `in.files` | 入力 | `FileRef` 単体または `FileRef[]`。 |
| `out.artifacts` | 出力 | 生成ファイル（`FileRef[]`）。 |
| `out.output` | 出力 | 分析テキスト（scalar）。 |

このノードは出力が 2 つ（`artifacts` / `output`）なので、`out` は**マップ形式**で書きます。

エラー：アダプタ未注入 `ADAPTER_MISSING` / instruction 未指定 `INSTRUCTION_MISSING` /
委譲先の失敗 `DELEGATE_*`（→ [委譲先の失敗](#委譲先の失敗)）。

---

## 型と静的検証

`gwr validate`（= `dry_run`）は各ノードの**入出力型シグネチャ**を使って、参照先が
書かれる前に読まれていないか（`READ_BEFORE_WRITE`）、型が食い違わないか（`TYPE_MISMATCH`）を
実行前に検査します。型は `TableRef` / `FileRef` / `Doc[]` / scalar の粒度で伝播します。
詳細は [CLI → validate](cli.md#validate)。

## 委譲先の失敗

`llm` / `retrieval` / `code_interpreter` は外部サービスへの委譲なので、フロー側の誤りとは
別に「相手が応じない」失敗が起きます。接続層（`TransportError`）と封筒（`EnvelopeError`）の
失敗は**ノード層で 4 つの理由コードへ畳まれ**、利用者には定型文が返ります。

| `reason` | 畳んでいるもの | 誰が直すか |
|---|---|---|
| `DELEGATE_ENDPOINT_INVALID` | 接続先が公開 HTTPS として不正（スキーム・ホスト無し・内部宛） | 運用者（`GWR_*_ENDPOINT`） |
| `DELEGATE_AUTH_FAILED` | トークン取得の失敗、委譲先の 401 / 403 | 運用者（API キー・Keycloak の資格情報） |
| `DELEGATE_TIMEOUT` | タイムアウト、ポーリング打ち切り、429 | 利用者（時間をおいて再実行） |
| `DELEGATE_FAILED` | 接続失敗、委譲先が返したエラー、想定外の応答 | 利用者は再実行、運用者はログ |

振り分けは 2 段です。**接続先・認証・時間切れのように失敗の種類が確定するものを先に**決め、
それ以外（委譲先が応答を返したが失敗だったもの）は **HTTP ステータスで分けます**
（401 / 403 → `DELEGATE_AUTH_FAILED`、429 → `DELEGATE_TIMEOUT`、それ以外 → `DELEGATE_FAILED`）。
混雑（429）が `DELEGATE_TIMEOUT` になるのはこのためです。

**応答に出るのは理由コードと定型文だけ**です。url・ホスト名・応答本文は載せません
（運用者はサーバのログとトレースバックで見ます → [エラー時のレスポンス](flow-toml.md#エラー時のレスポンス)）。

既定（`on_error = "fail"`）ではフローが止まります。`skip` / `default` を明示したときだけ
続行し、そのときは**結果が欠落したまま成功応答が返る**ことに注意してください。
