#!/bin/sh
# ─── Entrypoint do container — Dashboard BI Finance Scua ──────────────────────
set -e

# 1. Injeta .streamlit/secrets.toml a partir da variável STREAMLIT_SECRETS
#    (base64-encoded). Isso permite passar credenciais em Railway/Render/Fly.io
#    sem montar volumes ou commitar o arquivo de secrets.
#
#    Como gerar o valor para a env var da plataforma de hospedagem:
#       base64 -w 0 .streamlit/secrets.toml
#
if [ -n "$STREAMLIT_SECRETS" ]; then
    mkdir -p /app/.streamlit
    printf '%s' "$STREAMLIT_SECRETS" | base64 -d > /app/.streamlit/secrets.toml
    echo "INFO [entrypoint]: secrets.toml configurado a partir de STREAMLIT_SECRETS."
fi

# 2. Inicia o Streamlit escutando na porta definida pela plataforma (PORT)
#    ou na porta padrão 8501 caso não esteja definida.
exec streamlit run dashboard.py \
    --server.port="${PORT:-8501}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
