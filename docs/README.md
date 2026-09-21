# gwr ドキュメント

genai-workflow-runner（gwr）の利用者向けドキュメントです。TOML でフローを書き、
[ガバメントAI 源内 OSS](https://github.com/digital-go-jp/genai-web) の Web に
AI アプリ（ExApp）として登録するまでを体系的に説明します。

## 目次

0. [前提環境のセットアップ](prerequisites.md) — Windows 11 / WSL2 + Ubuntu ほか、土台
   （OS・uv、任意で Docker）をゼロから用意する手順。
1. [フロー TOML の書き方](flow-toml.md) — `version` / `[[inputs]]` / `[[steps]]` /
   `[[outputs]]` の全体像、制御フロー（`branch` / `foreach`）、エラー方針。
2. [ノードリファレンス](nodes.md) — `file.load` / `file.transform` / `file.write` /
   `flow.validate` / `llm` / `retrieval` / `code_interpreter` の入出力・パラメータ一覧。
3. [式（参照と条件）](expressions.md) — `$.` 参照（JMESPath）と `when` 条件（CEL）の
   文法・使える関数・制限。
   - [フロー TOML 仕様（生成タスク特化の凝縮版）](flow-toml-for-ai.md) — 上の 1〜3 を
     1 ファイルに凝縮したもの。AI にフロー TOML を書かせるときの入力に使います
     （正は 1〜3 の各ページ）。
4. [CLI リファレンス](cli.md) — `validate` / `spec` / `ui-spec` / `mcp` / `run` / `serve` /
   `smoke-llm` / `smoke-rag` / `smoke-ci`。
5. [委譲先（LLM / RAG / CI）への接続と環境変数](delegates.md) — 本番（クラウド版）/ オンプレ版 の
   認証差、`GWR_*` 環境変数の全リスト、RAG「該当なし」判定。
6. [デプロイと源内OSS の Web への ExApp 登録](deploy.md) — Docker ビルド、オンプレ版のネットワーク
   参加、ExApp 登録手順。

## フロー TOML を AI に書かせる（MCP）

手で書かずに済ませたい場合は `uv run gwr mcp` で MCP サーバ（stdio）を起動し、お使いの
MCP クライアントから接続します。仕様の取得・検証・実走を AI がツールとして呼べるので、
「仕様を貼る → 生成 → 保存 → `validate` を叩く」往復が不要になります。
→ [CLI リファレンスの `mcp`](cli.md#mcp)

## 5 分クイックスタート

ローカルでの動作確認（フロー TOML のテスト＋単体テスト）です。外部 I/O は Fake／mock で、
源内OSS には接続しません。環境の用意は [前提環境のセットアップ](prerequisites.md)、公開・登録は
[デプロイと ExApp 登録](deploy.md) を参照してください。

```bash
uv sync                                  # 依存を同期（Python 3.12+ も uv が用意）

# フローの静的検証（構文・データフロー・型を実行前にチェック）
uv run gwr validate examples/hello.toml

# ローカル実行（委譲は Fake、外部に一切出ない）→ HelloWorld
uv run gwr run examples/hello.toml

# 単体テスト（外部 I/O は全て mock・ローカル完結）
uv run pytest
```

## 同梱フロー例

| ファイル | 内容 |
|---|---|
| [`examples/hello.toml`](../examples/hello.toml) | 委譲なしの最小フロー。固定文字列「HelloWorld」を返すだけ（入力不要・経路疎通の動作確認用）。 |
| [`examples/poc.toml`](../examples/poc.toml) | `file.load → file.transform(集計) → llm(要約) → branch → file.write` の一通り。 |
| [`examples/llm-smoke.toml`](../examples/llm-smoke.toml) | 質問 → `llm` → 回答 の最小フロー。 |
| [`examples/rag-smoke.toml`](../examples/rag-smoke.toml) | `retrieval → branch(該当判定) → llm` の RAG フロー。 |
| [`examples/ci-smoke.toml`](../examples/ci-smoke.toml) | `code_interpreter` でファイル分析しグラフを生成。 |
| [`examples/toml-generator.toml`](../examples/toml-generator.toml) | フロー TOML 自体を作るフロー。`llm → flow.validate → branch → file.write(text)` で、検証を通った場合だけ `.toml` を artifacts で返す。 |
