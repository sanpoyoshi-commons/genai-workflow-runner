# 委譲先（LLM / RAG / CI / 判定）への接続と環境変数

`llm` / `retrieval` / `code_interpreter` / `judge` ノードは、推論・検索・コード実行・判定を
**外部の API へ委譲**します。gwr 自身はサンドボックスも LLM も持ちません。このうち
RAG と CI の**リクエスト/レスポンス契約は同一**（envelope `{inputs:{…}} → {outputs, artifacts?}`）で、
**違うのは認証だけ**です。LLM は OpenAI 互換、`judge` は専用の JSON 形で、どちらも封筒を使いません。

> ここでいう envelope は**源内OSS の Web プロトコルのリクエスト/レスポンスの包み**で、gwr 内部の
> `Envelope`（ステップ間でデータを渡すスロットの入れ物 →
> [Envelope とスロット](flow-toml.md#envelope-とスロット)）とは別物です。

| 環境 | 委譲先ルート | 認証 |
|---|---|---|
| **本番（クラウド版）** | 登録済み ExApp の公開 HTTPS | `x-api-key`（`GWR_*_API_KEY`） |
| **[オンプレ版（genai-deploy-onpre）](https://github.com/sanpoyoshi-commons/genai-deploy-onpre)** | 中央 `api` に集約 | Keycloak Bearer（`GWR_KC_*` で password grant） |

`run` / `serve` は、対応する `GWR_*_ENDPOINT` が設定されたときだけ実委譲アダプタを
配線します。未設定なら LLM は Fake、RAG/CI/judge ノードはアダプタ未注入のままです
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

## judge（`GWR_JUDGE_*`）

`GWR_JUDGE_ENDPOINT` を設定すると `judge` ノードが実委譲になります。**委譲先は
genai-ai-api-onpre の judge ルートにしかありません**（ガバメントAI 源内 OSS 本体には
対応するルートがありません）。未設定なら実行時に `ADAPTER_MISSING` で止まります。

| 環境変数 | 説明 |
|---|---|
| `GWR_JUDGE_ENDPOINT` | 判定端点。オンプレ版の例 `https://api:3443/api/judge`。未設定なら委譲しない。 |
| `GWR_JUDGE_API_KEY` | 本番＝委譲先の `x-api-key`。オンプレ版は `GWR_KC_*` の Bearer を使うので不要。 |
| `GWR_JUDGE_TIMEOUT` | 応答を待つ秒数（既定 **660**）。 |
| `GWR_JUDGE_CONNECT_TIMEOUT` | 接続を待つ秒数（既定 **10**）。 |
| `GWR_VERIFY_TLS` / `GWR_ALLOW_INSECURE` / `GWR_USER_ID` | RAG / CI と共通。 |

認証は RAG / CI と同じ経路です（`GWR_KC_USER` があれば Keycloak Bearer、無ければ `x-api-key`）。

### 待ち時間を 660 秒にしている理由

**委譲先は自分の予算の中で既に再試行しています。** 既定では 1 回の試行が 300 秒、
リクエスト全体が 600 秒で、予算を使い切ると 504 を返します。gwr 側の待ちがこれより短いと、
**委譲先が答えを返す前に gwr が切ってしまいます**（それまでの推論が丸ごと無駄になります）。
そのため既定を 600 秒より長い 660 秒にしてあります。委譲先の予算を変えたときは、
**必ずこちらも上回るように**合わせてください。

**gwr 側では再試行しません。** 再試行を両側に置くと待ち時間が掛け算になるためで、
再試行は委譲先の 1 層だけに置いています。接続の待ちだけは 10 秒に分けてあります
（接続できない相手を 11 分待つ意味はないため）。

判定モデルが一定時間使われないと落ちるため、**読み込みからの初回は 2 分以上かかることが
あります**（手元の実測で単一試行の最悪は約 153 秒）。問いは逐次に処理されるので、
問いを増やすほど予算を使い切りやすくなります。

> [!warning] 源内OSS の画面から ExApp として呼ぶ場合
> その経路では gwr を呼ぶ側（worker）の待ち時間（`EXAPP_HTTP_TIMEOUT_MS`、既定 30 秒）が
> 先に効きます。**そこに収まらないフローは、gwr や委譲先の設定に関係なく切れます**
> （`judge` に限らず、時間のかかる `llm` / `retrieval` を含むフローも同じです）。
> `gwr run` や `gwr serve` の `/invoke` を直接叩く場合は、この制約を受けません。

---

## RAG の「該当なし」判定

源内OSS の RAG は該当が無くても、**空ではなく「該当なし」の文章を 200 で返します**
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
（源内OSS の law-rag の no-result 文 3 種をコメント付きで同梱、既定は無効）。

> 源内OSS 固有の文言は gwr 本体に焼かず、運用設定で注入する方針です。
> マーカー未設定時は常に「該当あり」側に流れ、本文中に「該当なし」文がそのまま出ます。

---

## 環境別の最小セット早見表

**本番（クラウド版）** — `x-api-key` のみ：

```bash
GWR_LLM_ENDPOINT=https://.../llm   GWR_LLM_API_KEY=...
GWR_RAG_ENDPOINT=https://.../rag   GWR_RAG_API_KEY=...
GWR_CI_ENDPOINT=https://.../ci     GWR_CI_API_KEY=...
# judge の委譲先は onpre の api にしかないため、本番（クラウド版）では設定できません。
```

**オンプレ版** — LLM は ollama 直、RAG/CI は api + Keycloak：

```bash
GWR_LLM_ENDPOINT=http://ollama:11434/v1/chat/completions
GWR_LLM_MODEL=<pull済モデル>  GWR_LLM_ALLOW_INSECURE=true

GWR_RAG_ENDPOINT=https://api:3443/api/law-rag/query
GWR_CI_ENDPOINT=https://api:3443/api/code-interpreter/responses
GWR_JUDGE_ENDPOINT=https://api:3443/api/judge
GWR_KC_TOKEN_URL=http://keycloak:8080/realms/genai-realm/protocol/openid-connect/token
GWR_KC_USER=<user>  GWR_KC_PASS=<pass>
GWR_VERIFY_TLS=false  GWR_ALLOW_INSECURE=true
```

デプロイ全体の手順は [デプロイと ExApp 登録](deploy.md) を参照。
