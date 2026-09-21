# AGENTS.md

このリポジトリ（gwr）で作業する AI エージェント向けの手順書です。

## セットアップ

前提は **uv** のみ（Python 3.12+ は uv が用意します）。

`uv sync` は**宣言的**です。`--extra` に挙げたものだけが入り、挙げなかった extra は
**外れます**。足し算ではないので、必要な extra は毎回まとめて指定してください。

```bash
# 迷ったらこれ（テスト全体が回り、実 HTTP 接続も使える）
uv sync --extra serve --extra mcp --extra http

uv sync                                  # 既定の依存だけ（extra は全て外れる）
uv sync --extra serve --extra mcp        # テスト全体を回すのに必要な最小
```

`--extra serve` を入れないと `tests/test_serve.py` の収集が失敗します。`--extra mcp` が
無いと `tests/test_mcp_server.py` / `tests/test_mcp_stdio.py` は skip されます。テスト全体を
回すときは両方を入れて同期してください。

## テストと静的検査

```bash
uv run pytest                 # 外部 I/O は全て mock・ローカル完結
uv run pytest --cov           # カバレッジ付き
uv run ruff check .           # lint
uv run mypy                   # 型
```

`tests/real/` は接続環境変数が無ければ skip されます（`-m real` でマーク済み）。

## フロー TOML を書いたら必ず検証する

```bash
uv run gwr validate path/to/flow.toml --json
```

- **`"ok": true` が返るまで直してください。** 終了コードは 0（問題なし）/ 1（あり）。
- 指摘の `reason` と直し方の対応表は [docs/cli.md の理由コード一覧](docs/cli.md#理由コード一覧)。
- `validate` は外部に接続しません。何度実行しても安全です。

フロー TOML の書き方は **[docs/flow-toml-for-ai.md](docs/flow-toml-for-ai.md)**（生成タスク
特化の凝縮版・1 ファイルで完結）にまとめてあります。詳細が要るときは
[docs/flow-toml.md](docs/flow-toml.md) / [docs/nodes.md](docs/nodes.md) /
[docs/expressions.md](docs/expressions.md) を参照してください。

## MCP 経由で使う場合

`uv run gwr mcp` で MCP サーバ（stdio）が起動し、以下のツールが使えます。検証の往復を
手で回さずに済みます。

| ツール | 使いどころ |
|---|---|
| `gwr_spec` | フロー TOML 仕様 1 枚を読む（書く前に必ず） |
| `gwr_validate` | 書いた TOML を検証する。**`ok` が true になるまで直す** |
| `gwr_dry_run` | Fake 委譲でローカル実走して挙動を見る |
| `gwr_ui_spec` | `[[inputs]]` からガバメントAI 源内 OSS のリクエスト形式 JSON を生成する |

`examples/*.toml` は Resources（`gwr://examples/<ファイル名>`）として引けます。近い例を
引いてから書くと初手が安定します。

ツールは**ファイルパスを受け取りません**。フロー TOML は本文の文字列で渡してください
（パスを渡すとスキーマ検証で弾かれます）。
`gwr_dry_run` の委譲先は Fake 固定で、環境変数が設定済みでも実エンドポイントには
接続しません。詳細は [docs/cli.md の mcp 節](docs/cli.md#mcp)。

下の「やってはいけないこと」は MCP 経由でも同じく適用されます。

## やってはいけないこと

- **実エンドポイントに接続しない。** `GWR_LLM_ENDPOINT` / `GWR_RAG_ENDPOINT` /
  `GWR_CI_ENDPOINT` / `GWR_KC_*` を設定しない。これらが未設定なら `run` / `serve` の委譲は
  Fake アダプタになり、ネットワークに出ません。`smoke-llm` / `smoke-rag` / `smoke-ci` は
  実叩き専用のサブコマンドなので、人間が明示的に指示したときだけ使ってください。
- **外部 I/O（LLM・検索・コード実行）を実呼び出しするテストを書かない。** 必ず mock または
  `gwr.adapters.fakes` を使います。
- **外部アクセスノード（http 取得・web 取得）を追加しない。** gwr は設計上、委譲先以外への
  通信経路を持ちません。
- **秘密情報・行政データをコミットしない。** `examples/` は合成データのみです。
- **GPL / AGPL の依存を追加しない。** 他 OSS のコードを流用しない（Apache-2.0 の完全
  オリジナル実装を維持します）。
- **依存の追加は慎重に。** 追加するときは `pyproject.toml` の optional extra に入れ、既定の
  `uv sync` で入る依存を増やさないでください。

## コミット前

`pre-commit install` 済みなら、commit 時に gitleaks、push 時に semgrep / osv-scanner /
checkov / trivy が走ります（各ツール未導入の環境では自動スキップ）。手動で一括実行：

```bash
pre-commit run --hook-stage pre-push --all-files
```

## リポジトリの構成

| パス | 中身 |
|---|---|
| `src/gwr/cli.py` | `gwr` コマンド（`validate` / `spec` / `ui-spec` / `mcp` / `run` / `serve` / `smoke-*`） |
| `src/gwr/runner.py` | フロー実行と静的検証（`dry_run`）。`Issue` / `ValidationReport` |
| `src/gwr/validate.py` | TOML → 検証レポート。`--json` 用の `report_to_dict` |
| `src/gwr/mcp_server.py` | MCP サーバ（stdio）。ツール 4 本と Resources |
| `src/gwr/assets.py` | 同梱データ（仕様 1 枚・フロー例）の読み出し |
| `src/gwr/nodes/` | ローカルノード（`file.load` / `file.transform` / `file.write` / `flow.validate`） |
| `src/gwr/adapters/` | 委譲ノード（`llm` / `retrieval` / `code_interpreter`）と HTTP 層 |
| `src/gwr/contract.py` | `[[inputs]]` / `[[outputs]]` の契約と UI 仕様生成 |
| `src/gwr/expr.py` | `$.` 参照（JMESPath）と `when`（CEL） |
| `examples/` | フロー TOML の例（合成データのみ） |
| `docs/` | 利用者向けドキュメント |
