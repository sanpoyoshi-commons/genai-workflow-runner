# 前提環境のセットアップ（Windows 11 / WSL2 ほか）

gwr は Python だけで動く軽量なツールです。**ローカル確認（[クイックスタート](../README.md#クイックスタートローカル確認)
＝フロー TOML のテスト＋単体テスト）に必要なのは [uv](https://docs.astral.sh/uv/) だけ**で、
Docker も常駐サービスも要りません。本書は、その土台（OS と uv）を **ゼロから**用意する手順です。

- Windows 11 → WSL2 → Ubuntu → 更新 / セキュリティ → **uv**
- gwr を ExApp として公開したり オンプレ版スタックで実機テストする場合だけ、追加で **Docker** が
  要ります（→ [Docker（任意）](#docker任意exapp-公開オンプレ版-テスト時)）。

動作環境は **Linux（WSL2 / ネイティブ）** が基本です。macOS でも uv は動きます。最も手軽なのは
**Windows 11 + WSL2 (Ubuntu) / x86_64** と **ネイティブ Linux / x86_64** です。

> **メモリ**：gwr 本体は軽量で、WSL2 の既定メモリ割り当てで十分です。embedding やローカル LLM を
> 常駐させる オンプレ版スタックを同居で動かす場合だけ、別途まとまった RAM（WSL2 は `.wslconfig` での
> 明示割当）が要ります。それはオンプレ配布物側の前提環境ドキュメントを参照してください。

---

## ルート A: Windows 11 + WSL2

### A-1. WSL2 と Ubuntu を入れる

**管理者権限の PowerShell**（または Windows Terminal）で：

```powershell
# WSL2 + 既定の Ubuntu を一括インストール（要再起動）
wsl --install
```

- 完了後に **PC を再起動**します。再起動後、Ubuntu のウィンドウが開き、**UNIX ユーザー名とパスワード**
  を求められるので設定します（このパスワードは `sudo` で使います。控えておく）。
- ディストリビューションを選びたい場合：

```powershell
wsl --list --online           # 選べる一覧
wsl --install -d Ubuntu-24.04 # 例：Ubuntu 24.04 を指定
```

- 既に古い WSL を使っている場合は最新化：`wsl --update`

### A-1b.（任意）使い捨ての試用インスタンスを作る

> **気軽に試せます**：このやり方で作るインスタンスは、いつでも `wsl --unregister` 一発で**跡形なく
> 消せます**（消えるのはそのインスタンスの仮想ディスクだけ。普段使いの Ubuntu や Windows には一切
> 影響しません）。「まっさらな環境でゼロから通す」検証に向いています。

Ubuntu 公式の WSL 用 rootfs を落として **別名のインスタンス**として取り込むのが最もクリーンです。
以下は **通常ユーザー権限の PowerShell** で実行します。`D:\wsl` は任意の作業フォルダです（C ドライブでも可）。

**1) フォルダ作成 + 公式 rootfs / SHA256SUMS の取得**（Ubuntu 24.04 = noble の例・約 340 MB）

```powershell
New-Item -ItemType Directory -Path D:\wsl\gwr-test  -Force
New-Item -ItemType Directory -Path D:\wsl\downloads -Force

$base = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current"
Invoke-WebRequest -Uri "$base/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz" -OutFile "D:\wsl\downloads\ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile "D:\wsl\downloads\SHA256SUMS"
```

**2) SHA256 で改ざん検査**（2 つのハッシュ文字列が一致することを確認）

```powershell
Get-FileHash -Algorithm SHA256 D:\wsl\downloads\ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz
Get-Content D:\wsl\downloads\SHA256SUMS | Select-String "wsl-amd64-wsl.rootfs.tar.gz"
```

→ 上（算出値）と下（正解値）のハッシュが一致すれば OK（大文字・小文字の違いは無視）。一致しなければ
取り込まず 1) からやり直してください。

**3) 別名インスタンスとして取り込んで入る**

```powershell
wsl --import gwr-test D:\wsl\gwr-test D:\wsl\downloads\ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz
wsl -d gwr-test
```

**4) 作業用ユーザーを作る**（取り込み直後の既定ユーザーは `root`）

```bash
adduser dev             # 対話：パスワード等を設定
usermod -aG sudo dev    # sudo を使えるように
```

毎回このユーザーで起動するには、`/etc/wsl.conf` に既定ユーザーを設定します（インポートした
インスタンスは既定ユーザーが `root` のため）：

```bash
sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[user]
default=dev
EOF
```

PowerShell で `wsl --shutdown` → `wsl -d gwr-test` で開き直すと、`dev` ユーザーで起動します。

**試用が終わったら丸ごと破棄**

```powershell
wsl --list --verbose         # 名前と状態を確認
wsl --unregister gwr-test    # このインスタンスを完全削除（取り消し不可）
```

> `--unregister` は **そのインスタンスのデータを完全に消す**操作です（取り消し不可）。名前を取り違え
> ないよう、必ず `wsl -l -v` で確認してから実行してください。普段使いの `Ubuntu` を消さないこと。

> **補足**：新しめの WSL（`--name` 対応）なら、ダウンロードの代わりに
> `wsl --install -d Ubuntu-24.04 --name gwr-test` でも別名インスタンスを作れます
> （`wsl --version` で対応を確認。使えなければ上の rootfs 方式を使う）。

### A-2. リポジトリは Linux 側に置く

clone 先は必ず **Linux ファイルシステム配下**（例：`~/work`）にします。`/mnt/c/...`（Windows 側）に
置くと、**動作が極端に遅く・ファイル権限の不整合**が起きます。

### A-3.（任意・推奨）WSL2 カーネルの脆弱性緩和：未使用モジュールの無効化

> **なぜ WSL2 固有か**：WSL2 のカーネルは **Microsoft 提供**で、Ubuntu の `apt upgrade` では
> パッチされません（カーネル更新は `wsl --update`）。修正カーネルが届くまでの間、**使っていない
> カーネル機能を無効化**しておくのが有効なハードニングです。下表のモジュール（userspace 暗号 API・
> IPsec ESP・AFS RPC）は gwr の動作では使わないため、無効化による副作用はありません。
> （ネイティブ Linux は `apt full-upgrade`＋再起動でカーネルごと更新されるため不要です。）

2026 年春、Ubuntu 24.04 系が影響を受ける Linux カーネルのローカル特権昇格（LPE）脆弱性が相次いで
公表されました（**確認時点：2026 年 5 月**）。いずれも Ubuntu Security Team が「該当モジュールの
無効化」を緩和策として案内しています。

| 通称 | CVE | 対象モジュール |
|---|---|---|
| Copy Fail | CVE-2026-31431 | `algif_aead`（AF_ALG userspace crypto） |
| Dirty Frag | CVE-2026-43284 / -43500 | `esp4` / `esp6`（IPsec ESP）/ `rxrpc`（AFS RPC） |
| Fragnesia | CVE-2026-46300 | `esp4` / `esp6` / `rxrpc`（Dirty Frag と同一緩和策でカバー） |

**Ubuntu の中**で、該当モジュールを `install <mod> /bin/false` で無効化します（blacklist だけでは
不十分なため、Ubuntu 公式と同じく `install ... /bin/false` を使います）：

```bash
# Copy Fail (CVE-2026-31431)
echo "install algif_aead /bin/false" | sudo tee /etc/modprobe.d/disable-algif.conf
sudo rmmod algif_aead 2>/dev/null || true

# Dirty Frag / Fragnesia (CVE-2026-43284 / -43500 / -46300)
sudo tee /etc/modprobe.d/dirty-frag.conf > /dev/null <<'EOF'
install esp4 /bin/false
install esp6 /bin/false
install rxrpc /bin/false
EOF
sudo rmmod esp4 esp6 rxrpc 2>/dev/null || true

# 確認（出力が空＝いずれも未ロードなら OK）
lsmod | grep -E 'algif_aead|esp4|esp6|rxrpc' || echo "対象モジュールは未ロード（OK）"
```

> **`update-initramfs` は不要**：WSL2 は initramfs を使わず、`modprobe` が `/etc/modprobe.d/*.conf`
> を直接参照するため、再起動後も無効化が維持されます。
> **恒久対策**：これらは暫定の緩和策です。`wsl --update` でカーネルを更新し、修正済みカーネルが
> 配布されたら緩和策の要否を見直してください。一次情報：Ubuntu Security Team
> （[Copy Fail](https://ubuntu.com/blog/copy-fail-vulnerability-fixes-available) /
> [Dirty Frag](https://ubuntu.com/blog/dirty-frag-linux-vulnerability-fixes-available) /
> [CVE-2026-46300](https://ubuntu.com/security/CVE-2026-46300)）。

---

## OS の最新化とセキュリティ（共通）

以降は **Ubuntu のターミナルの中**で行います（Windows の方は WSL2 の Ubuntu、ネイティブ Linux の方は
そのまま）。

### OS を最新化する

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt autoremove -y
```

### セキュリティの最低限

個人開発・**localhost 用途**の前提での最小限です（gwr は開発・実験用途・無保証。
[README の免責](../README.md) 参照）。

```bash
# 自動セキュリティ更新（任意・推奨）
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

- **強いパスワード**を UNIX ユーザーに設定し、不要なサービスは動かさない。
- WSL2 は Windows の NAT 配下にあり、LAN からの着信は既定で素通しされません（主な防御は Windows 側
  ファイアウォール）。ネイティブ Linux で LAN 公開する場合だけ `ufw` で必要ポートを絞ってください。

---

## uv を入れる

gwr のローカル確認に必要なのはこれだけです。

```bash
# uv をインストール（既にあれば不要）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 現在のシェルに PATH を通す（インストーラの案内に従う。多くは ~/.local/bin）
source $HOME/.local/bin/env 2>/dev/null || true

uv --version   # 入ったか確認
```

> Python 3.12+ は uv が自動で用意します（個別インストール不要）。

---

## git と clone

```bash
sudo apt install -y git
git --version
```

> clone するだけなら `git config` のユーザー設定は不要です。自分で変更を `git commit` する場合のみ
> `git config --global user.name` / `user.email` を設定してください。

リポジトリを取得します（**WSL2 は必ず Linux 側に**置く → [A-2](#a-2-リポジトリは-linux-側に置く)）：

```bash
mkdir -p ~/work && cd ~/work
git clone https://github.com/sanpoyoshi-commons/genai-workflow-runner.git genai-workflow-runner
cd genai-workflow-runner
```

---

## 動作確認（ローカル）

土台ができたら、[クイックスタート](../README.md#クイックスタートローカル確認) を実行します。

```bash
uv sync                                  # 依存を同期（Python 3.12+ も uv が用意）
uv run gwr validate examples/hello.toml  # フローの静的検証
uv run gwr run examples/hello.toml        # ローカル実行（外部に出ない）→ HelloWorld
uv run pytest                            # 単体テスト（外部 I/O は全て mock）
```

ここまで通れば、gwr のローカル確認環境は完成です。

---

## Docker（任意：ExApp 公開・オンプレ版 テスト時）

gwr を [ExApp として公開](deploy.md)（`gwr serve` を Docker 化）したり、[オンプレ版（genai-deploy-onpre）](https://github.com/sanpoyoshi-commons/genai-deploy-onpre)で実機テスト
する場合だけ Docker Engine が要ります。ローカル確認（上記）には不要です。

### Docker Engine を入れる（公式 apt リポジトリ）

```bash
# 1) 競合する古いパッケージを除去（入っていなければ無害）
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  sudo apt-get remove -y $pkg 2>/dev/null || true
done

# 2) Docker 公式 apt リポジトリを登録
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update

# 3) Docker Engine + Compose プラグインをインストール
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 4) sudo なしで docker を使えるように（任意・推奨／反映には再ログイン）
sudo usermod -aG docker $USER
```

### WSL2 特有：Docker を自動起動させる

WSL2 では Docker デーモンが自動で立ち上がりません。**systemd を有効化**します。`/etc/wsl.conf` に
`[boot] systemd=true` を足し（[A-1b](#a-1b任意使い捨ての試用インスタンスを作る) で `[user]` を設定
済みなら、それを消さないよう追記）：

```bash
sudo tee -a /etc/wsl.conf > /dev/null <<'EOF'

[boot]
systemd=true
EOF
```

PowerShell で `wsl --shutdown` → Ubuntu を開き直し：

```bash
sudo systemctl enable --now docker
```

> ネイティブ Linux は systemd が docker を自動起動します（必要なら `sudo systemctl enable --now docker`）。

### 動作確認

```bash
docker version              # Client/Server 両方が表示されれば OK
docker compose version      # Compose v2 プラグインの確認
docker run --rm hello-world # 実際にコンテナが動くか
```

- `permission denied`（docker.sock）→ `usermod -aG docker $USER` 後の再ログインがまだ。WSL2 は
  `wsl --shutdown` 後に開き直す。
- `Cannot connect to the Docker daemon` → デーモン未起動。WSL2 は上の systemd 有効化、ネイティブ
  Linux は `sudo systemctl start docker`。

ここまで揃えば、[デプロイと ExApp 登録](deploy.md) の手順に進めます。
