# 式（参照と条件）

gwr の式には 2 種類あります。用途と文法・制限がそれぞれ違います。

| 種類 | 使う場所 | エンジン | 返すもの |
|---|---|---|---|
| **参照** | `in = { ... }` の値、`out`、`collect`、`foreach`、`[[outputs]]` の `from` | JMESPath | 任意の生値 |
| **条件** | `branch` の `when` | CEL | bool |

どちらもルート前置 `$.` が **Envelope のルート参照**を表します（`$.tables.raw` = スロット
`tables.raw`）。`$` 単体は Envelope 全体を指します。

---

## 参照（JMESPath）

`$.` で始まる文字列はすべて参照として解決されます（`$.` で始まらない値はリテラル）。
内部では [JMESPath](https://jmespath.org/) で評価されるので、ネスト・配列添字・スライス・
射影などが使えます。

```toml
in = { table = "$.tables.summary" }            # スロットそのもの
in = { context = "$.vars.docs[0].content" }    # 配列の 0 番目の content
when = "len($.tables.summary.data) == 0"       # 表の data 配列の長さ
from = "$.files.report"                         # FileRef を出力へ
```

### よく使う形

| 式 | 意味 |
|---|---|
| `$.vars.msg` | スロット `vars.msg` の値。 |
| `$.tables.raw.data` | `TableRef` の行データ配列。 |
| `$.vars.docs[0]` | リスト `vars.docs` の先頭要素。 |
| `$.vars.docs[0].content` | 先頭 Doc の `content`。 |
| `$.vars.items[*].name` | 各要素の `name` を集めた配列（射影）。 |
| `$` | Envelope 全体（通常は使わない）。 |

- 参照先が存在しなければ `null` が返ります（JMESPath の仕様）。
- 構文として不正な参照は `validate` 時に `BAD_REFERENCE`、実行時に `ExprError`。
- コンパイル結果はキャッシュされるので、同じ参照式の繰り返しは速いです。

---

## 条件評価（CEL）

`branch` の `when` は [CEL（Common Expression Language）](https://github.com/google/cel-spec)
で評価され、**bool を返す**必要があります。CEL は登録済み関数しか呼べないサンドボックスなので、
Python の import / 任意属性アクセス / eval は構造的に不可能です。

```toml
when = "len($.vars.docs) == 0"                 # 空判定
when = "len($.tables.summary.data) == 0"       # 表が空か
when = "$.vars.score >= 80"                    # 数値比較
when = "$.vars.flag && $.vars.count > 0"       # 論理結合
when = "$.vars.name.startsWith(\"A\")"         # 前方一致
```

### 使える演算子

比較 `== != < <= > >=`、論理 `&& || !`、算術 `+ - * / %`、三項 `cond ? a : b`、
メンバ・添字アクセス、リスト/マップリテラルなど標準的な CEL 演算が使えます。

### 呼べる関数（許可リスト）

これ**以外の関数呼び出しは静的に拒否**されます（`禁止された関数呼び出し`）。

| 関数 | 用途 |
|---|---|
| `len(x)` / `size(x)` | 長さ・要素数。 |
| `int(x)` `double(x)` `string(x)` `bool(x)` | 型変換。 |
| `has(x)` | フィールド存在判定。 |
| `startsWith(s)` `endsWith(s)` `contains(s)` | 文字列の前方/後方/部分一致。 |

> **`matches`（正規表現）は使えません。** celpy が Python の `re` で評価するため ReDoS の
> 余地があり、安全に強制中断できないので許可リストから外しています。文字列判定は
> `startsWith` / `endsWith` / `contains` で代替してください。

### 制限（fail-closed）

| 制限 | 値 | 超過時 |
|---|---|---|
| 式の長さ | 500 文字 | `式が長すぎる` |
| 括弧ネスト深さ | 32 | `ネストが深すぎる` |
| 評価タイムアウト | 2.0 秒（ソフト） | `式評価タイムアウト` |
| 未許可関数 | — | `禁止された関数呼び出し` |
| bool 以外を返す | — | `条件式が bool を返さない` |

これらはいずれも実行を止める（`ExprError` → `BRANCH_EVAL_FAILED`）方向に倒れます。
`when` は `validate` 時にコンパイル検証され、コンパイルできなければ
`BRANCH_WHEN_UNCOMPILABLE` として実行前に検出されます。

---

## 参照と条件の `$.` の違い

- **参照（JMESPath）** … `$.tables.raw` の `$.` は剥がされ、JMESPath パスとして解決。
- **条件（CEL）** … `$.tables.raw` の `$.` は剥がされ、CEL の識別子参照に変換。
  条件式が参照するトップレベルスロットだけを評価コンテキストに渡すので、巨大な表を
  まるごと CEL に流し込むことはありません（性能のための絞り込み）。

どちらも書き方は同じ `$.スロット.パス` で、エンジンが用途に応じて使い分けます。
