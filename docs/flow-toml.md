# フロー TOML の書き方

gwr のフローは 1 つの TOML ファイルで宣言します。ファイルは 4 つのトップレベル要素で
構成されます。

```toml
version = "1"        # 1. フォーマット版（"1" を記載。将来拡張用の予約フィールドで現在は検証されない）
[[inputs]]  ...      # 2. 入力契約（利用者から受け取るもの）
[[steps]]   ...      # 3. フロー本体（やること）
[[outputs]] ...      # 4. 出力写像（利用者へ返すもの）
```

実行は **完全ステートレス・fail-fast**。1 リクエスト = 1 回の `invoke`、中間データは
プロセスメモリ内のみで、失敗時は破棄して停止します（再開・永続化はしません）。

- 入力 → [`[[inputs]]`](#1-inputs--入力契約)
- やること → [`[[steps]]`](#2-steps--フロー本体)
- 分岐・反復 → [制御フロー](#制御フローbranch--foreach)
- 出力 → [`[[outputs]]`](#3-outputs--出力写像)
- スロットとデータ受け渡し → [Envelope とスロット](#envelope-とスロット)

各ノードの細かいパラメータは [ノードリファレンス](nodes.md)、`$.` 参照や `when` 条件の
文法は [式](expressions.md) を参照してください。

---

## Envelope とスロット

ステップ間のデータ受け渡しは **Envelope** という名前空間付きの入れ物で行います。
ステップは入力をスロットから読み、出力をスロットへ書きます。スロットは
ドット区切りのパスで、慣習的に次の名前空間を使います。

| 名前空間 | 用途 | 主な書き込み元 |
|---|---|---|
| `files.<key>` | ファイル（`FileRef`） | `[[inputs]]` の file 型、`file.write`、`code_interpreter` |
| `vars.<key>` | スカラ・文字列・リスト・Doc[] | `[[inputs]]` の scalar 型、`llm`、`retrieval`、`foreach` |
| `tables.<key>` | 表（`TableRef`） | `file.load`、`file.transform` |

スロットを**読む**ときは先頭に `$.` を付けた参照式を使います（例 `$.tables.raw`）。
**書く**ときは `out` に `$.` なしのスロットパスを書きます（例 `out = "tables.raw"`）。

```toml
[[steps]]
id = "load"
type = "file.load"
in  = { file = "$.files.data" }   # files.data を読む（$. 付き）
out = "tables.raw"                # tables.raw に書く（$. なし）
```

---

## 1. `[[inputs]]` — 入力契約

`[[inputs]]` の 1 宣言から、次の **3 つが同時に導出**されます。

1. **源内 UI の自動生成** … `gwr ui-spec` が出すリクエスト形式 JSON。
2. **入力検証** … 必須・型・最小最大などを実行前に検査（fail-closed）。
3. **Runner への束縛** … file は `files.<key>`、scalar は `vars.<key>` へ自動で入る。

```toml
[[inputs]]
key = "data"          # 束縛先スロット名（→ files.data または vars.data）
type = "file"         # コンポーネント種別（下表）
label = "対象Excel"   # UI の表示名（省略時は key）
required = true       # 必須か（省略時 false）
accept = "..."        # type 固有の任意パラメータ（下表）
```

### コンポーネント種別と束縛先

| `type` | 束縛先 | 値 | 任意パラメータ |
|---|---|---|---|
| `text` | `vars.<key>` | 文字列 | `desc` `min_length` `max_length` `default_value` |
| `textarea` | `vars.<key>` | 文字列 | `desc` `min_length` `max_length` `default_value` |
| `number` | `vars.<key>` | int/float | `desc` `min` `max` `default_value` |
| `file` | `files.<key>` | `FileRef`（`multiple=true` なら `FileRef[]`） | `desc` `accept` `multiple` `max_size` `max_file_count` |
| `select` | `vars.<key>` | 選択値 | `desc` `items`（**必須**） `default_value` |
| `checkbox` | `vars.<key>` | 選択値の配列 | `desc` `items`（**必須**） `default_value` |
| `radio` | `vars.<key>` | 選択値 | `desc` `items`（**必須**） `default_value` |
| `hidden` | `vars.<key>` | 文字列 | `default_value` |

- `select` / `checkbox` / `radio` は `items` が無いと検証エラー（`INPUT_MISSING_ITEMS`）。
- `number` は文字列で来ても数値化し、整数なら int に丸めます。非数値は `NOT_NUMERIC`。
- `checkbox` の値は内部でカンマ区切り → 配列に正規化されます。
- 予約キー `conversation_history`（会話継続用）は宣言すると自動的に `hidden` 固定。
- `required` で値が無ければ `REQUIRED_MISSING`。`default_value` があれば未指定時にそれが入る。

> ファイルの受信形式（源内本来の `inputs.files[]` 集約形と per-key 形の両対応）は
> 仕様としては自動処理されるので、フロー作者が意識する必要はありません。

---

## 2. `[[steps]]` — フロー本体

`[[steps]]` は**宣言順に並んだフラットなリスト**です。各ステップは共通の骨格を持ちます。

```toml
[[steps]]
id   = "agg"                       # ステップ ID（フロー内で一意・必須）
type = "file.transform"            # ノード種別（必須）
in   = { table = "$.tables.raw" }  # 入力（ノード入力名 → 参照/リテラル）
out  = "tables.summary"            # 出力スロット（文字列 or テーブル）
# --- 以下はノード固有のパラメータ（例：file.transform の ops）---
ops  = [{ op = "groupby", by = ["部署"], agg = { "売上" = "sum" } }]
# --- 以下は任意の共通制御キー ---
next     = "write"                 # 次に飛ぶ ID（省略時は次のステップへ）
on_error = "fail"                  # エラー時の挙動（既定 fail）
```

### `in` — 入力

`in` はノードの入力名をキー、参照式またはリテラルを値にしたテーブルです。

- `$.` で始まる文字列は**参照**として解決されます（例 `"$.tables.raw"`、`"$.vars.docs[0].content"`）。
- それ以外はそのまま**リテラル**として渡ります（例 `in = { question = "データが空です" }`）。

各ノードが受け取る入力名は [ノードリファレンス](nodes.md) を参照。

### `out` — 出力

ノードの結果を Envelope のスロットへ書きます。2 形式あります。

```toml
# 単一出力：ノードの主要出力を 1 スロットへ
out = "tables.summary"

# 複数出力：ノード出力名 → スロットのマップ（code_interpreter など）
out = { artifacts = "files.charts", output = "vars.msg" }
```

### `next` と終端

- `next` 省略時は、リスト上の**次のステップへフォールスルー**します。
- `next = "<id>"` で任意のステップへジャンプできます。
- `next = "__end__"` でフローを正常終了します（予約 ID）。
  最後のステップでないところで「ここで終わり」にしたいとき必須
  （例：`write` の後に `empty` へ落ちないよう `next = "__end__"`）。

### `on_error` — ステップのエラー方針

| `on_error` | 挙動 |
|---|---|
| `fail`（既定） | そのステップで停止しフロー全体を失敗させる（fail-fast）。 |
| `skip` | そのステップを飛ばして続行（出力は書かない）。 |
| `default` | ステップの `default` 値を出力スロットへ書いて続行。 |

```toml
[[steps]]
id = "agg"
type = "file.transform"
in = { table = "$.tables.raw" }
out = "tables.summary"
on_error = "default"
default = { columns = [], data = [] }   # 失敗時に書く値
ops = [ ... ]
```

---

## 制御フロー（`branch` / `foreach`）

`branch` と `foreach` は Runner が直接解釈する制御ステップで、レジストリ上の
「ノード」ではありません（`registry` に登録された他ステップとは別扱い）。

### `branch` — 条件分岐

`when`（CEL 条件式）を評価し、真なら `then`、偽なら `else` の ID へジャンプします。

```toml
[[steps]]
id = "gate"
type = "branch"
when = "len($.tables.summary.data) == 0"   # CEL 条件（bool を返すこと）
then = "empty"                             # 真のとき飛ぶ ID
else = "write"                             # 偽のとき飛ぶ ID
```

- `then` / `else` には `__end__` も指定でき、その場で正常終了します。
- `when` の文法・使える関数は [式 → 条件評価](expressions.md#条件評価cel) を参照。
- `when` がコンパイルできない・bool を返さない場合は検証/実行時にエラー。

### `foreach` — 反復

リスト参照を走査し、各要素について `body`（ステップ ID 列）を実行、`collect` の値を
`out` へ集約します。

```toml
[[steps]]
id = "loop"
type = "foreach"
foreach = "$.vars.items"   # 反復対象のリスト参照
as = "item"                # 各要素を束縛する変数名（→ vars.item、既定 "item"）
body = ["score"]           # 各要素について実行するステップ ID 列
collect = "$.vars.one"     # 各反復後に集約する値の参照
out = "vars.scores"        # collect の結果リストを書くスロット

# body に挙げたステップは直線走査から除外され、foreach 経由でのみ実行される
[[steps]]
id = "score"
type = "llm"
config_type = "scoring"
in = { item = "$.vars.item" }
out = "vars.one"
```

- 反復ごとに `vars.<as>`（既定 `vars.item`）へ当該要素が入ります。
- `body` に挙げた ID は**直線走査の対象外**になり、foreach の中だけで実行されます。
- 反復数の上限は既定 **1000**（`GWR_MAX_FOREACH_ITEMS` で可変）。超過は
  `FOREACH_LIMIT_EXCEEDED` で停止（入力由来リストによる暴走防止）。
- 参照先が `null` のときは空リスト扱い、リストでなければ `FOREACH_NOT_LIST`。

---

## 3. `[[outputs]]` — 出力写像

Envelope のスロットを、源内レスポンスの `outputs`（テキスト）／`artifacts`（ファイル）へ
写像します。**利用者自身のデータ**なので値を含めて返します。

```toml
[[outputs]]
key = "msg"            # 出力の論理名
from = "$.vars.msg"    # 取り出すスロット参照（必須）
type = "text"          # "text"（既定）または "file"
label = "要約メッセージ" # 見出し（text のとき "## ラベル" として付く）
```

- `type = "text"`：値を文字列化し、`label` があれば `## ラベル` 見出し付きで連結。
  複数の text 出力は空行 2 つで結合され、1 つの Markdown 文字列 `outputs` になります。
- `type = "file"`：参照先が `FileRef`（`contents` を持つ）なら `artifacts` に積みます。
  ファイル名は `FileRef` の名前 → `label` → `key` の順で決まります。

### エラー時のレスポンス

入力検証や実行が失敗すると、`invoke` は値を出さず次を返します（中間データは破棄済み）。

```json
{
  "outputs": "3列目: ファイルを読み込めませんでした。",
  "artifacts": [],
  "error": { "reason": "FILE_PARSE_ERROR", "coordinates": { "col": 2 } }
}
```

利用者向けメッセージは**理由コード＋座標（行・列番号）のみ**で、セル値は載せません
（値フリーの方針）。

---

## 完成例

`file.load → 集計 → 要約 → 空判定 → 出力` の一通りは
[`examples/poc.toml`](../examples/poc.toml) を参照してください。書いたら必ず
[`gwr validate`](cli.md#validate) で構文・データフロー・型を静的チェックしてから動かします。
