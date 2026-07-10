# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

Dashboard financeiro da Scua. Dois artefatos independentes no mesmo repositório:

1. **ETL** (`contaazul_bi/`) — extrai dados da API do Conta Azul, transforma num star schema e grava no Supabase. Roda via GitHub Actions agendado.
2. **Dashboard** (`dashboard.py`) — app Streamlit de arquivo único (~3000 linhas) que lê o Supabase e renderiza as visualizações. Roda em container Docker no Railway.

Os dois processos **não se comunicam diretamente** — o Supabase (PostgreSQL) é o único ponto de integração. O ETL escreve, o dashboard lê. O código é em português (comentários, logs, nomes de tabelas/colunas).

## Comandos

```bash
# Ambiente
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r requirements.txt

# Dashboard local (http://localhost:8501)
streamlit run dashboard.py

# ETL — pipeline completo (extrai → transforma → grava no Supabase se DATABASE_URL setada)
python -m contaazul_bi.main run

# OAuth — autorização inicial (one-time, salva tokens no Supabase)
python -m contaazul_bi.main authorize
python -m contaazul_bi.main authorize --redirected-url "https://scua.com.br/?code=...&state=..."

# OAuth — valida credenciais e força refresh (mesmo check que o GitHub Actions roda antes do ETL)
python -m contaazul_bi.main oauth-status --force-refresh

# Gerar hash bcrypt para um novo usuário do dashboard
python -c "import bcrypt; print(bcrypt.hashpw(b'senha', bcrypt.gensalt()).decode())"
```

Não há suíte de testes nem linter configurados no repositório.

## Arquitetura do ETL

`ContaAzulETLPipeline.run()` em [contaazul_bi/main.py](contaazul_bi/main.py) é o orquestrador. Fluxo:

- **Extractors** (`contaazul_bi/extractors/`) chamam a API e retornam DataFrames "raw". Cada extractor recebe um `ContaAzulClient` compartilhado.
- **`ContaAzulClient`** ([client.py](contaazul_bi/client.py)) — HTTP com paginação e retry; pede o access token ao `ContaAzulOAuthManager` a cada request.
- **`build_analytics()`** ([transformers/analytics.py](contaazul_bi/transformers/analytics.py)) recebe o dict de frames raw e monta o star schema (`dim_*` / `fato_*`). Toda a lógica de negócio (fluxo de caixa realizado vs. compromissado, DRE, recorrência) vive aqui.
- **`write_analytics_to_supabase()`** ([supabase_writer.py](contaazul_bi/supabase_writer.py)) grava com `if_exists="replace"` — **cada run substitui as tabelas inteiras** no schema `bi_analytics`. Só as tabelas do allowlist `ANALYTICS_TABLES` são escritas; qualquer nova tabela do star schema precisa ser adicionada lá também.

Pontos de atenção ao editar o ETL:
- **`PAID_STATUSES` está duplicado** em `main.py` e `analytics.py` (com conjuntos ligeiramente diferentes — `main.py` inclui `"ACQUITTED"`). Mantenha em sincronia se alterar.
- Escrita no Supabase só acontece se `DATABASE_URL` estiver no ambiente; sem ela, o ETL só grava Parquet/JSON em `output/`.
- Extractors são ligados/desligados por flags `CONTA_AZUL_ENABLE_*` (ver [config.py](contaazul_bi/config.py)). Os defaults do código diferem dos valores usados em produção — a fonte de verdade para produção é o bloco `env:` em [.github/workflows/etl_pipeline.yml](.github/workflows/etl_pipeline.yml).

## Arquitetura do dashboard

[dashboard.py](dashboard.py) é um único arquivo Streamlit. Estrutura por convenção de nomes:

- `main()` orquestra: tema → CSS → auth → `load_data()` → sidebar (navegação + filtros) → `pagina_*()`.
- **Páginas**: `pagina_resumo`, `pagina_cenarios`, `pagina_receita`, `pagina_dre`. Cada uma recebe o dict `data` já filtrado por ano/mês/centro/categoria.
- **`calc_*()`** = lógica de cálculo (retornam números/dicts); **`fig_*()`** = constroem figuras Plotly; **`_filtrar_*()`** = filtros de DataFrame. Mantenha essa separação.
- **`load_data()`** (cache 1h) lê do Supabase via `_get_db_engine()` (`@st.cache_resource`). **Fallback**: se não há `DATABASE_URL`, lê Parquet de `output/analytics/`. O dict `data` usa chaves curtas (`cr`, `cp`, `realizado`, `saldos`, `vendas`, `contratos`, `centros`, `categorias`) que **não** batem com os nomes das tabelas — o mapeamento está em `_load_from_database` / `load_data`.

### Autenticação e sessão

- Credenciais ficam em `st.secrets` (`.streamlit/secrets.toml` local, ou base64 na env `STREAMLIT_SECRETS` em produção — decodificada por [entrypoint.sh](entrypoint.sh)).
- Login bcrypt em `_run_auth()`. Bloqueio após 5 tentativas (5 min), sessão expira em 8h.
- **"Lembrar de mim"**: token persistente por navegador na tabela `bi_remember_tokens` do Supabase (selector + validador com hash). `_ensure_auth_storage()` cria a tabela sob demanda.
- Painel ⚙ (`_render_settings_panel`) e disparo manual de ETL (`_render_etl_trigger`) só aparecem para usuários com `admin = true`. O disparo usa a GitHub Actions API via PAT em `st.secrets["github_actions"]`.

## Persistência (Supabase)

- **Schema `bi_analytics`** — star schema, recriado a cada ETL. Não precisa de migration.
- **Schema `public`** — tabelas de controle com RLS (só `service_role`): `bi_cenarios` (cenários de projeção em JSONB, linha única id=1, editada pelo dashboard), `bi_oauth_tokens` (tokens OAuth, linha única), `bi_remember_tokens`. Criadas por [migrations/001_init_supabase.sql](migrations/001_init_supabase.sql) (rodar uma vez).
- **Token store OAuth** ([oauth.py](contaazul_bi/oauth.py)): usa `SupabaseTokenStore` (tabela `bi_oauth_tokens`) quando `DATABASE_URL` existe, senão cai para arquivo local `TokenStore`. Ambos os processos (ETL e o comando `authorize`) leem/escrevem o mesmo lugar.

## Deploy e agendamento

- **Railway**: redeploy automático a cada push em `main`. Imagem Docker roda só o dashboard (ver [Dockerfile](Dockerfile)); o ETL **não** roda no Railway.
- **GitHub Actions**: [etl_pipeline.yml](.github/workflows/etl_pipeline.yml) roda o ETL em `cron: '0 6 * * *'`. Antes do `run`, executa `oauth-status --force-refresh` para falhar cedo se o OAuth estiver quebrado. `DATABASE_URL` e credenciais Conta Azul vêm de GitHub Secrets.

Detalhes operacionais completos (variáveis de ambiente, troubleshooting de OAuth, reautorização, transferência de ownership) estão no [README.md](README.md).
