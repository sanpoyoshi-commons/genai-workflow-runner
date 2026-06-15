# 委譲先（LLM / RAG / CI）への接続と環境変数

`llm` / `retrieval` / `code_interpreter` ノードは、推論・検索・コード実行を**源内 API へ
委譲**します。gwr 自身はサンドボックスも LLM も持ちません。委譲先の
**リクエスト/レスポンス契約は 3 つとも同一**（封筒 `{inputs:{…}} → {outputs, artifacts?}`）で、
**違うのは認証だけ**です。

| 環境 | 委譲先ルート | 認証 |
|---|---|---|
| **本番（クラウド版）** | 登録済み ExApp の公開 HTTPS | `x-api-key`（`GWR_*_API_KEY`） |
| **[オンプレ版（genai-deploy-onpre）](https://github.com/sanpoyoshi-commons/genai-deploy-onpre)** | 中央 `api` に集約 | Keycloak Bearer（`GWR_KC_*` で password grant） |

`run` / `serve` は、対応する `GWR_*_ENDPOINT` が設定されたときだけ実委譲アダプタを
配線します。未設定なら LLM は Fake、RAG/CI ノードはアダプタ未注入のままです
（後方互換）。

---

## LLM（`GWR_LLM_*`）

`GWR_LLM_ENDPOINT` を設定すると、`run` / `serve` の LLM ステップが Fake ではなく
実 LLM に委譲されます。

| 環境変数 | 説明 |
|---|---|
| `GWR_LLM_ENDPOINT` | LLM 端点。未設定なら Fake。例 `http://ollama:11434/v1/chat/completions`。 |
| `GWR_LLM_MODEL` | モデル ID（既定 `local`）。`config_type` の `[default]` に入る。 |
| `GWR_LLM_SYSTEM_PROMPT` | system プロンプト。 |
| `GWR_LLM_MODE` | `chat`（既定）/ `responses`。 |
| `GWR_LLM_API_KEY` | 本番＝委譲先 ExApp の `x-api-key`。 |
| `GWR_LLM_ALLOW_INSECURE` | `true` で http / 内部宛を許可（オンプレ版 等）。 |
| `GWR_LLM_VERIFY_TLS` | `false` で自己署名 TLS の検証を無効化。 |
| `GWR_USER_ID` | `x-user-id`（監査用・任意）。 |

> オンプレ版の LLM は ollama を直叩きします（中央 `api` 経由ではない）。
> 例：`GWR_LLM_ENDPOINT=http://ollama:11434/v1/chat/completions`、
> `GWR_LLM_MODEL=<pull 済みモデル>`、`GWR_LLM_ALLOW_INSECURE=true`。

`--config` または `GWR_LLM_MODEL` / `GWR_LLM_SYSTEM_PROMPT` が
`config_type` 解決の `[default]` レイヤとして渡ります（[ノード → config_type](nodes.md#config_type-と設定の解決)）。

---

## RAG / CI（`GWR_RAG_*` / `GWR_CI_*`）

`GWR_RAG_ENDPOINT` / `GWR_CI_ENDPOINT` のいずれかを設定すると、RAG / CI アダプタが
env から配線されます（`run` / `serve` の WebUI E2E 用）。

| 環境変数 | 説明 |
|---|---|
| `GWR_RAG_ENDPOINT` | RAG 端点。オンプレ版の例 `https://api:3443/api/law-rag/query`。 |
| `GWR_CI_ENDPOINT` | CI 端点。オンプレ版の例 `https://api:3443/api/code-interpreter/responses`。 |
| `GWR_RAG_API_KEY` / `GWR_CI_API_KEY` | 本番＝委譲先 ExApp の `x-api-key`。 |
| `GWR_VERIFY_TLS` | `false` で自己署名 TLS の検証を無効化（オンプレ版）。 |
| `GWR_ALLOW_INSECURE` | `true` で http / 内部宛を許可（オンプレ版 等の信頼済み委譲先）。 |
| `GWR_TIMEOUT` | 委譲先タイムアウト秒（既定 600。law-rag/CI は CPU で数分かかり得る）。 |
| `GWR_USER_ID` | `x-user-id`（監査用・任意）。 |

### オンプレ版の認証（Keycloak Bearer）

オンプレ版では RAG/CI が中央 `api` に集約され、Keycloak Bearer ゲートが掛かります。
`GWR_KC_USER` が設定されていると、**invoke のたびに失効再取得する**トークンプロバイダが
配線され、`Authorization: Bearer` を載せます。

| 環境変数 | 説明 |
|---|---|
| `GWR_KC_TOKEN_URL` | Keycloak token 端点。例 `http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token`。 |
| `GWR_KC_CLIENT_ID` | password grant の client_id（空でも既定 `genai-web-dev` にフォールバック）。 |
| `GWR_KC_USER` | Keycloak ユーザー名（これがあるとオンプレ版 Bearer 経路になる）。 |
| `GWR_KC_PASS` | Keycloak パスワード。 |

> 常駐 `serve` では、起動時 1 回固定のトークンだと数分で `access_token` が失効し、時間が
> 経ってからの委譲呼び出しが 401 になります。そのため `serve` は invoke 毎にトークンを
> 取り直す方式です。本番（クラウド版）は委譲先 ExApp の `x-api-key` で叩くため Keycloak は使いません
> （`GWR_KC_*` 未設定 → `x-api-key` 経路）。

---

## RAG の「該当なし」判定

源内 RAG は該当が無くても、**空ではなく「該当なし」の文章を 200 で返します**
（GCP lawsy 由来の固定文をオンプレ版も忠実にポート／AWS は LLM 生成文で常に非空）。
したがって gwr の「空 = 該当なし」だけでは該当なし分岐を出せません。

そこで委譲先が返す「該当なし」文を**運用者がマーカーとして注入**します。`outputs` が
マーカーに一致すると `docs=[]` となり、フローを次の分岐で「該当なし」側へ落とせます。

```toml
[[steps]]
id = "gate"
type = "branch"
when = "len($.vars.docs) == 0"   # マーカー一致で docs=[] → 該当なし側へ
then = "none"
else = "hit"
```

| 環境変数 | 説明 |
|---|---|
| `GWR_RAG_NO_RESULT_MARKERS_FILE` | 「該当なし」文を 1 行 1 つ書いたファイル。`#` 始まり・空行は無視（＝既定でコメントアウトしておけば無効）。長い日本語向き。 |
| `GWR_RAG_NO_RESULT_MARKERS` | 改行区切りの簡易インライン指定。 |

優先順は **引数（`--no-result-marker*`） > 環境変数**。サンプルは
[`examples/rag-no-result-markers.example.txt`](../examples/rag-no-result-markers.example.txt)
（源内 law-rag の no-result 文 3 種をコメント付きで同梱、既定は無効）。

> 源内固有の文言は gwr 本体に焼かず、運用設定で注入する方針です。
> マーカー未設定時は常に「該当あり」側に流れ、本文中に「該当なし」文がそのまま出ます。

---

## 環境別の最小セット早見表

**本番（クラウド版）** — `x-api-key` のみ：

```bash
GWR_LLM_ENDPOINT=https://.../llm   GWR_LLM_API_KEY=...
GWR_RAG_ENDPOINT=https://.../rag   GWR_RAG_API_KEY=...
GWR_CI_ENDPOINT=https://.../ci     GWR_CI_API_KEY=...
```

**オンプレ版** — LLM は ollama 直、RAG/CI は api + Keycloak：

```bash
GWR_LLM_ENDPOINT=http://ollama:11434/v1/chat/completions
GWR_LLM_MODEL=<pull済モデル>  GWR_LLM_ALLOW_INSECURE=true

GWR_RAG_ENDPOINT=https://api:3443/api/law-rag/query
GWR_CI_ENDPOINT=https://api:3443/api/code-interpreter/responses
GWR_KC_TOKEN_URL=http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token
GWR_KC_USER=<user>  GWR_KC_PASS=<pass>
GWR_VERIFY_TLS=false  GWR_ALLOW_INSECURE=true
```

デプロイ全体の手順は [デプロイと ExApp 登録](deploy.md) を参照。
