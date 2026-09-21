# CLI リファレンス

`gwr` コマンドのサブコマンド一覧です。インストールは `uv sync`（serve を使うなら
`uv sync --extra serve`、実 LLM/RAG/CI 接続を使うなら `--extra http`、MCP サーバを
使うなら `--extra mcp`）。以降の例は `uv run gwr ...` 前提です。

| サブコマンド | 用途 | 外部 I/O |
|---|---|---|
| [`validate`](#validate) | フロー TOML を静的検証 | なし |
| [`spec`](#spec) | フロー TOML 仕様 1 枚を出力 | なし |
| [`ui-spec`](#ui-spec) | 源内OSS のリクエスト形式 JSON を出力 | なし |
| [`mcp`](#mcp) | MCP サーバを stdio で起動 | なし |
| [`run`](#run) | フローをローカル実走 | LLM は既定 Fake（env で実 LLM） |
| [`serve`](#serve) | `/invoke` を HTTP 公開 | LLM は既定 Fake（env で実 LLM） |
| [`smoke-llm`](#smoke-llm) | 委譲先 LLM を 1 回実叩き | あり |
| [`smoke-rag`](#smoke-rag) | 委譲先 RAG を 1 回実叩き | あり |
| [`smoke-ci`](#smoke-ci) | 委譲先 Code Interpreter を 1 回実叩き | あり |

委譲先の環境変数（`GWR_*`）は [委譲先への接続](delegates.md) にまとめています。

---

## validate

フロー TOML を実行せずに検証します（**構文・データフロー・型伝播**を fail-closed で検出）。

```bash
uv run gwr validate examples/poc.toml
```

問題なければ `OK: フロー検証に問題なし`（終了コード 0）。問題があれば 1 行 1 件で
`[カテゴリ] step_id: 理由 詳細` を出して終了コード 1。カテゴリは次の 3 種。

| カテゴリ | 検出するもの（例） |
|---|---|
| `static` | ID 重複・未知ノード型・未解決ジャンプ先・`when` のコンパイル不能・参照式の構文。 |
| `dataflow` | 書く前に読む（`READ_BEFORE_WRITE`）・出力の参照先が未書込。 |
| `type` | 入出力の型不一致（`TYPE_MISMATCH`）。 |

`serve` は起動時にこの検証を回し、不合格なら**起動を拒否**します。

| オプション | 説明 |
|---|---|
| `--json` | 検証結果を機械可読 JSON で出力する（終了コードは変わらない）。 |

### `--json`（機械可読出力）

AI にフロー TOML を書かせて自己修正させる場合など、出力を機械で読むときに使います。

```bash
uv run gwr validate examples/poc.toml --json
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

- **終了コードは `--json` の有無で変わりません**（問題なし 0 / あり 1）。`--json` 無しの
  テキスト出力も変わりません。
- `step_id` は**特定できる step が無いとき `null`**（TOML 自体が読めない場合、`id` が
  欠けている場合など）。`[[outputs]]` 由来の指摘は `"outputs"` になります。
- `--json` を付けたときだけ、TOML の構文エラーやファイル読み取り失敗も**例外ではなく
  JSON** で返ります（`TOML_PARSE_ERROR` / `FILE_UNREADABLE`）。
- `schema_version` は出力形の版です。**`1` の間は既存の `reason` を改名・削除しません**
  （追加のみ）。破壊的変更が要るときはこの数値を上げます。

### 理由コード一覧

`reason` の全数です。「直し方」は AI の自己修正の手がかりとして使えます。

| `reason` | カテゴリ | 意味 | 典型的な直し方 |
|---|---|---|---|
| `MISSING_ID` | `static` | step に `id` が無い。 | その `[[steps]]` に一意な `id = "..."` を足す。 |
| `DUPLICATE_ID` | `static` | 同じ `id` の step が 2 つ以上ある。 | 片方を別名にする。`then` / `else` / `next` / `body` の参照先も合わせて直す。 |
| `MISSING_TYPE` | `static` | step に `type` が無い。 | ノード 7 種か制御 2 種（`branch` / `foreach`）の `type` を足す（[ノード](nodes.md)）。 |
| `UNKNOWN_NODE_TYPE` | `static` | `type` が未知（`detail` ＝ 指定値）。 | 綴りを直す。使えるのは `file.load` / `file.transform` / `file.write` / `flow.validate` / `llm` / `retrieval` / `code_interpreter` / `branch` / `foreach` のみ。 |
| `NEXT_UNRESOLVED` | `static` | `next` の飛び先 `id` が存在しない（`detail` ＝ 飛び先）。 | 実在する step の `id` にする。そこで終わらせたいなら `next = "__end__"`。 |
| `BRANCH_MISSING_WHEN` | `static` | `branch` に `when` が無い。 | CEL の条件式を `when = "..."` で足す（[式](expressions.md)）。 |
| `BRANCH_MISSING_THEN` | `static` | `branch` に `then` が無い。 | 真のときの飛び先 `id`（または `"__end__"`）を足す。 |
| `BRANCH_MISSING_ELSE` | `static` | `branch` に `else` が無い。 | 偽のときの飛び先 `id`（または `"__end__"`）を足す。`else` は省略できない。 |
| `BRANCH_WHEN_UNCOMPILABLE` | `static` | `when` の CEL がコンパイルできない（`detail` ＝ 理由）。 | 許可関数 10 個だけを使い、bool を返す式にする。500 字 / 括弧ネスト 32 以内。`matches`（正規表現）は使えない。 |
| `BRANCH_THEN_UNRESOLVED` | `static` | `then` の飛び先 `id` が存在しない（`detail` ＝ 飛び先）。 | 実在する step の `id` か `"__end__"` にする。 |
| `BRANCH_ELSE_UNRESOLVED` | `static` | `else` の飛び先 `id` が存在しない（`detail` ＝ 飛び先）。 | 実在する step の `id` か `"__end__"` にする。 |
| `FOREACH_MISSING_REF` | `static` | `foreach` step に反復元の `foreach` 参照が無い。 | `foreach = "$.tables.raw.data"` のように配列を指す参照を足す。 |
| `FOREACH_BODY_UNRESOLVED` | `static` | `body` に挙げた `id` が存在しない（`detail` ＝ その `id`）。 | `body` の各 `id` を実在する step の `id` に直す。body の step は同じ `[[steps]]` 列に並べて書く。 |
| `BAD_REFERENCE` | `static` | `in` の `$.` 参照が JMESPath として不正（`detail` ＝ 理由）。 | 非 ASCII や記号を含むキーは二重引用符で囲む（例 `$.tables.t.data[0]."売上"`）。 |
| `TEXT_FORMAT_SPREADSHEET_NAME` | `static` | `file.write` の `format = "text"` と、表計算ソフトが開く `name` の組み合わせ（`detail` ＝ その `name`）。`text` は数式インジェクション無害化をしないため、無害化されていない内容が数式として解釈され得る。 | 表を出すなら `format = "csv"` / `"xlsx"` にする（そちらは無害化される）。文字列をそのまま出すなら `name` の拡張子を `.toml` / `.md` / `.txt` など表計算ソフトが開かないものにする。対象の拡張子は `.csv` / `.tsv` / `.xls` / `.xlsx` / `.xlsm` / `.slk` / `.dif`。 |
| `READ_BEFORE_WRITE` | `dataflow` | 読もうとしたスロットを、上流のどの step も書いていない（`detail` ＝ スロット）。 | その `in` が読むスロットを、上流いずれかの step の `out` で書く。綴り誤りが最多。`[[inputs]]` 由来なら `files.<key>` / `vars.<key>`。 |
| `OUTPUT_READ_BEFORE_WRITE` | `dataflow` | `[[outputs]]` の `from` が書かれていない（`detail` ＝ スロット、`step_id` は `"outputs"`）。 | `from` の綴りを直すか、そのスロットを `out` で書く step を足す。 |
| `TYPE_MISMATCH` | `type` | `in` に渡した型がノードの要求と違う（`detail` ＝ `スロット:実型 != 引数名 requires 要求型`）。 | 要求型のスロットを渡す。`TableRef` が要るなら `file.load` / `file.transform` の出力を、`FileRef` が要るなら `file` 入力か `file.write` の出力を渡す。 |
| `TOML_PARSE_ERROR` | `static` | TOML として読めない（`--json` 時のみ。`detail` ＝ 位置つきメッセージ、`step_id` は `null`）。 | `detail` の行・列を直す。非 ASCII のベアキーは不可（`"売上" = ...` と引用する）。 |
| `FILE_UNREADABLE` | `static` | ファイルを開けない / UTF-8 で読めない（`--json` 時のみ。`step_id` は `null`）。 | パスと文字コードを確認する。 |

> この表は実装の `reason` 定数集合と**テストで突合**しています（`tests/test_reason_codes.py`）。
> 片方だけ増やすとテストが落ちます。

---

## spec

フロー TOML の**仕様 1 枚**（Markdown）を標準出力へ出します。中身は
[docs/flow-toml-for-ai.md](flow-toml-for-ai.md) と同一で、MCP の `gwr_spec` ツールが返すものと
同じです。パッケージに同梱されているため、リポジトリの外でも使えます。

```bash
uv run gwr spec > flow-toml-spec.md
```

AI に渡して TOML を書かせる用途を想定しています。フロー側から使う例：

```bash
uv run gwr run examples/toml-generator.toml \
  --set request="Excelを部署別に集計して要約する" \
  --set spec="$(uv run gwr spec)"
```

---

## ui-spec

入力契約（`[[inputs]]`）から、ガバメントAI 源内 OSS のチーム管理に登録する
**リクエスト形式 JSON**を生成します。

```bash
uv run gwr ui-spec examples/poc.toml
```

出力をそのまま源内OSS の Web の ExApp 登録フォームに貼り付けます
（[デプロイと ExApp 登録](deploy.md)）。

---

## mcp

フロー TOML の作成を支援する **MCP サーバ**を stdio で起動します。MCP クライアント
（対応するエディタや AI クライアント）から接続すると、仕様・検証・実走・UI 定義生成を
ツールとして呼べるので、フローを手で書いて手で `validate` を叩く往復が要らなくなります。

```bash
uv sync --extra mcp
uv run gwr mcp
```

`mcp` extra が入っていない環境では、案内を stderr に出して終了コード 1 で終わります
（他のサブコマンドには影響しません）。

トランスポートは **stdio のみ**です。HTTP / SSE は提供しません。

### 公開するツール（4 本）

| ツール | 入力 | 出力 |
|---|---|---|
| `gwr_spec` | なし | フロー TOML 仕様 1 枚（Markdown）。[docs/flow-toml-for-ai.md](flow-toml-for-ai.md) と同じ内容 |
| `gwr_validate` | `flow_toml`（文字列） | [`validate --json`](#--json機械可読出力) と同一の `{schema_version, ok, issues[]}` |
| `gwr_dry_run` | `flow_toml`（文字列）、`inputs`（object） | `{outputs, artifacts_meta}`（失敗時のみ `error` が付く） |
| `gwr_ui_spec` | `flow_toml`（文字列） | [`ui-spec`](#ui-spec) と同一の JSON |

ツールは**ファイルパスを受け取りません**。フロー TOML は本文の文字列で渡します
（この層はファイルシステムに触りません）。本文の上限は 1 MiB です。ファイルから読むときは
先に中身を読み出してその文字列を渡してください。この注意は `flow_toml` 引数の説明として
入力スキーマにも載せてあります。

`gwr_dry_run` は委譲アダプタを **Fake に固定**しており、`GWR_LLM_ENDPOINT` などが
設定済みの環境でも実エンドポイントへは接続しません。`artifacts_meta` は名前・バイト数・
行列数のメタだけで、ファイル本体は返しません。実行全体の上限は 60 秒です。
`type = "file"` の `[[inputs]]` を持つフローは実ファイルが必要なため `gwr_dry_run` の
対象外で、その旨を返します（検証は `gwr_validate` が本文だけで完結します）。

### Resources

`examples/*.toml` を Resources として公開します。URI は `gwr://examples/<ファイル名>` で、
列挙は同梱された例に限定されます（外からパスを受け取らないため、同梱ディレクトリの外へ
出ることが構造的に起きません）。各 Resource には「何をする例か」の 1 行説明が付きます。

仕様 1 枚と例はパッケージに同梱されるため、リポジトリの外（wheel からのインストール先）
でも読めます。

---

## run

フローをローカルで実走します。**LLM ステップは既定で Fake**（決定論の固定文字列）になり、
`file.*` は実処理されるので、Excel/CSV 加工の動作確認に向きます。

```bash
uv run gwr run examples/poc.toml \
  --file data=path/to/input.xlsx \
  --set threshold=100 \
  -o out
```

| オプション | 説明 |
|---|---|
| `--file KEY=PATH` | ファイル入力。`KEY` は `[[inputs]]` の `key`。複数指定可。 |
| `--set KEY=VALUE` | スカラ入力（数値/文字列）。複数指定可。 |
| `-o, --out DIR` | artifacts の出力先（既定 `gwr-out`）。 |
| `--llm-fake TEXT` | LLM ステップに返す固定テキスト（既定 `(LLMモック出力)`）。 |
| `--config TOML` | LLM 設定 TOML（`model_id` / `system_prompt` 等）。省略時は最小既定。 |

- 結果の `outputs` テキストを標準出力に、`artifacts` を `--out` ディレクトリに書き出します。
- 環境変数 `GWR_LLM_ENDPOINT` を設定すると Fake ではなく**実 LLM**に委譲します
  （モデル等は `GWR_LLM_MODEL` / `GWR_LLM_SYSTEM_PROMPT`、詳細は [委譲先](delegates.md)）。
- `GWR_RAG_ENDPOINT` / `GWR_CI_ENDPOINT` を設定すれば RAG / CI も実委譲で実走できます。

---

## serve

`/invoke` を HTTP で公開します（源内OSS の Web に ExApp 登録して WebUI から叩く用）。
`gwr[serve]`（FastAPI + uvicorn）が必要です。

```bash
uv run gwr serve examples/poc.toml --host 0.0.0.0 --port 8000
```

| オプション | 説明 |
|---|---|
| `--host` | バインドアドレス（既定 `127.0.0.1`）。 |
| `--port` | ポート（既定 `8000`）。 |
| `--llm-fake` / `--config` | `run` と同じ（LLM の既定 Fake / 設定 TOML）。 |

公開されるエンドポイント：

| メソッド・パス | 用途 |
|---|---|
| `POST /invoke` | `{inputs: {...}} → {outputs, artifacts}`。本体。 |
| `GET /ui-spec` | リクエスト形式 JSON（`ui-spec` と同じ）。 |
| `GET /healthz` | `{status: "ok"}`。 |

- 環境変数 `GWR_API_KEY` を設定すると、源内OSS の Web の ExApp 呼び出しと同じ `x-api-key`
  ヘッダを検証します（不一致は 401）。未設定なら認証なし（ローカル確認用）。
- 起動時に全フローを `validate` し、不合格なら起動を拒否します（fail-closed）。
- LLM / RAG / CI の実委譲は `run` と同じ `GWR_*_ENDPOINT` 系の環境変数で切り替えます。

---

## smoke-llm

委譲先の LLM を **1 回だけ実叩き**して応答を表示します（フローを介さない疎通確認）。

```bash
uv run gwr smoke-llm \
  --endpoint http://ollama:11434/v1/chat/completions \
  --model gemma2:2b \
  --prompt "こんにちは" \
  --allow-insecure
```

| オプション | 説明 |
|---|---|
| `--endpoint` | LLM 端点（必須）。 |
| `--model` | モデル ID（必須）。 |
| `--prompt` | ユーザープロンプト（必須）。 |
| `--system` | system プロンプト。 |
| `--mode` | `chat`（既定）/ `responses`。 |
| `--api-key` | 本番＝委譲先 ExApp の `x-api-key`。 |
| `--allow-insecure` | http / 内部宛を許可（オンプレ版 等の信頼済み委譲先）。 |
| `--insecure-tls` | 自己署名 TLS の検証を無効化。 |
| `--timeout` | 秒（既定 120）。 |

---

## smoke-rag

委譲先 RAG を 1 回実叩きし、`docs`（該当あり/なし）を表示します。**該当なしマーカー**を
渡すと、委譲先が返す「該当なし」文を `docs=[]` に落とす挙動を確認できます
（[RAG の該当なし判定](delegates.md#rag-の該当なし判定)）。

```bash
uv run gwr smoke-rag \
  --endpoint https://api:3443/api/law-rag/query \
  --question "育児休業の期間は？" \
  --kc-token-url http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token \
  --kc-user "$USER" --kc-pass "$PASS" \
  --allow-insecure --insecure-tls \
  --no-result-markers-file examples/rag-no-result-markers.example.txt
```

| オプション | 説明 |
|---|---|
| `--endpoint` | RAG 端点（必須）。 |
| `--question` | 質問（必須）。 |
| `--source` | 検索対象（任意）。 |
| `--top-k` | 取得件数（既定 5）。 |
| `--no-result-markers-file` | 「該当なし」文を 1 行 1 つ書いたファイル（`#` はコメント）。 |
| `--no-result-marker TEXT` | 「該当なし」と見なす `outputs` 文（複数指定可）。 |
| 認証・接続 | 下記「smoke 共通の認証オプション」。 |

---

## smoke-ci

委譲先 Code Interpreter を 1 回実叩きし、`output` と生成 `artifacts` を表示します。

```bash
uv run gwr smoke-ci \
  --endpoint https://api:3443/api/code-interpreter/responses \
  --instruction "カテゴリ別合計を棒グラフ（PNG）にして" \
  --file data.csv \
  --kc-token-url ... --kc-user "$USER" --kc-pass "$PASS" \
  --allow-insecure --insecure-tls -o out
```

| オプション | 説明 |
|---|---|
| `--endpoint` | CI 端点（必須）。 |
| `--instruction` | 指示文（必須）。 |
| `--file PATH` | 入力ファイル（csv/xlsx）。複数指定可。 |
| `-o, --out DIR` | artifacts 出力先（既定 `gwr-out`）。 |
| 認証・接続 | 下記「smoke 共通の認証オプション」。 |

---

## smoke 共通の認証オプション（smoke-rag / smoke-ci）

委譲先の**契約は同一で違うのは認証だけ**です。本番（クラウド版）は `x-api-key`、オンプレ版は
Keycloak Bearer。

| オプション | 説明 |
|---|---|
| `--api-key` | 本番＝委譲先 ExApp の `x-api-key`。 |
| `--user-id` | `x-user-id`（監査用・任意）。 |
| `--kc-token-url` | オンプレ版: Keycloak token 端点。 |
| `--kc-client-id` | オンプレ版: password grant の client_id（既定 `genai-web-dev`）。 |
| `--kc-user` / `--kc-pass` | オンプレ版: Keycloak のユーザー名 / パスワード。 |
| `--allow-insecure` | http / 内部宛を許可。 |
| `--insecure-tls` | 自己署名 TLS の検証を無効化。 |
| `--timeout` | 秒（既定 600。law-rag/CI は CPU で数分かかり得るため長め）。 |

`--kc-user` を渡すとオンプレ版用に password grant で Bearer を取得し、無ければ `--api-key` を
`x-api-key` として使います。詳細な環境変数は [委譲先への接続](delegates.md)。
