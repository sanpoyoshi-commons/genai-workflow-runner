# Changelog

このファイルは利用者から見た変更点をまとめたものです。各リリースの詳細は
GitHub Releases を参照してください。

## 2026-09-21

### Added

- `gwr mcp`: MCP サーバ（stdio）。対応クライアントから 4 つのツール（`gwr_spec` /
  `gwr_validate` / `gwr_dry_run` / `gwr_ui_spec`）でフロー TOML の検証・試走ができる
- `gwr validate --json`: 機械可読な検証結果（`{schema_version, ok, issues[]}`）
- `gwr spec`: AI 向けのフロー TOML 仕様 1 枚を出力する
- `flow.validate` ノード: フローの中でフロー TOML を検証する（ノードは 7 種になった）
- `file.write` の `format = "text"`: 文字列をそのままテキストファイルへ書き出す
- AI 向けドキュメント: `AGENTS.md` / `llms.txt` / `docs/flow-toml-for-ai.md`
- `examples/toml-generator.toml`: フロー TOML そのものを生成するフロー

### Changed

- `required = true` がキーの存在ではなく値が入っていることを見るようになった
  （空文字・空白のみは `REQUIRED_EMPTY` で拒否する）
- `error.reason` が理由コードだけを返すようになった（詳細は例外メッセージ側へ分けた）
- 上流プロジェクトの呼称と、本プロジェクトが独立・非公式である旨の記載を整理した
  （`DISCLAIMER.md` / README「本プロジェクトについて」）
- 委譲ノード（`llm` / `retrieval` / `code_interpreter`）に `on_error = "skip"` /
  `"default"` を指定したとき、委譲先の失敗もその方針に従うようになった。既定の `fail`
  （そこで止まる）は変更していないため、指定していないフローの挙動は変わらない

### Fixed

- 委譲先（LLM / RAG / Code Interpreter）への接続・認証・応答の失敗が、`/invoke` の
  500 ではなく通常のエラー応答として返るようになった。`error.reason` には
  `DELEGATE_ENDPOINT_INVALID`（接続先の設定）/ `DELEGATE_AUTH_FAILED`（認証）/
  `DELEGATE_TIMEOUT`（時間内に返らない・混雑）/ `DELEGATE_FAILED`（その他）のいずれかが
  入り、利用者には再実行すべきか管理者に連絡すべきかが分かる文言を返す。
  失敗の詳細（接続先・ホスト名・応答本文）は応答に含めず、サーバのログにのみ出力する
- 委譲先の接続先が不正なとき、`gwr run` / `gwr serve` がトレースバックではなく
  1 行のメッセージを出して終了するようになった（終了コード 2）

### Security

- `format = "text"` で表計算ソフトが開く拡張子（`.csv` / `.xlsx` など）を指定する
  組み合わせを、実行前の検証で拒否するようにした（`TEXT_FORMAT_SPREADSHEET_NAME`）
