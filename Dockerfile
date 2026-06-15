# gwr をgenai-web の ExApp として登録し WebUI から動作確認するためのコンテナ。
# 委譲先（LLM/RAG/CI）は持ち込まず、Excel/CSV 加工は Fake LLM で同期実走する。
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# serve（fastapi/uvicorn）＋ http（httpx＝委譲先 LLM/RAG/CI 接続）込み。console script `gwr` が入る。
RUN pip install --no-cache-dir ".[serve,http]"

# 非root実行（コンテナエスケープ時の被害低減）。ベースイメージの既存ユーザ（uid<1000）と衝突しない
# 高位の固定 uid/gid を使う（決定的・再現可能）。コンテナ自身の /etc/passwd で解決するためホストとは独立。
RUN groupadd -g 10001 appuser \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 検証するフロー（既定は同梱の PoC）。上書きは GWR_FLOW で。
ENV GWR_FLOW=/app/examples/poc.toml
# 認証キー（源内 ExApp 登録時の apiKey と一致させる）。未設定なら認証なし。
# ENV GWR_API_KEY=...

# /healthz（認証不要）への到達性で死活監視。curl 非依存（slim に curl 無し）＝stdlib のみで判定。
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)" || exit 1

# 0.0.0.0 で待受（コンテナ外＝同一 docker network の worker から到達可能に）。
CMD ["sh", "-c", "gwr serve \"$GWR_FLOW\" --host 0.0.0.0 --port 8000"]
