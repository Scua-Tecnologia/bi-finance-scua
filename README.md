# BI Finance — Scua

Dashboard financeiro da Scua, alimentado pela API do Conta Azul e acessível via navegador com autenticação. Substitui o Power BI com uma solução 100% em nuvem, sem dependência de arquivos locais ou desktop.

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Arquitetura](#2-arquitetura)
3. [Estrutura do repositório](#3-estrutura-do-repositório)
4. [Configuração do ambiente local](#4-configuração-do-ambiente-local)
5. [Variáveis de ambiente](#5-variáveis-de-ambiente)
6. [Deploy em produção (Railway)](#6-deploy-em-produção-railway)
7. [ETL e agendamento (GitHub Actions)](#7-etl-e-agendamento-github-actions)
8. [Autenticação — usuários e senhas](#8-autenticação--usuários-e-senhas)
9. [Banco de dados (Supabase)](#9-banco-de-dados-supabase)
10. [Manutenção e troubleshooting](#10-manutenção-e-troubleshooting)
11. [Responsável e transferência de ownership](#11-responsável-e-transferência-de-ownership)

---

## 1. Visão geral

O **BI Finance** é um dashboard interativo que exibe os dados financeiros e comerciais da Scua em tempo real. Os dados são extraídos automaticamente do ERP **Conta Azul** todos os dias úteis e armazenados no **Supabase** (PostgreSQL). O dashboard é publicado no **Railway** e pode ser acessado por qualquer pessoa autorizada via navegador, sem instalar nada.

**O que o dashboard exibe:**

- Resumo de caixa (entradas, saídas, saldo, runway)
- Fluxo de caixa realizado e compromissado
- Receita recorrente (MRR/ARR)
- Cenários de projeção de caixa
- Análise de contratos e churn

---

## 2. Arquitetura

### Infraestrutura

| Componente | Tecnologia | Função |
|---|---|---|
| Código-fonte | GitHub (privado) | Versionamento e CI/CD |
| Banco de dados | Supabase (PostgreSQL) | Armazenamento de todas as tabelas |
| Dashboard | Railway (Docker + Streamlit) | Interface web acessível via URL pública |
| ETL | GitHub Actions (agendado) | Extração e carga automática dos dados |

### Fluxo de dados

```
┌─────────────────────────────────────┐
│           API Conta Azul            │
│  /categorias  /contas-a-receber     │
│  /contratos   /vendas   /baixas     │
└──────────────┬──────────────────────┘
               │ OAuth 2.0 + HTTP/JSON
               ▼
┌─────────────────────────────────────┐
│     GitHub Actions (ETL diário)     │
│  Extração → Transformação → Carga   │
└──────────────┬──────────────────────┘
               │ SQLAlchemy / PostgreSQL
               ▼
┌─────────────────────────────────────┐
│            Supabase                 │
│  schema bi_analytics  → fato_*/dim_ │
│  schema public        → bi_cenarios │
│                         bi_oauth_tokens │
└──────────────┬──────────────────────┘
               │ SQLAlchemy (DATABASE_URL)
               ▼
┌─────────────────────────────────────┐
│    Dashboard Streamlit (Railway)    │
│  Login com bcrypt → Visualizações   │
└─────────────────────────────────────┘
```

### Segurança

- Login com usuário e senha (hash bcrypt) — sem acesso sem autenticação
- Bloqueio automático após 5 tentativas incorretas (5 minutos)
- Sessão expira após 8 horas
- Tokens OAuth armazenados no Supabase — nenhum arquivo de credencial no servidor
- Tabelas de analytics isoladas no schema `bi_analytics` (não exposto via API REST do Supabase)

---

## 3. Estrutura do repositório

```
.
├── dashboard.py                  ← Aplicação Streamlit (interface do dashboard)
├── requirements.txt              ← Dependências Python
├── Dockerfile                    ← Imagem Docker para deploy no Railway
├── entrypoint.sh                 ← Script de inicialização do container
├── .env.example                  ← Template de variáveis de ambiente (sem valores reais)
│
├── contaazul_bi/                 ← Pacote Python do ETL
│   ├── config.py                 ← Configurações lidas das variáveis de ambiente
│   ├── oauth.py                  ← Gerenciamento de tokens OAuth (login e refresh)
│   ├── client.py                 ← Cliente HTTP com paginação e retry automático
│   ├── main.py                   ← Orquestrador do pipeline + CLI
│   ├── supabase_writer.py        ← Escrita das tabelas analytics no Supabase
│   ├── extractors/
│   │   ├── finance.py            ← Categorias, contas, transferências, baixas
│   │   ├── sales.py              ← Vendas
│   │   ├── contracts.py          ← Contratos
│   │   └── people.py             ← Pessoas (clientes/fornecedores)
│   └── transformers/
│       └── analytics.py          ← Transformações e montagem do star schema
│
├── migrations/
│   └── 001_init_supabase.sql     ← Script SQL para criação inicial das tabelas de controle
│
├── .streamlit/
│   ├── config.toml               ← Configurações do Streamlit (CORS, XSRF, etc.)
│   └── secrets.toml.example      ← Template de credenciais do dashboard
│
└── .github/
    └── workflows/
        └── etl_pipeline.yml      ← Workflow do GitHub Actions (ETL agendado)
```

---

## 4. Configuração do ambiente local

Para rodar o dashboard ou o ETL localmente (desenvolvimento/debug):

### 4.1 Pré-requisitos

- Python 3.12
- Acesso ao Supabase (DATABASE_URL)
- Credenciais OAuth do Conta Azul

### 4.2 Instalação

```bash
# Clone o repositório
git clone https://github.com/thiagocangussuc/bi-finance-scua.git
cd bi-finance-scua

# Crie e ative o ambiente virtual
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate

# Instale as dependências
pip install -r requirements.txt
```

### 4.3 Configure as variáveis de ambiente

```bash
# Copie o template
cp .env.example .env

# Edite o .env com os valores reais (nunca commite este arquivo)
```

### 4.4 Configure as credenciais do dashboard

```bash
# Copie o template de secrets
cp .streamlit/secrets.toml.example .streamlit/secrets.toml

# Edite com o usuário e hash de senha desejados
```

### 4.5 Rode o dashboard localmente

```bash
streamlit run dashboard.py
```

O dashboard abrirá em `http://localhost:8501`. Se `DATABASE_URL` estiver configurada no `.env`, os dados virão do Supabase. Caso contrário, o dashboard tentará carregar arquivos Parquet da pasta `output/analytics/` (fallback local).

### 4.6 Rode o ETL localmente

```bash
python -m contaazul_bi.main run
```

---

## 5. Variáveis de ambiente

### ETL e dashboard (arquivo `.env` local / secrets do Railway)

| Variável | Obrigatória | Descrição | Onde obter |
|---|---|---|---|
| `DATABASE_URL` | Sim | Connection string PostgreSQL do Supabase (Transaction Pooler, porta 6543) | Supabase → Project Settings → Database → Transaction pooler |
| `CONTA_AZUL_CLIENT_ID` | Sim | ID do app OAuth cadastrado no Conta Azul | Conta Azul → Configurações → Integrações |
| `CONTA_AZUL_CLIENT_SECRET` | Sim | Secret do app OAuth | Conta Azul → Configurações → Integrações |
| `CONTA_AZUL_REDIRECT_URI` | Sim | URI de redirecionamento cadastrada no app OAuth | Deve ser idêntica ao valor cadastrado no app |
| `CONTA_AZUL_ENABLE_SALES` | Não | Ativa extração de vendas (`true`/`false`) | — |
| `CONTA_AZUL_ENABLE_CONTRACTS` | Não | Ativa extração de contratos (`true`/`false`) | — |
| `CONTA_AZUL_ENABLE_ACQUITTANCES` | Não | Ativa extração de baixas (`true`/`false`) | — |
| `CONTA_AZUL_ENABLE_INSTALLMENT_ENRICHMENT` | Não | Necessário para extração de baixas | — |
| `CONTA_AZUL_LOOKBACK_YEARS` | Não | Anos de histórico a buscar (padrão: `1`) | — |
| `CONTA_AZUL_LOOKAHEAD_YEARS` | Não | Anos futuros a buscar (padrão: `1`) | — |
| `CONTA_AZUL_INSTALLMENT_LOOKBACK_MONTHS` | Não | Meses retroativos para baixas (padrão: `12`) | — |
| `CONTA_AZUL_TIMEOUT_SECONDS` | Não | Timeout HTTP em segundos (padrão: `120`) | — |
| `CONTA_AZUL_PAGE_SIZE` | Não | Registros por página na API (padrão: `100`) | — |
| `CONTA_AZUL_LOG_LEVEL` | Não | Nível de log: `DEBUG`, `INFO`, `WARNING` | — |

### Credenciais do dashboard (`.streamlit/secrets.toml` local / `STREAMLIT_SECRETS` no Railway)

```toml
[credentials.nome_do_usuario]
name          = "Nome Completo"
password_hash = "$2b$12$..."   # hash bcrypt da senha
```

---

## 6. Deploy em produção (Railway)

O dashboard roda como um container Docker no Railway. O Railway monitora o repositório GitHub e faz redeploy automaticamente a cada push na branch `main`.

### Variáveis de ambiente no Railway

Configure as seguintes variáveis no painel do Railway (Settings → Variables):

| Variável | Valor |
|---|---|
| `DATABASE_URL` | Connection string do Supabase (Transaction Pooler, porta 6543) |
| `CONTA_AZUL_CLIENT_ID` | ID do app OAuth |
| `CONTA_AZUL_CLIENT_SECRET` | Secret do app OAuth |
| `CONTA_AZUL_REDIRECT_URI` | URI cadastrada no app |
| `STREAMLIT_SECRETS` | Conteúdo do `secrets.toml` codificado em base64 |

### Como gerar o valor de `STREAMLIT_SECRETS`

```bash
# No terminal, na raiz do projeto (com o secrets.toml preenchido):
base64 -w0 .streamlit/secrets.toml
```

Cole o resultado (string longa sem quebras de linha) na variável `STREAMLIT_SECRETS` no Railway.

### Primeiro deploy

1. Conecte o repositório GitHub no painel do Railway
2. Configure todas as variáveis de ambiente acima
3. O Railway fará o build da imagem Docker e publicará o dashboard automaticamente

---

## 7. ETL e agendamento (GitHub Actions)

O pipeline ETL roda automaticamente via GitHub Actions, conforme definido em [`.github/workflows/etl_pipeline.yml`](.github/workflows/etl_pipeline.yml).

### Agendamento

O ETL executa **todos os dias às 05:00 BRT** (08:00 UTC), de segunda a domingo.

### Secrets necessários no GitHub

Configure em: **GitHub → Settings → Secrets and variables → Actions**

| Secret | Descrição |
|---|---|
| `DATABASE_URL` | Connection string do Supabase (Transaction Pooler, porta 6543) |
| `CONTA_AZUL_CLIENT_ID` | ID do app OAuth |
| `CONTA_AZUL_CLIENT_SECRET` | Secret do app OAuth |
| `CONTA_AZUL_REDIRECT_URI` | URI cadastrada no app |

### Como acionar manualmente

1. Acesse o repositório no GitHub
2. Clique na aba **Actions**
3. Selecione o workflow **ETL — Conta Azul → Supabase**
4. Clique em **Run workflow**

### O que o ETL faz

```
1. Autentica na API do Conta Azul (renova o token OAuth automaticamente)
2. Extrai tabelas de referência: categorias, centros de custo, contas financeiras
3. Extrai dados financeiros: contas a receber, contas a pagar, transferências, saldos
4. Extrai dados comerciais: vendas, contratos, baixas
5. Transforma os dados em star schema (dimensões + fatos)
6. Grava todas as tabelas no schema bi_analytics do Supabase
```

### O que fazer se o ETL falhar

| Erro | Causa provável | Solução |
|---|---|---|
| `Token store vazio` ou `400 Bad Request` ao renovar token | Refresh token expirado | Reautorizar (ver seção 10) |
| `could not translate host name` | `DATABASE_URL` incorreta no GitHub Secret | Corrija o secret com a URL real do Supabase |
| `timeout` | API do Conta Azul lenta | Aumente `CONTA_AZUL_TIMEOUT_SECONDS` no workflow |
| Qualquer outro erro | Ver logs detalhados | Actions → execução com falha → step "Executar pipeline ETL" |

---

## 8. Autenticação — usuários e senhas

O dashboard usa autenticação com bcrypt. As credenciais ficam no `secrets.toml` (local) ou na variável `STREAMLIT_SECRETS` (produção).

### Adicionar um novo usuário

**1.** Gere o hash bcrypt da senha:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'senha_do_usuario', bcrypt.gensalt()).decode())"
```

**2.** Adicione ao `secrets.toml`:

```toml
[credentials.nome_login]
name          = "Nome Completo"
password_hash = "$2b$12$..."   # cole o hash gerado acima
```

**3.** Regenere o `STREAMLIT_SECRETS` em base64 e atualize no Railway.

### Remover um usuário

Basta apagar o bloco `[credentials.nome_login]` correspondente do `secrets.toml` e atualizar o `STREAMLIT_SECRETS` no Railway.

### Regras de segurança

- Após **5 tentativas de senha incorretas**, o login fica bloqueado por **5 minutos**
- A sessão expira automaticamente após **8 horas**
- Senhas nunca ficam armazenadas em texto puro — apenas o hash bcrypt

---

## 9. Banco de dados (Supabase)

### Schemas

| Schema | Acesso externo | Conteúdo |
|---|---|---|
| `bi_analytics` | Não (isolado) | Tabelas de dados extraídos do Conta Azul |
| `public` | Via API REST (com RLS) | Tabelas de controle da aplicação |

### Tabelas de controle (schema `public`)

| Tabela | Descrição |
|---|---|
| `bi_cenarios` | Linha única (id=1) com os cenários de projeção de caixa em formato JSONB. Editada pelo dashboard. |
| `bi_oauth_tokens` | Linha única (id=1) com os tokens OAuth do Conta Azul. Atualizada automaticamente pelo ETL. |

Ambas têm **Row Level Security (RLS)** habilitado — apenas o `service_role` tem acesso (o `anon` não consegue ler nem escrever).

### Tabelas de analytics (schema `bi_analytics`)

Criadas e substituídas automaticamente a cada execução do ETL.

**Dimensões:**

| Tabela | Descrição |
|---|---|
| `dim_categoria` | Categorias financeiras com classificação DRE |
| `dim_categoria_dre` | Estrutura hierárquica do DRE |
| `dim_centro_custo` | Centros de custo cadastrados |
| `dim_conta_financeira` | Contas bancárias e caixas |
| `dim_pessoa` | Clientes e fornecedores |
| `dim_calendario` | Calendário diário com ano, mês, trimestre |

**Fatos:**

| Tabela | Descrição |
|---|---|
| `fato_contas_a_receber` | Parcelas a receber com status e flags de vencimento |
| `fato_contas_a_pagar` | Parcelas a pagar com status e flags de vencimento |
| `fato_transferencias` | Movimentações entre contas internas |
| `fato_vendas` | Pedidos comerciais |
| `fato_contratos` | Contratos com métricas de recorrência |
| `fato_baixas` | Pagamentos efetivados |
| `fato_fluxo_caixa_realizado` | Caixa real (baseado na data de pagamento) |
| `fato_fluxo_caixa_compromissado` | Caixa futuro (baseado na data de vencimento) |
| `fato_financeiro_consolidado` | União de receitas e despesas |
| `fato_saldos_contas` | Saldo atual de cada conta financeira |

### Migration inicial

O arquivo [`migrations/001_init_supabase.sql`](migrations/001_init_supabase.sql) cria as tabelas `bi_cenarios` e `bi_oauth_tokens` com RLS. Deve ser executado **uma única vez** no SQL Editor do Supabase ao configurar um novo projeto.

As tabelas de analytics (`fato_*`, `dim_*`) **não precisam de migration** — são criadas automaticamente pelo ETL.

---

## 10. Manutenção e troubleshooting

### Reautorização OAuth (token expirado)

O ETL renova os tokens automaticamente a cada execução. A reautorização manual é necessária apenas se:
- O refresh token expirar por inatividade prolongada
- O acesso for revogado no painel do Conta Azul

**Como reautorizar:**

**1.** Abra a URL de autorização no navegador (gerada pelo comando abaixo):

```bash
python -m contaazul_bi.main authorize
```

> Se o terminal não aceitar input interativo, copie a URL exibida, abra no navegador, faça login e copie a URL final de redirecionamento.

**2.** Após autorizar, o Conta Azul redireciona para `https://scua.com.br/?code=...&state=...`. Copie essa URL completa.

**3.** Execute no terminal Python para trocar o code pelos tokens sem o fluxo interativo:

```python
# Dentro do diretório do projeto, com o .env configurado
from contaazul_bi.config import Settings
from contaazul_bi.oauth import ContaAzulOAuthManager

settings = Settings.from_env()
manager = ContaAzulOAuthManager(settings)
manager.exchange_code_for_tokens("COLE_O_CODE_AQUI")
```

Os novos tokens são salvos automaticamente no Supabase.

### Dashboard não carrega dados

1. Verifique se o ETL rodou com sucesso recentemente (aba Actions no GitHub)
2. Se não rodou: acione manualmente e aguarde a conclusão
3. Se o dashboard ainda mostrar aviso de dados ausentes: verifique se `DATABASE_URL` está correta no Railway

### Adicionar o schema `bi_analytics` ao Supabase (novo projeto)

Após criar o projeto Supabase e rodar a migration inicial, execute no SQL Editor:

```sql
CREATE SCHEMA IF NOT EXISTS bi_analytics;

GRANT USAGE ON SCHEMA bi_analytics TO service_role;
GRANT ALL ON ALL TABLES IN SCHEMA bi_analytics TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA bi_analytics
    GRANT ALL ON TABLES TO service_role;
```

Em seguida, acione o ETL manualmente para popular as tabelas.

### Logs de diagnóstico

- **ETL:** GitHub → Actions → execução → step "Executar pipeline ETL"
- **Dashboard:** Railway → seu serviço → aba Logs
- **Banco:** Supabase → SQL Editor (para inspecionar dados diretamente)

---

## 11. Responsável e transferência de ownership

| Campo | Valor |
|---|---|
| Responsável atual | Thiago Carvalho |
| Contato | — |
| Repositório | `github.com/thiagocangussuc/bi-finance-scua` (privado) |
| Dashboard | URL pública configurada no Railway |
| Supabase | Projeto na organização Scua |

### Para transferir o projeto

1. **GitHub:** Transferir o repositório em Settings → Danger Zone → Transfer repository
2. **Railway:** Convidar o novo responsável como membro do projeto e remover o anterior
3. **Supabase:** Transferir a organização ou convidar o novo responsável como admin
4. **Secrets:** Compartilhar de forma segura os valores de `CONTA_AZUL_CLIENT_ID`, `CONTA_AZUL_CLIENT_SECRET` e `DATABASE_URL`
5. **Reautorização:** O novo responsável precisará reautorizar o OAuth com sua conta do Conta Azul (ver seção 10)
