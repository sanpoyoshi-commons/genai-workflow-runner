# デプロイとgenai-web への ExApp 登録

gwr は「ライブラリ核 ＋ `/invoke` を出す薄い HTTP 層」です。これを [genai-web](https://github.com/digital-go-jp/genai-web) に
**プロトコル準拠の AI アプリ（ExApp）**として登録すると、源内の WebUI から
フローを実行できます。認証・チーム権限・課金・endpoint / API キーの管理は
genai-web 側が持つため、gwr 側で用意するのは次の 3 点だけです。

- 公開した `/invoke` の **endpoint**
- それを守る **apiKey**（`GWR_API_KEY`）
- 入力フォームの元になる **リクエスト形式 JSON**（`gwr ui-spec` の出力）

おおまかな流れ：

1. フローを書いて [`gwr validate`](cli.md#validate) で検証。
2. [`gwr ui-spec`](cli.md#ui-spec) でリクエスト形式 JSON を生成。
3. gwr を `serve`（または Docker）で公開。
4. genai-web の ExApp 登録に endpoint / apiKey / リクエスト形式 JSON を入れる。

---

## ローカルで公開する（最小）

```bash
uv sync --extra serve
export GWR_API_KEY=secret123   # 下の curl と ExApp 登録の apiKey で同じ値を使う
uv run gwr serve examples/poc.toml --host 0.0.0.0 --port 8000
```

- `GWR_API_KEY` を設定すると `/invoke` が `x-api-key` を検証します（未設定なら認証なし）。
- 実 LLM/RAG/CI に繋ぐなら `GWR_*_ENDPOINT` 系を併せて設定（[委譲先への接続](delegates.md)）。

動作確認：

```bash
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/ui-spec
curl -s -X POST http://localhost:8000/invoke \
  -H "x-api-key: $GWR_API_KEY" -H 'content-type: application/json' \
  -d '{"inputs": {"question": "テスト"}}'
```

---

## Docker で公開する

リポジトリ直下の `Dockerfile` でイメージをビルドできます（非 root 実行）。

```bash
docker build -t gwr:latest .
docker run --rm -p 8000:8000 \
  -e GWR_API_KEY=secret123 \
  -e GWR_FLOW=/app/examples/poc.toml \
  gwr:latest
```

`GWR_FLOW` で動かすフローを切り替えます（既定 `/app/examples/poc.toml`）。
委譲を使うなら `GWR_*_ENDPOINT` と認証を環境変数で渡します（[委譲先への接続](delegates.md)）。

---

## genai-web に ExApp 登録する（本番＝クラウド版）

これが本番の載せ方です。公開した gwr を、genai-webの
ExApp として登録します。gwr 本体は源内と同居する必要はなく、**genai-web から到達できる
公開 HTTPS** のどこに置いても構いません。

### 1. 公開して保護する

- gwr を公開 HTTPS で配置する（任意のホスト/クラウド）。前段に TLS 終端（リバース
  プロキシ等）を置き、`/invoke` を HTTPS で公開します。
- `GWR_API_KEY` を**必ず**設定する。genai-web からの呼び出しはこの `x-api-key` で検証されます。

### 2. 委譲先（LLM/RAG/CI）の認証を設定する（使うフローのみ）

本番では委譲先は源内に登録済みの ExApp（公開 HTTPS）で、認証は **`x-api-key`** です。

```bash
GWR_LLM_ENDPOINT=https://.../llm   GWR_LLM_API_KEY=...
GWR_RAG_ENDPOINT=https://.../rag   GWR_RAG_API_KEY=...
GWR_CI_ENDPOINT=https://.../ci     GWR_CI_API_KEY=...
```

Keycloak は本番では使いません（`GWR_KC_*` 未設定 → `x-api-key` 経路）。全変数は
[委譲先への接続](delegates.md)。

### 3. リクエスト形式 JSON を生成する

```bash
uv run gwr ui-spec <flow>.toml
```

`[[inputs]]` から入力フォーム定義（リクエスト形式 JSON）が生成されます。

### 4. genai-web に登録する

genai-web のチーム管理 → ExApp 登録に次を入力します。

| 項目 | 値 |
|---|---|
| endpoint | 公開した gwr の `https://.../invoke`。 |
| apiKey | `GWR_API_KEY` に設定した値。 |
| リクエスト形式 JSON | `uv run gwr ui-spec <flow>.toml` の出力。 |

これでgenai-web の WebUI から、`[[inputs]]` 由来の入力フォームでフローを実行でき、
結果が `[[outputs]]`（テキスト＋ファイル）として表示されます。`[[inputs]]` に
ファイル型があれば、源内が `inputs.files[]` 形でアップロードを渡します。

> 認証・利用者管理・データ保持・課金はgenai-web の責務です。gwr は endpoint の公開と
> apiKey 検証だけを担い、源内のソースには一切依存しません（API 仕様準拠の独立実装）。

### チェックリスト（本番）

- [ ] `gwr validate <flow>` が OK。
- [ ] gwr を公開 HTTPS に配置し、`GWR_API_KEY` を設定した。
- [ ] 委譲を使うなら `GWR_*_ENDPOINT` と `x-api-key`（`GWR_*_API_KEY`）を設定。
- [ ] `gwr ui-spec <flow>` の JSON を ExApp 登録に貼り、apiKey を一致させた。
- [ ] `curl /healthz` と `/invoke` の疎通を確認した。

---

## （付録）オンプレ版で実機テストする

[**オンプレ版**（`genai-deploy-onpre`）](https://github.com/sanpoyoshi-commons/genai-deploy-onpre)を手元で動かし、gwr を ExApp として
繋いで実機確認する場合の手順です。**本番の載せ方は上の節**で、ここはローカル検証用です。
本番との違いは委譲先の認証だけ（オンプレ版は中央 `api` に集約＋Keycloak Bearer、本番は
`x-api-key`）。詳細は [委譲先への接続](delegates.md)。

オンプレ版の既存ネットワークに gwr を参加させ、worker から ExApp として呼べるようにします。
compose は [`deploy/gwr-onpre.compose.yml`](../deploy/gwr-onpre.compose.yml)。

### 前提：オンプレ版を queue プロファイルで起動

worker が ExApp を実呼び出しするため queue プロファイルが必要です。このオンプレ版は
secrets オーバーレイで初期化済みのため、**必ず 2 ファイル指定**で起動します。
ExApp の SSRF allowlist に `gwr` を足すのは**compose 起動時の環境変数上書き**で行い、
オンプレ版側のファイルは編集しません。

```bash
cd ../genai-deploy-onpre
EXAPP_ALLOW_PRIVATE_ENDPOINTS=true EXAPP_ENDPOINT_ALLOWLIST=echo-exapp,gwr \
  COMPOSE_PROFILES=queue \
  docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

ネットワーク名は `docker network ls | grep genai_internal` で確認できます
（compose プロジェクト名が `genai-local` でなければ `ONPRE_NETWORK` で上書き）。

### gwr を起動

```bash
cd genai-workflow-runner
GWR_API_KEY=<任意> docker compose -f deploy/gwr-onpre.compose.yml up -d --build
```

- コンテナ名は `gwr`。worker からは `http://gwr:8000` で到達します（allowlist の host と一致）。
- `GWR_FLOW`（既定 `/app/examples/poc.toml`）で動かすフローを切り替えます。
- 委譲先は compose の環境変数で渡します（オンプレ版は LLM=ollama 直、RAG/CI=api+Keycloak。
  全リストは [委譲先への接続](delegates.md)）。
- RAG「該当なし」マーカーを有効化するなら `GWR_RAG_NO_RESULT_MARKERS_FILE` を指定
  （既定の同梱ファイルは全行コメントアウト＝無効。有効化は `#` を外して再ビルドか別ファイルをマウント）。

### genai-web（オンプレ版）への登録

登録項目は本番と同じ（endpoint=`http://gwr:8000/invoke`、apiKey=`GWR_API_KEY`、
リクエスト形式 JSON=`gwr ui-spec` の出力）。オンプレ版固有の確認事項：

- [ ] オンプレ版を queue プロファイルで起動済み・allowlist に `gwr` を追加済み。
- [ ] gwr が `genai_internal` ネットワークに参加し、worker から `http://gwr:8000` に到達できる。
- [ ] 委譲を使うならオンプレ版認証（`GWR_KC_*`）を設定（[委譲先への接続](delegates.md)）。
