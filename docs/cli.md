# CLI リファレンス

`gwr` コマンドのサブコマンド一覧です。インストールは `uv sync`（serve を使うなら
`uv sync --extra serve`、実 LLM/RAG/CI 接続を使うなら `--extra http`）。以降の例は
`uv run gwr ...` 前提です。

| サブコマンド | 用途 | 外部 I/O |
|---|---|---|
| [`validate`](#validate) | フロー TOML を静的検証 | なし |
| [`ui-spec`](#ui-spec) | 源内リクエスト形式 JSON を出力 | なし |
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

---

## ui-spec

入力契約（`[[inputs]]`）から、源内チーム管理に登録する**リクエスト形式 JSON**を生成します。

```bash
uv run gwr ui-spec examples/poc.toml
```

出力をそのままgenai-web の ExApp 登録フォームに貼り付けます
（[デプロイと ExApp 登録](deploy.md)）。

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

`/invoke` を HTTP で公開します（genai-web に ExApp 登録して WebUI から叩く用）。
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

- 環境変数 `GWR_API_KEY` を設定すると、genai-web の ExApp 呼び出しと同じ `x-api-key`
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
