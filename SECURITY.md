# セキュリティポリシー

## 対応バージョン

| バージョン | 対応 |
|---|---|
| 0.1.x | :white_check_mark: |

最新の `main` を基準にメンテナンスしています。古いバージョンへの個別バックポートは
基本的に行いません。

## 脆弱性の報告

**脆弱性は公開 Issue に書かず、非公開で報告してください。**

GitHub の **Private Vulnerability Reporting** を使ってください。

1. 本リポジトリの **Security** タブを開く
2. **Report a vulnerability** から報告フォームを送信する

再現手順・影響範囲・該当箇所（ファイル/行）を添えていただけると助かります。
個人メンテナンスのため返信はベストエフォートですが、受領後できる限り早く対応します。

> リポジトリ設定で Private Vulnerability Reporting が無効だとボタンは表示されません。
> Settings → Code security and analysis → Private vulnerability reporting を有効化してください。

## セキュリティ対策の状況

本リポジトリは以下を適用済みです（設定は `.pre-commit-config.yaml`、詳細はコミット履歴を参照）。

- **静的スキャン**：Gitleaks（秘密情報、`pre-commit`）／Semgrep（SAST）・OSV-Scanner
  （依存脆弱性）・Checkov（IaC）・Trivy（FS）を `pre-push` で実行（uvx / docker 導入時。
  未導入の環境では自動でスキップ）、および OWASP Top 10:2025 / Agentic Applications 2026
  観点のレビュー。
- **ハードニング**：コンテナの非 root 実行、外部 HTTP・自前 LLM/サンドボックスを持たない
  設計（委譲のみ）、入力上限（行数・xlsx 解凍後サイズ・foreach 反復数）、
  式評価のサンドボックス化（CEL の許可関数限定・長さ/深さ/タイムアウト制限）、
  表出力の数式インジェクション無害化。
