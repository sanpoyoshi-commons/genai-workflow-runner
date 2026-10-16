# Changelog

このファイルは利用者から見た変更点をまとめたものです。各リリースの詳細は
GitHub Releases を参照してください。

## 0.3.1 — 2026-10-16

### Security

- `mcp` extra が引く pyjwt を 2.15.0 に上げた（CVE-2026-101918・CVE-2026-102275）。
  `mcp` extra を入れていない環境には関係しない

### Fixed

- パッケージに `py.typed` を同梱した。gwr を依存に使うプロジェクトの型検査で、gwr の
  型情報が使えるようになった

## 0.3.0 — 2026-09-28

### Added

- `judge` ノード: フロー中のテキストについて型付きの問い（`choice` / `noul` / `score`）を
  立て、答えと確信度を判定 API から受け取る（ノードは 8 種になった）。gwr 自身は推論せず、
  判定は委譲先が行う。問いは step に静的に書き、式解決されるのは `in.state` だけ
- `gwr validate --json` の `warnings[]`: 直さなくても実行できる書き方の指摘。`ok` は
  error だけで決まるため、既存の応答の意味は変わらない（`schema_version` は `1` のまま）
- 委譲先への接続に `GWR_JUDGE_ENDPOINT` / `GWR_JUDGE_API_KEY` / `GWR_JUDGE_TIMEOUT`
  （既定 660 秒）/ `GWR_JUDGE_CONNECT_TIMEOUT`（既定 10 秒）
- `examples/judge-smoke.toml`: 判定の答えと確信度で分岐するフロー。分岐ごとに別の出力キーへ
  書くので、どちらへ倒れたかが出力から読める
- `examples/judge-structured.toml`: 同じ問いを `criteria` と `instructions` を表・表の配列で
  書いた版（委譲先が構造化に対応した版でないと実行時に断られる）

`judge` の `criteria` と `instructions` には表・配列も書けます（値は変形せずに委譲先へ
渡します）。ただし委譲先が構造化に対応した版でないと `DELEGATE_FAILED` になり、また
判定の精度にどう効くかは `gemma4:e2b` では測っていません。詳細は
[ノードリファレンス](docs/nodes.md#judge) を参照してください。

### Changed

- 委譲先が `504` を返したとき、`DELEGATE_FAILED` ではなく `DELEGATE_TIMEOUT`
  （「時間をおいて再実行してください」）を返すようになった。`llm` / `retrieval` /
  `code_interpreter` にも同じように効く
- ドキュメント: `criteria` の既定を問いの型ごとに示した。`choice` は表（ラベルが分岐の
  条件式に出る）、`score` は説明を含めた文字列の配列（表にすると答えの `legend` に
  委譲先が整形した説明が入り、出力が読みにくくなる）

### Security

- `.pre-commit-config.yaml` の trivy を、`latest` ではなく版とダイジェストで固定した
  （`aquasec/trivy:0.74.0@sha256:62b1e65e…`）。`latest` は同じ名前で中身が差し替わるため、
  スキャナ自身の供給網リスクを避ける

---

## 0.2.0 — 2026-09-21

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
