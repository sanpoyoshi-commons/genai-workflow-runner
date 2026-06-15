# genai-workflow-runner (gwr)

genai-webプロトコル準拠の汎用AIワークフローランナー。TOML で多段の AI 業務ワークフローを
「部品（ノード）＋レシピ（TOML）」として量産する、**完全オリジナル・Apache-2.0** の実装です。

> [genai-web](https://github.com/digital-go-jp/genai-web) に**プロトコル準拠の AI アプリ**として登録できる、独立したマイクロサービス
> （ライブラリ核＋`/invoke` を出す薄い HTTP 層）です。

## 特長

- **フラットな step リスト＋id 参照ジャンプ**で記述する宣言的フロー（`version="1"`）。
- **fail-fast・完全ステートレス**実行（失敗時は中間データを破棄、再開なし）。
- **入力契約 `[[inputs]]`** 1 宣言から、源内 UI 用 JSON 生成・入力検証・実行時束縛を導出。
- **出力写像 `[[outputs]]`** で結果を源内 `outputs`（テキスト）／`artifacts`（ファイル）へ。
- **値フリーの構造化ログ**：ログにセル値を渡す経路を作らない。データ起因エラーは
  座標（行・列番号）＋理由コードのみ。
- **静的検証**（`gwr validate`）：構文／データフロー／型伝播を実行前に検出（fail-closed）。
- **外部アクセスを持たない**：LLM・検索・コード実行は源内 API への委譲アダプタ＋テストモック。
  自前サンドボックス・自前 LLM は持たない。

## クイックスタート（ローカル確認）

ここで行うのは **ローカルでの動作確認だけ**です ―― フロー TOML が正しく書けているかの
**検証・実行**と、ライブラリ本体の**単体テスト**。外部 I/O（LLM・検索・コード実行）は
すべて Fake／mock で、ネットワークにも源内にも接続しません。genai-web への公開・登録は
[次節](#デプロイと-exapp-登録するには)を参照してください。

### 必要なソフト

唯一の前提は **uv**（Python のパッケージ／プロジェクト管理ツール）です。Python 3.12+ は
uv が自動で用意します。

> **Windows 11 の方**：先に [WSL2 + Ubuntu のセットアップ](docs/prerequisites.md) で土台を
> 用意してください（gwr のローカル確認に Docker は不要です）。

```bash
# uv をインストール（既にあれば不要）
curl -LsSf https://astral.sh/uv/install.sh | sh

uv --version              # uv が入ったか確認

uv sync                   # 依存を同期（必要なら Python 3.12+ も uv が取得）
uv run python --version   # 3.12 以上であることを確認
```

### フロー TOML のテスト

```bash
# 静的検証：構文・データフロー・型伝播を実行前に検出（fail-closed）
uv run gwr validate examples/hello.toml

# ローカル実行：委譲（LLM/検索/CI）は Fake、外部に一切出ない
uv run gwr run examples/hello.toml
```

`examples/poc.toml` は load →集計→要約→分岐→出力の多段サンプルです（実行にはファイル入力が必要）。

### 単体テスト

```bash
# 外部 I/O は全て mock・ローカル完結
uv run pytest
```

## デプロイと ExApp 登録するには

gwr をgenai-web に**プロトコル準拠の AI アプリ（ExApp）**として登録すると、WebUI から
フローを実行できます。公開（`gwr serve` / Docker）・`gwr ui-spec` によるリクエスト形式
JSON の生成・本番（クラウド版）とオンプレ版での実機テストそれぞれの登録手順は、次を参照してください。

→ **[デプロイと ExApp 登録](docs/deploy.md)**

## ドキュメント

利用者向けの使い方は [`docs/`](docs/README.md) に体系化しています。

- [フロー TOML の書き方](docs/flow-toml.md) — `inputs` / `steps` / `outputs`・制御フロー。
- [ノードリファレンス](docs/nodes.md) — 各ノードの入出力・パラメータ。
- [式（参照と条件）](docs/expressions.md) — `$.` 参照（JMESPath）と `when`（CEL）。
- [CLI リファレンス](docs/cli.md) — `validate` / `ui-spec` / `run` / `serve` / `smoke-*`。
- [委譲先への接続と環境変数](docs/delegates.md) — LLM/RAG/CI の `GWR_*` 一覧・該当なし判定。
- [デプロイと ExApp 登録](docs/deploy.md) — 公開・genai-web への本番登録手順（付録: オンプレ版での実機テスト）。

## ノード

| ノード | 役割 |
|---|---|
| `file.load` | csv/xlsx を読み込み表（TableRef）にする |
| `file.transform` | 表を集計・整形（groupby/filter/sort/…・数式インジェクション無害化） |
| `file.write` | 表を csv/xlsx に出力し artifacts（base64）にする |
| `branch` | 条件分岐（CEL 評価で step id へジャンプ） |
| `foreach` | 反復（リストを走査し body を実行・集約） |
| `llm` / `retrieval` / `code_interpreter` | 源内 API への委譲（実体は源内側・テストは mock） |

## フロー例（抜粋）

`examples/poc.toml` 参照。`file.load → file.transform(集計) → llm(要約) → branch(空判定)
→ file.write` を 1 本同期実行します。

## 委譲先（LLM / RAG / CI）への接続

委譲先の **リクエスト/レスポンス契約は同一**（封筒 `{inputs:{…}} → {outputs, artifacts?}`）で、
**違うのは認証だけ**です。

- **本番（クラウド版）**：委譲先は登録済み ExApp の公開 HTTPS で、認証は **`x-api-key`**
  （`GWR_LLM_API_KEY` / `GWR_RAG_API_KEY` / `GWR_CI_API_KEY`）。
- **オンプレ版**：同じ封筒ルートが中央 `api` に集約され **Keycloak Bearer** ゲート。実機テストでは
  `GWR_KC_TOKEN_URL` / `GWR_KC_USER` / `GWR_KC_PASS`（public client `genai-web-dev` の password
  grant）でトークンを取得し `Authorization: Bearer` を載せる。これはオンプレ版用の足場で、本番
  アダプタは `x-api-key` のまま（認証ヘッダの差し替えのみ）。LLM はオンプレ版では ollama 直叩き。

実機スモーク：`gwr smoke-llm` / `gwr smoke-rag` / `gwr smoke-ci`。

### RAG の「該当なし」判定

源内 RAG は該当が無くても **空ではなく「該当なし」の文章を 200 で返す**（GCP lawsy 由来の固定文
3 種をオンプレ版も忠実ポート／AWS は LLM 生成文で常に非空）。したがって「空＝該当なし」は成り立たず、
gwr は **運用注入の no-result マーカー**で判定します（`GWR_RAG_NO_RESULT_MARKERS_FILE` または
`GWR_RAG_NO_RESULT_MARKERS`。例＝`examples/rag-no-result-markers.example.txt`。3 種の文言を
コメント付きで同梱、既定は無効）。`outputs` がマーカーに一致すると `docs=[]` となり、フローは
`branch when="len($.vars.docs)==0"` で該当なし分岐へ落とせます。源内固有の文言は gwr 本体に
持たせず運用設定で注入する方針（本体は特定サービス非依存に維持）。

## genai-web への載せ方

HTTP 層（`gwr.serve.create_app`）が `/invoke`（`{inputs} → {outputs, artifacts}`）と
`/ui-spec` を提供します。genai-web に AI アプリとして登録し、認証・チーム権限・endpoint・
API キーはgenai-web 側が管理します。

## ライセンス

Apache-2.0。源内のソースコードは 1 行もコピーしておらず、API 仕様にのみ準拠した
完全オリジナル実装です（源内由来の帰属義務はありません）。

## 免責

本リポジトリは上流（[`digital-go-jp/genai-web`](https://github.com/digital-go-jp/genai-web)）が公開する
プロトコル／API 仕様にのみ準拠した独立・非公式の完全オリジナル実装で、デジタル庁・上流・AWS とは
無関係（非提携・非公認）です。詳細は [DISCLAIMER.md](DISCLAIMER.md) を参照してください。

本ソフトウェアは現状有姿で提供されます。実 LLM／検索／実データ連携は利用者環境の
源内 API 設定に依存します。examples は合成データのみを含みます。

gwr の HTTP 層（`/invoke`・`/ui-spec`）はgenai-web からの呼び出しを受ける統合用であり、
外部へ出る通信ノード（http 取得等）は持ちません。**源内の運用環境（信頼境界。内部
ネットワーク／VPC 等）内での利用を想定**しており、インターネットへ直接公開する構成は
想定していません（認証・チーム権限・endpoint・API キーはgenai-web 側が管理します）。
