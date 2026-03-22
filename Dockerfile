# ─── Dashboard BI Finance Scua ────────────────────────────────────────────────
# Imagem: apenas o dashboard Streamlit.
# O pipeline ETL roda separadamente via GitHub Actions (ver .github/workflows/).
#
# Build local:
#   docker build -t bi-scua .
#   docker run -p 8501:8501 --env-file .env -e STREAMLIT_SECRETS=$(base64 -w0 .streamlit/secrets.toml) bi-scua
#
# Deploy em Railway/Render: conecte este repositório e configure as variáveis
# de ambiente conforme .env.example + a variável STREAMLIT_SECRETS (base64).
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# curl é necessário para o HEALTHCHECK
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências primeiro — aproveita cache de layer quando só o código muda
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código do projeto (excluindo o que está em .dockerignore)
COPY . .

# Script de entrada: injeta secrets e inicia o Streamlit
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501

# Verifica se o Streamlit está respondendo na porta correta
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf "http://localhost:${PORT:-8501}/_stcore/health" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
