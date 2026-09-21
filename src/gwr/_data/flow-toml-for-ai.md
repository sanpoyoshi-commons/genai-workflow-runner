# フロー TOML 仕様（生成タスク特化の凝縮版）

**このファイルの位置づけ**：AI にフロー TOML を書かせるための**凝縮版**です。1 ファイルで
書き切るのに必要な事実だけを集めてあります。**正は既存の docs** で、各節から該当箇所へ
リンクしています。記述が食い違ったらリンク先が正です。

- [フロー TOML の書き方](flow-toml.md) — 各要素の完全な説明
- [ノードリファレンス](nodes.md) — ノードごとのパラメータ全数
- [式](expressions.md) — JMESPath / CEL の文法
- [CLI リファレンス](cli.md) — `validate` と理由コード

**書いたら必ず検証すること**（→ [9. 検証手順](#9-検証手順)）：

```bash
uv run gwr validate <file> --json
```

`"ok": true` が返るまで直します。外部接続は一切起きません。

---

## 1. ファイルの 4 要素

フローは 1 つの TOML です。トップレベルは 4 つだけ。→ [詳細](flow-toml.md)

```toml
version = "1"        # 将来拡張用の予約フィールド。"1" と書く（現在は検証されない）
[[inputs]]  ...      # 入力契約（利用者から受け取るもの）
[[steps]]   ...      # フロー本体（やること）
[[outputs]] ...      # 出力写像（利用者へ返すもの）
```

実行は**完全ステートレス・fail-fast**。1 リクエスト＝ 1 回の実行で、中間データはメモリ内のみ。

### 最小例（これ単体で `validate` を通る）

```toml
version = "1"

[[inputs]]
key = "data"
type = "file"
label = "対象CSV"
required = true

[[steps]]
id = "load"
type = "file.load"
in = { file = "$.files.data" }
out = "tables.raw"

[[steps]]
id = "top"
type = "file.transform"
in = { table = "$.tables.raw" }
out = "tables.picked"
ops = [{ op = "head", n = 10 }]

[[steps]]
id = "write"
type = "file.write"
format = "csv"
name = "picked.csv"
in = { table = "$.tables.picked" }
out = "files.report"

[[outputs]]
key = "report"
from = "$.files.report"
type = "file"
label = "抽出結果"
```

---

## 2. スロット — 読みは `$.` 付き・書きは `$.` なし

ステップ間のデータは **Envelope** の名前空間付きスロットで渡します。フロー作者が使うのは
次の 3 つです。→ [詳細](flow-toml.md#envelope-とスロット)

| 名前空間 | 入るもの | 主な書き込み元 |
|---|---|---|
| `files.<key>` | ファイル（`FileRef`） | `[[inputs]]` の `file` 型 / `file.write` / `code_interpreter` |
| `tables.<key>` | 表（`TableRef`） | `file.load` / `file.transform` |
| `vars.<key>` | 文字列・数値・リスト・`Doc[]` | `[[inputs]]` の scalar 型 / `llm` / `retrieval` / `foreach` |

**最重要の書式**：

- **読む** → `in` の値に `$.` を**付ける**：`in = { table = "$.tables.raw" }`
- **書く** → `out` は `$.` を**付けない**：`out = "tables.summary"`

```toml
[[steps]]
id = "load"
type = "file.load"
in  = { file = "$.files.data" }   # 読む（$. 付き）
out = "tables.raw"                # 書く（$. なし）
```

`[[inputs]]` の束縛先は自動で決まります。**`type = "file"` は `files.<key>`、それ以外は
`vars.<key>`**。`key = "data"` の file 入力なら `$.files.data` で読めます。

**`required = true` は「値が入っていること」を見ます**（キーの存在ではありません）。
キーが無ければ `REQUIRED_MISSING`、空文字や空白のみ（半角/全角スペース・タブ・改行）なら
`REQUIRED_EMPTY` で実行時に止まります。どちらも実行時エラーで、`validate` の理由コードとは
別系統です（`validate` は入力値を見ません）。

> `$.` で始まらない `in` の値はリテラルとして渡ります（例 `in = { question = "データが空です" }`）。

---

## 3. ノード 7 種＋制御 2 種

使える `type` はこの 9 個だけです。これ以外は `UNKNOWN_NODE_TYPE`。→ [詳細](nodes.md)

| `type` | 役割 | `in` のキー名 | 出力型 | 必須パラメータ |
|---|---|---|---|---|
| `file.load` | csv/xlsx を表にする | `file`（`FileRef`） | `TableRef` | なし（`format` / `max_rows` は任意） |
| `file.transform` | 表を集計・整形 | `table`（`TableRef`） | `TableRef` | `ops`（→ [4](#4-filetransform-の-ops-8-種)） |
| `file.write` | 表や文字列をファイルにする | `table`（`TableRef`）／`format="text"` なら `text`（文字列） | `FileRef` | なし（`format` 既定 csv、`name` 任意。`text` に表計算の拡張子は不可） |
| `flow.validate` | フロー TOML の本文を検証 | `flow_toml`（文字列） | scalar（`{schema_version, ok, issues[]}`） | なし（`max_bytes` 任意） |
| `llm` | LLM 推論 | **任意の名前で複数可**（プロンプト材料） | scalar | なし（`config_type` 既定 `"default"`、`schema` 任意） |
| `retrieval` | RAG 検索 | `query`（文字列） | `Doc[]`（`vars` へ） | なし（`source` / `top_k` 既定 5 / `filters` 任意） |
| `code_interpreter` | コード実行・可視化 | `files`（`FileRef` か `FileRef[]`） | `artifacts`＝`FileRef[]` ＋ `output`＝scalar | `instruction` |
| `branch` | 条件分岐 | （`when` を使う） | — | `when` / `then` / `else` の 3 つ全部 |
| `foreach` | 反復 | （`foreach` を使う） | 集約リスト | `foreach` / `body` |

**`file.write` の `format = "text"`** は表ではなく**文字列**を受け、そのまま UTF-8 で
ファイルにします（生成した TOML や Markdown を artifacts で返す用途）。

> [!warning] `format = "text"` は数式インジェクション無害化を**しません**
> 渡した文字列をそのまま書きます（TOML の `=` を壊さないため）。したがって
> **スプレッドシートで開かれる拡張子を `name` に付けてはいけません。**
> `.csv` / `.tsv` / `.xls` / `.xlsx` / `.xlsm` / `.slk` / `.dif` を付けると
> `TEXT_FORMAT_SPREADSHEET_NAME` で **`validate` が落ちます**（→ [7](#7-理由コード--直し方)）。
> 表を出したいときは `format = "csv"` / `"xlsx"` を使ってください。そちらは無害化されます。

**`flow.validate`** はフロー TOML の本文を受け、`gwr validate --json` と同じレポートを
返します。`when = "$.vars.<slot>.ok"` で「検証を通ったか」を分岐条件にできます。

**`llm` / `retrieval` / `code_interpreter` は委譲**です（実体は外部サービス）。失敗すると
**既定（`on_error = "fail"`）ではそこでフローが止まり**、`error.reason` に
`DELEGATE_ENDPOINT_INVALID` / `DELEGATE_AUTH_FAILED` / `DELEGATE_TIMEOUT` / `DELEGATE_FAILED`
のいずれかが返ります（接続先・認証・タイムアウト・その他）。

> [!warning] 委譲ノードに `on_error = "skip"` / `"default"` を安易に付けない
> 続行すると、**結果が欠落したまま成功応答が返ります**（要約が空のまま「成功」に見える）。
> 付けてよいのは、欠落しても意味が通る出力だけです。

**`code_interpreter` は出力が 2 つ**なので `out` をマップで書きます：

```toml
out = { artifacts = "files.charts", output = "vars.msg" }
```

`branch` / `foreach` の書き方：

```toml
[[steps]]
id = "gate"
type = "branch"
when = "len($.tables.summary.data) == 0"   # CEL。bool を返すこと
then = "empty"                             # 真のとき飛ぶ id（"__end__" 可）
else = "write"                             # 偽のとき飛ぶ id（"__end__" 可）。省略不可

[[steps]]
id = "loop"
type = "foreach"
foreach = "$.vars.items"   # 反復対象のリスト参照（必須）
as = "item"                # 各要素の束縛名 → vars.item（既定 "item"）
body = ["score"]           # 各要素で実行する step id 列（必須）
collect = "$.vars.one"     # 各反復後に集める値の参照
out = "vars.scores"        # collect の結果リストを書くスロット

[[steps]]
id = "score"               # body に挙げた step は直線走査から除外される
type = "llm"
in = { item = "$.vars.item" }
out = "vars.one"
```

反復上限は既定 1000（超過で停止）。参照先が `null` なら空リスト、リスト以外はエラー。

---

## 4. `file.transform` の `ops` 8 種

`ops` は上から順に適用されます。これ以外の `op` はありません。→ [詳細](nodes.md#filetransform)

| `op` | パラメータ | 動作 |
|---|---|---|
| `select` | `columns = [...]` | 指定列だけ残す。 |
| `rename` | `columns = { 旧 = 新 }` | 列名を改名。 |
| `filter` | `column` ＋ `eq`/`ne`/`gt`/`ge`/`lt`/`le` のいずれか 1 つ | 条件で行を絞る。 |
| `dropna` | `columns = [...]`（任意） | 欠損行を除去。 |
| `sort` | `by = [...]`, `ascending`（既定 `true`） | 安定ソート。 |
| `head` | `n` | 先頭 n 行。 |
| `groupby` | `by = [...]`, `agg = { 列 = 集計 }` | キー順に集約（`sum` / `mean` 等）。 |
| `sanitize` | （なし） | 数式インジェクション無害化。`file.write` の `format = "csv"` / `"xlsx"` は自動適用（`"text"` は**しない**→ [3](#3-ノード-7-種制御-2-種)）。 |

```toml
ops = [
  { op = "dropna", columns = ["売上"] },
  { op = "groupby", by = ["部署"], agg = { "売上" = "sum" } },
  { op = "sort", by = ["売上"], ascending = false },
]
```

> **TOML のベアキーは ASCII のみ**です。非 ASCII の列名は `"売上" = "sum"` と引用します。

---

## 5. 2 つの式言語 — 参照は JMESPath・条件は CEL

**使い分けは場所で決まります。迷う余地はありません。** → [詳細](expressions.md)

| 書く場所 | 言語 | 返すもの |
|---|---|---|
| `in` の値 / `[[outputs]]` の `from` / `foreach` / `collect` | **JMESPath**（`$.` 前置） | 任意の値 |
| `branch` の `when` | **CEL** | **bool のみ** |

### 参照（JMESPath）

```toml
in = { table = "$.tables.raw" }
in = { text  = "$.vars.docs[0].content" }
in = { v     = '$.tables.t.data[0]."売上"' }   # 非 ASCII キーは二重引用符で囲む
```

### 条件（CEL）

**`when` で呼べる関数は次の 10 個だけ**です。これ以外を書くと `BRANCH_WHEN_UNCOMPILABLE`。

```
len  size  int  double  string  bool  has  startsWith  endsWith  contains
```

- **`matches`（正規表現）は使えません。** ReDoS 回避のため意図的に除外しています。
  文字列判定は `startsWith` / `endsWith` / `contains` で書きます。
- 制限：**式は 500 文字以内 / 括弧のネストは 32 まで / 評価タイムアウト 2.0 秒**。
- **bool 以外を返すとエラー**です。`when = "$.vars.n"` は不可、`when = "$.vars.n > 0"` と書きます。

```toml
when = "len($.tables.summary.data) == 0"
when = "$.vars.count > 10 && contains($.vars.msg, 'エラー')"
```

---

## 6. `next = "__end__"` の落とし穴

`next` を省略したステップは、**リスト上の次のステップへフォールスルー**します。ジャンプで
到達したステップも同じです。そのため「ここで終わり」を明示しないと、**分岐の片側が
もう片方へなだれ込みます**。

```toml
[[steps]]
id = "gate"
type = "branch"
when = "len($.tables.summary.data) == 0"
then = "empty"
else = "write"

[[steps]]
id = "write"
type = "file.write"
in = { table = "$.tables.summary" }
out = "files.report"
next = "__end__"        # ← これが無いと write の後に empty まで実行されてしまう

[[steps]]
id = "empty"
type = "llm"
in = { question = "データが空です" }
out = "vars.msg"
```

- `next = "__end__"` はフローの正常終了です（予約 id）。`then` / `else` にも書けます。
- **`validate` はこの落とし穴を検出しません。** データフロー上は矛盾しないためです。
  分岐を書いたら、合流させない側に `next = "__end__"` を置いたか必ず目視してください。

---

## 7. 理由コード → 直し方

`validate --json` が返す `reason` の全数です。**正は
[CLI リファレンスの理由コード一覧](cli.md#理由コード一覧)**（実装とテストで突合済み）。

| `reason` | 直し方 |
|---|---|
| `MISSING_ID` | その `[[steps]]` に一意な `id = "..."` を足す。 |
| `DUPLICATE_ID` | 片方を別名に。`then` / `else` / `next` / `body` の参照先も直す。 |
| `MISSING_TYPE` | [3 の表](#3-ノード-7-種制御-2-種)の 9 種から `type` を足す。 |
| `UNKNOWN_NODE_TYPE` | 綴りを直す。9 種以外は使えない。 |
| `NEXT_UNRESOLVED` | 実在する step の `id` にする。終わらせたいなら `next = "__end__"`。 |
| `BRANCH_MISSING_WHEN` | CEL 条件を `when = "..."` で足す。 |
| `BRANCH_MISSING_THEN` | 真のときの飛び先 `id`（または `"__end__"`）を足す。 |
| `BRANCH_MISSING_ELSE` | 偽のときの飛び先 `id`（または `"__end__"`）を足す。省略不可。 |
| `BRANCH_WHEN_UNCOMPILABLE` | 許可関数 10 個だけを使い bool を返す式にする。500 字・ネスト 32 以内。`matches` 不可。 |
| `BRANCH_THEN_UNRESOLVED` | 実在する step の `id` か `"__end__"` にする。 |
| `BRANCH_ELSE_UNRESOLVED` | 実在する step の `id` か `"__end__"` にする。 |
| `FOREACH_MISSING_REF` | `foreach = "$.vars.items"` のように配列を指す参照を足す。 |
| `FOREACH_BODY_UNRESOLVED` | `body` の `id` を実在する step の `id` に直す。 |
| `BAD_REFERENCE` | 非 ASCII や記号を含むキーを二重引用符で囲む（`$.tables.t.data[0]."売上"`）。 |
| `TEXT_FORMAT_SPREADSHEET_NAME` | `file.write` の `format = "text"` に、表計算ソフトが開く `name` を付けている（`detail` ＝ その `name`）。`text` は無害化しない。表を出すなら `format = "csv"` / `"xlsx"` に、文字列をそのまま出すなら `name` を `.toml` / `.md` / `.txt` 等にする。対象＝`.csv` `.tsv` `.xls` `.xlsx` `.xlsm` `.slk` `.dif`。 |
| `READ_BEFORE_WRITE` | **その `in` が読むスロットを、上流いずれかの step の `out` で書く。** 綴り誤りが最多。`[[inputs]]` 由来なら `files.<key>` / `vars.<key>`。 |
| `OUTPUT_READ_BEFORE_WRITE` | `[[outputs]]` の `from` の綴りを直すか、そのスロットを書く step を足す。 |
| `TYPE_MISMATCH` | 要求型のスロットを渡す。`TableRef` が要るなら `file.load` / `file.transform` の出力、`FileRef` が要るなら `file` 入力か `file.write` の出力。 |
| `TOML_PARSE_ERROR` | `detail` の行・列を直す。非 ASCII のベアキーは不可。 |
| `FILE_UNREADABLE` | パスと文字コードを確認する。 |

---

## 8. 実用例

`file.load → 集計 → LLM 要約 → 空判定で分岐 → xlsx 出力` の一通りは
[`examples/poc.toml`](../examples/poc.toml) にあります。新しいフローはこれを写して
組み替えるのが早道です。骨格：

```toml
version = "1"

[[inputs]]
key = "data"
type = "file"
label = "対象Excel"
required = true
accept = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

[[steps]]
id = "load"
type = "file.load"
in = { file = "$.files.data" }
out = "tables.raw"

[[steps]]
id = "agg"
type = "file.transform"
in = { table = "$.tables.raw" }
out = "tables.summary"
ops = [{ op = "groupby", by = ["部署"], agg = { "売上" = "sum" } }]

[[steps]]
id = "summarize"
type = "llm"
config_type = "answer_generation"
in = { table = "$.tables.summary" }
out = "vars.msg"

[[steps]]
id = "gate"
type = "branch"
when = "len($.tables.summary.data) == 0"
then = "empty"
else = "write"

[[steps]]
id = "write"
type = "file.write"
format = "xlsx"
name = "summary.xlsx"
in = { table = "$.tables.summary" }
out = "files.report"
next = "__end__"

[[steps]]
id = "empty"
type = "llm"
config_type = "answer_generation"
in = { question = "データが空です" }
out = "vars.msg"

[[outputs]]
key = "msg"
from = "$.vars.msg"
type = "text"
label = "要約メッセージ"

[[outputs]]
key = "report"
from = "$.files.report"
type = "file"
label = "加工済みExcel"
```

`[[outputs]]` の `type` は `"text"`（既定・`label` が `## 見出し` になる）か `"file"`
（`FileRef` を artifacts に積む）の 2 つです。

---

## 9. 検証手順

```bash
uv run gwr validate path/to/flow.toml --json
```

```json
{
  "schema_version": 1,
  "ok": false,
  "issues": [
    {
      "category": "dataflow",
      "step_id": "write",
      "reason": "READ_BEFORE_WRITE",
      "detail": "tables.summary"
    }
  ]
}
```

1. `issues` の各件を [7 の表](#7-理由コード--直し方)で引いて直す。
2. **`"ok": true` が返るまで繰り返す。** 終了コードは 0（問題なし）/ 1（あり）。
3. `static` カテゴリの指摘があるときは `dataflow` / `type` の検査は走りません。
   構造を直してからもう一度かけると、次の層の指摘が出ます。

`validate` は外部に一切接続しません。手元で何度実行しても安全です。
ローカル実走まで確かめるなら `uv run gwr run <file> --file data=... -o out`
（LLM は既定で Fake。→ [CLI](cli.md#run)）。
