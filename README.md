# BI Gerencial — Pipeline ETL Conta Azul (v2)

Este projeto extrai dados financeiros e comerciais do ERP **Conta Azul**, transforma-os em um modelo dimensional (star schema) e grava arquivos **Parquet** prontos para consumo no **Power BI** ou em qualquer ferramenta de BI compatível.

---

## Sumário

1. [O que este projeto faz](#1-o-que-este-projeto-faz)
2. [Como os dados fluem — visão geral](#2-como-os-dados-fluem--visão-geral)
3. [Pré-requisitos](#3-pré-requisitos)
4. [Instalação](#4-instalação)
5. [Configuração — variáveis de ambiente](#5-configuração--variáveis-de-ambiente)
6. [Primeiro uso — autorização OAuth](#6-primeiro-uso--autorização-oauth)
7. [Executando o pipeline](#7-executando-o-pipeline)
8. [O que é gerado — arquivos de saída](#8-o-que-é-gerado--arquivos-de-saída)
9. [Modelo de dados — star schema](#9-modelo-de-dados--star-schema)
10. [Estrutura do código](#10-estrutura-do-código)
11. [Como conectar ao Power BI](#11-como-conectar-ao-power-bi)
12. [Diagnóstico e solução de problemas](#12-diagnóstico-e-solução-de-problemas)
13. [Referência de todas as variáveis de ambiente](#13-referência-de-todas-as-variáveis-de-ambiente)

---

## 1. O que este projeto faz

O Conta Azul armazena as informações em sua própria nuvem, acessível via API REST. O Power BI não consegue se conectar diretamente a essa API — ele precisa de arquivos ou banco de dados estruturados.

Este projeto resolve esse problema em três etapas automáticas:

```
API Conta Azul  ──►  Extração (Python)  ──►  Arquivos Parquet  ──►  Power BI
```

| Etapa | O que acontece |
|---|---|
| **Extração** | Faz chamadas paginadas à API do Conta Azul e salva os dados brutos em Parquet |
| **Transformação** | Aplica regras de negócio, normaliza campos, cria métricas calculadas |
| **Carga** | Grava as tabelas do modelo analítico (star schema) em Parquet |

O pipeline é executado manualmente (ou agendado) sempre que se quer atualizar o Power BI com os dados mais recentes.

---

## 2. Como os dados fluem — visão geral

```
┌─────────────────────────────────────────────────────────────┐
│                     Conta Azul API v2                        │
│  /categorias  /contas-a-receber  /contas-a-pagar             │
│  /transferencias  /contratos  /vendas  /pessoas  /baixas     │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP/JSON (paginado, com retry)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Extratores (extractors/)                     │
│  FinanceExtractor  SalesExtractor  ContractsExtractor        │
│  PeopleExtractor   InvoiceExtractor                          │
└──────────────────────┬──────────────────────────────────────┘
                       │ DataFrames pandas
                       ▼
┌─────────────────────────────────────────────────────────────┐
│               output/raw/*.parquet  (dados brutos)           │
│  categorias  contas_receber  contas_pagar  transferencias    │
│  vendas  contratos  parcelas  baixas  ...                    │
└──────────────────────┬──────────────────────────────────────┘
                       │ DataFrames pandas
                       ▼
┌─────────────────────────────────────────────────────────────┐
│          Transformador (transformers/analytics.py)           │
│  • Normaliza datas e valores monetários                      │
│  • Calcula sinalizadores (está_vencido, recorrente, etc.)    │
│  • Enriquece baixas com dados de origem da parcela           │
│  • Cria dimensão calendário                                  │
│  • Monta star schema completo                                │
└──────────────────────┬──────────────────────────────────────┘
                       │ DataFrames pandas
                       ▼
┌─────────────────────────────────────────────────────────────┐
│           output/analytics/*.parquet  (modelo BI)            │
│  dim_categoria  dim_calendario  fato_contas_a_receber        │
│  fato_baixas  fato_fluxo_caixa_realizado  ...                │
└─────────────────────────────────────────────────────────────┘
                       │
                       ▼
                  Power BI Desktop
```

---

## 3. Pré-requisitos

| Requisito | Versão mínima | Como verificar |
|---|---|---|
| Python | 3.10 | `python --version` |
| pip | qualquer | `pip --version` |
| Acesso ao Conta Azul | — | Conta com perfil de integração |
| App OAuth cadastrado no Conta Azul | — | Veja seção 6 |

> **Dica:** Recomenda-se usar Python 3.12 para garantir compatibilidade com as versões de pacotes testadas.

---

## 4. Instalação

### 4.1 Clone ou copie o projeto

Se você recebeu o projeto como arquivo ZIP, extraia-o para uma pasta de sua preferência.
Se estiver usando git:

```bash
git clone <url-do-repositorio>
cd "02 - Projeto - BI (v2)"
```

### 4.2 Crie um ambiente virtual (recomendado)

Um ambiente virtual isola as dependências deste projeto das demais instalações Python do seu computador.

```bash
# Criar o ambiente virtual
python -m venv .venv

# Ativar no Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Ativar no Windows (cmd)
.\.venv\Scripts\activate.bat

# Ativar no Linux/macOS
source .venv/bin/activate
```

> Após ativar, você verá `(.venv)` no início do prompt de comando.

### 4.3 Instale as dependências

```bash
pip install -r requirements.txt
```

Isso instala os 4 pacotes necessários:

| Pacote | Para que serve |
|---|---|
| `pandas` | Manipulação de tabelas em memória |
| `requests` | Chamadas HTTP à API do Conta Azul |
| `python-dotenv` | Leitura do arquivo `.env` |
| `pyarrow` | Leitura e escrita de arquivos Parquet |

---

## 5. Configuração — variáveis de ambiente

Todas as configurações do pipeline ficam no arquivo `.env`, na raiz do projeto. Este arquivo **nunca deve ser enviado ao Git** (já está protegido pelo `.gitignore`).

Se o arquivo `.env` não existir, crie-o copiando o template abaixo:

```dotenv
# ─── Credenciais OAuth do app cadastrado no Conta Azul ────────────────────────
CONTA_AZUL_CLIENT_ID=seu_client_id_aqui
CONTA_AZUL_CLIENT_SECRET=seu_client_secret_aqui
CONTA_AZUL_REDIRECT_URI=https://scua.com.br/

# ─── URLs da API (não alterar salvo mudança de versão da API) ─────────────────
CONTA_AZUL_AUTH_BASE_URL=https://auth.contaazul.com
CONTA_AZUL_API_BASE_URL=https://api-v2.contaazul.com
CONTA_AZUL_SCOPE=openid profile aws.cognito.signin.user.admin

# ─── Onde guardar tokens e arquivos de saída ──────────────────────────────────
CONTA_AZUL_TOKEN_STORE_PATH=.secrets/conta_azul_tokens.json
CONTA_AZUL_OUTPUT_DIR=output

# ─── Comportamento do pipeline ────────────────────────────────────────────────
CONTA_AZUL_LOG_LEVEL=INFO
CONTA_AZUL_TIMEOUT_SECONDS=60
CONTA_AZUL_PAGE_SIZE=100

# ─── Janela de datas (quanto de histórico/futuro buscar) ─────────────────────
CONTA_AZUL_LOOKBACK_YEARS=1
CONTA_AZUL_LOOKAHEAD_YEARS=1
CONTA_AZUL_INSTALLMENT_LOOKBACK_MONTHS=12

# ─── Módulos opcionais (true/false) ───────────────────────────────────────────
CONTA_AZUL_ENABLE_SALES=true
CONTA_AZUL_ENABLE_CONTRACTS=true
CONTA_AZUL_ENABLE_PEOPLE=false
CONTA_AZUL_ENABLE_INVOICES=false
CONTA_AZUL_ENABLE_INSTALLMENT_ENRICHMENT=true
CONTA_AZUL_ENABLE_ACQUITTANCES=true
```

> **Onde encontrar `CLIENT_ID` e `CLIENT_SECRET`?**
> No painel do Conta Azul, acesse **Configurações → Integrações → Aplicativos OAuth**.
> Crie um novo app (ou use o existente) e copie as credenciais geradas.
> A `REDIRECT_URI` cadastrada no app **precisa ser idêntica** ao valor no `.env`.

---

## 6. Primeiro uso — autorização OAuth

O Conta Azul usa o padrão **OAuth 2.0 Authorization Code**. Isso significa que, na primeira vez, é necessário autorizar o app manualmente pelo navegador. Após isso, o pipeline se autentica sozinho indefinidamente usando o *refresh token*.

### Passo a passo

**1.** Com o ambiente virtual ativado, execute:

```bash
python -m contaazul_bi.main authorize
```

**2.** O terminal exibirá uma URL e abrirá o navegador automaticamente. Faça login no Conta Azul e clique em **Autorizar**.

**3.** Após autorizar, o Conta Azul redireciona para a URL cadastrada (ex: `https://scua.com.br/?code=abc123&state=xyz`). Essa página pode dar erro — isso é normal. Copie a URL completa da barra do navegador.

**4.** Cole a URL no terminal quando solicitado e pressione Enter.

**5.** Os tokens são salvos em `.secrets/conta_azul_tokens.json`. A partir deste momento, o pipeline renova os tokens automaticamente a cada execução — **você não precisa repetir este passo**.

> **Quando repetir este passo?**
> Somente se o arquivo `.secrets/conta_azul_tokens.json` for excluído, se o app for revogado no Conta Azul, ou se o *refresh token* expirar por inatividade prolongada (geralmente 30 dias sem executar o pipeline).

---

## 7. Executando o pipeline

Com o ambiente virtual ativado, na raiz do projeto:

```bash
python -m contaazul_bi.main run
```

O pipeline executa as seguintes fases em sequência:

```
1. Extração de tabelas de referência  (categorias, DRE, centros de custo, contas financeiras)
2. Extração financeira                (contas a receber, contas a pagar, transferências, saldos)
3. Extração comercial                 (vendas, contratos, pessoas)
4. Extração de baixas                 (detalhamento de cada parcela quitada — mais lento)
5. Transformação analítica            (star schema, métricas, calendário)
6. Gravação dos arquivos Parquet      (raw/ e analytics/)
7. Geração do run_summary.json        (resumo com contagem de linhas por tabela)
```

### Tempo esperado

A etapa mais demorada é a extração de baixas (item 4), que faz uma chamada HTTP por parcela quitada. Com ~550 parcelas, isso leva aproximadamente **2 minutos**. O tempo total típico é de **2 a 3 minutos**.

### Saída do terminal

Durante a execução, cada linha de log segue o formato:

```
2026-03-21 18:15:08 | INFO | contaazul_bi.client | Endpoint /v1/categorias página 1/2: 100 registros.
```

| Campo | Significado |
|---|---|
| Data/hora | Momento do evento |
| Nível | `INFO` = normal, `WARNING` = algo a investigar, `ERROR` = falha |
| Módulo | Qual parte do código gerou a mensagem |
| Mensagem | O que aconteceu |

Um `WARNING` não interrompe o pipeline — é apenas um aviso. Por exemplo:

```
WARNING | Tabela 'dim_pessoa' retornou 0 linhas. Verifique a extração.
```

---

## 8. O que é gerado — arquivos de saída

Após a execução, a pasta `output/` terá esta estrutura:

```
output/
├── raw/                          ← dados brutos vindos da API
│   ├── categorias.parquet
│   ├── categorias_dre.parquet
│   ├── centros_custo.parquet
│   ├── contas_financeiras.parquet
│   ├── saldos_contas_financeiras.parquet
│   ├── contas_receber.parquet
│   ├── contas_pagar.parquet
│   ├── transferencias.parquet
│   ├── vendas.parquet
│   ├── contratos.parquet
│   ├── parcelas.parquet
│   └── baixas.parquet
│
├── analytics/                    ← modelo dimensional pronto para o Power BI
│   ├── dim_categoria.parquet
│   ├── dim_categoria_dre.parquet
│   ├── dim_centro_custo.parquet
│   ├── dim_conta_financeira.parquet
│   ├── dim_pessoa.parquet
│   ├── dim_calendario.parquet
│   ├── fato_saldos_contas.parquet
│   ├── fato_contas_a_receber.parquet
│   ├── fato_contas_a_pagar.parquet
│   ├── fato_transferencias.parquet
│   ├── fato_vendas.parquet
│   ├── fato_contratos.parquet
│   ├── fato_baixas.parquet
│   ├── fato_fluxo_caixa_realizado.parquet
│   ├── fato_fluxo_caixa_compromissado.parquet
│   └── fato_financeiro_consolidado.parquet
│
└── meta/
    └── run_summary.json          ← resumo da última execução
```

### O arquivo run_summary.json

Após cada execução bem-sucedida, este arquivo é atualizado com:

- Data e hora de início e fim
- Duração em segundos
- Número de linhas de cada tabela gerada
- Lista de tabelas que retornaram 0 linhas (`zero_row_warnings`) — útil para detectar problemas de extração

Exemplo:
```json
{
  "started_at": "2026-03-21T18:15:08",
  "finished_at": "2026-03-21T18:17:13",
  "duration_seconds": 124.8,
  "zero_row_warnings": ["dim_pessoa"],
  "raw_outputs": { "categorias": { "rows": 127, "parquet": "..." } },
  "analytics_outputs": { "fato_baixas": { "rows": 552, "parquet": "..." } }
}
```

---

## 9. Modelo de dados — star schema

O modelo analítico segue o padrão **star schema**: tabelas de dimensão (prefixo `dim_`) descrevem "quem/o quê/quando" e tabelas de fato (prefixo `fato_`) descrevem eventos com valores numéricos.

### Dimensões

| Tabela | Descrição |
|---|---|
| `dim_categoria` | Categorias financeiras do plano de contas, enriquecidas com grupos e subgrupos do DRE |
| `dim_categoria_dre` | Estrutura hierárquica do DRE (Demonstrativo de Resultado do Exercício) |
| `dim_centro_custo` | Centros de custo cadastrados |
| `dim_conta_financeira` | Contas bancárias e caixas cadastrados |
| `dim_pessoa` | Clientes e fornecedores |
| `dim_calendario` | Calendário diário com ano, mês, trimestre, semana — gerado automaticamente cobrindo todo o intervalo de datas presente nos dados |

### Fatos

| Tabela | Granularidade | Descrição |
|---|---|---|
| `fato_contas_a_receber` | 1 linha por parcela a receber | Receitas previstas/realizadas, com status e flags de vencimento |
| `fato_contas_a_pagar` | 1 linha por parcela a pagar | Despesas previstas/realizadas, com status e flags de vencimento |
| `fato_transferencias` | 1 linha por transferência | Movimentações entre contas financeiras internas |
| `fato_vendas` | 1 linha por venda | Pedidos comerciais vinculados a contratos |
| `fato_contratos` | 1 linha por contrato | Contratos com métricas calculadas (recorrência, valor base de renovação) |
| `fato_baixas` | 1 linha por pagamento efetivado | Baixas reais com dados de origem da parcela e metadados de categoria/centro de custo |
| `fato_fluxo_caixa_realizado` | 1 linha por baixa, com `data_fluxo` | Visão de caixa realizado (baseado na data de pagamento efetivo) |
| `fato_fluxo_caixa_compromissado` | 1 linha por parcela, com `data_fluxo` | Visão de caixa compromissado (baseado na data de vencimento) |
| `fato_financeiro_consolidado` | 1 linha por parcela | União de receitas e despesas com colunas de competência e vencimento |
| `fato_saldos_contas` | 1 linha por conta financeira | Saldo atual de cada conta |

### Campos calculados relevantes

| Campo | Tabela(s) | Descrição |
|---|---|---|
| `tipo_evento` | `fato_contas_a_receber/pagar` | `"RECEITA"` ou `"DESPESA"` |
| `valor_documento_sinal` | `fato_contas_a_receber/pagar` | Valor com sinal: positivo para receitas, negativo para despesas |
| `esta_vencido` | `fato_contas_a_receber/pagar` | `true` se vencimento passou e não está quitado |
| `status_normalizado` | `fato_contas_a_receber/pagar` | Status em maiúsculas para facilitar filtros |
| `valor_baixa_sinal` | `fato_baixas` | Valor pago com sinal (positivo/negativo conforme tipo) |
| `possui_historico_recorrente` | `fato_contratos` | `true` se o contrato tem 4 ou mais vendas vinculadas |
| `elegivel_renovacao_sem_churn` | `fato_contratos` | `true` se o contrato está ativo, tem histórico e tem valor base calculado |
| `entrada_dre` | `dim_categoria` | Classificação DRE da categoria (ex: `DEDUCOES_RECEITA_BRUTA`) |

---

## 10. Estrutura do código

```
contaazul_bi/
├── __init__.py
├── config.py             ← Todas as configurações lidas do .env
├── oauth.py              ← Gerenciamento de tokens OAuth (login, refresh automático)
├── client.py             ← Cliente HTTP: paginação, retry, tratamento de erros
├── main.py               ← Orquestrador do pipeline + CLI (authorize / run)
├── logging_utils.py      ← Configuração do formato de log
├── utils.py              ← Funções auxiliares (Parquet, JSON, datas, coalesce)
├── extractors/
│   ├── finance.py        ← Categorias, contas, transferências, baixas
│   ├── sales.py          ← Vendas
│   ├── contracts.py      ← Contratos
│   ├── people.py         ← Pessoas (clientes/fornecedores)
│   └── invoices.py       ← Notas fiscais (desabilitado por padrão)
└── transformers/
    └── analytics.py      ← Toda a lógica de transformação e montagem do star schema
```

### Como cada parte se encaixa

```
.env
 └─► config.py (Settings)
       └─► oauth.py (ContaAzulOAuthManager)     ← cuida do token de acesso
             └─► client.py (ContaAzulClient)    ← faz as chamadas HTTP
                   └─► extractors/*.py          ← sabem quais endpoints chamar
                         └─► main.py            ← orquestra tudo e chama analytics.py
                               └─► transformers/analytics.py
                                     └─► output/analytics/*.parquet
```

### Detalhes de cada módulo

**`config.py`**
Lê todas as variáveis do `.env` e as expõe como atributos tipados na classe `Settings`. Calcula automaticamente as janelas de data (`dynamic_date_from`, `dynamic_date_to`) com base na data de hoje e nos anos de lookback/lookahead configurados. A data de referência é fixada no momento em que o pipeline é iniciado — ela não muda no meio da execução.

**`oauth.py`**
Implementa o fluxo OAuth 2.0. Salva os tokens em `.secrets/conta_azul_tokens.json` e os renova automaticamente quando expiram (normalmente a cada 1 hora). O pipeline nunca solicita nova autorização manual — apenas a primeira vez.

**`client.py`**
Toda comunicação com a API passa por aqui. Funcionalidades importantes:
- **Paginação automática**: continua buscando páginas até trazer todos os registros
- **Retry inteligente**: erros de rede e erros 5xx/429 são retentados com espera progressiva (1s, 2s, 4s...); erros 4xx (requisição inválida) **não** são retentados pois são permanentes
- **Renovação de token**: ao receber erro 401 (token expirado), renova e tenta novamente

**`main.py`**
O orquestrador. Define a ordem de extração, faz o "backfill" de contratos ausentes (contratos referenciados em vendas mas não retornados pelo endpoint de contratos são reconstruídos a partir das vendas), e gerencia o fluxo de parcelas para busca de baixas.

**`transformers/analytics.py`**
A lógica analítica. Recebe os DataFrames brutos e devolve o dicionário com todas as tabelas do star schema. Nenhuma chamada HTTP acontece aqui — é puramente transformação em memória.

---

## 11. Como conectar ao Power BI

### Opção A — Conectar diretamente aos arquivos Parquet

1. Abra o Power BI Desktop
2. Clique em **Obter Dados → Parquet**
3. Aponte para a pasta `output/analytics/`
4. Repita para cada tabela que deseja importar, ou use **Power Query** para criar uma função que carregue todas de uma vez

### Opção B — Usar uma pasta como fonte

1. **Obter Dados → Pasta**
2. Aponte para `output/analytics/`
3. No Power Query, filtre pelo nome do arquivo para separar as tabelas

### Atualização dos dados no Power BI

1. Execute o pipeline Python: `python -m contaazul_bi.main run`
2. No Power BI Desktop, clique em **Atualizar**

Os arquivos Parquet são sobrescritos a cada execução — o Power BI lerá automaticamente os dados mais recentes ao atualizar.

> **Dica:** Para automatizar a execução diária, crie uma tarefa no **Agendador de Tarefas do Windows** apontando para:
> ```
> python "caminho\completo\para\contaazul_bi\main.py" run
> ```
> Ou use o script PowerShell em `tools/build_forecast_workbook.ps1` como referência.

---

## 12. Diagnóstico e solução de problemas

### "Token store vazio. Execute `authorize` primeiro."

O arquivo `.secrets/conta_azul_tokens.json` não existe. Execute a autorização inicial:
```bash
python -m contaazul_bi.main authorize
```

### "Variável de ambiente obrigatória não informada: CONTA_AZUL_CLIENT_ID"

O arquivo `.env` não foi encontrado ou está incompleto. Verifique se ele existe na raiz do projeto e contém `CONTA_AZUL_CLIENT_ID` e `CONTA_AZUL_CLIENT_SECRET`.

### "Autorização recusada. error=invalid_client"

As credenciais `CLIENT_ID`/`CLIENT_SECRET` estão incorretas. Verifique no painel do Conta Azul.

### "State do OAuth divergente"

A URL colada no terminal não corresponde à sessão de autorização iniciada. Tente o fluxo novamente sem recarregar a página do navegador.

### O pipeline demora muito ou trava na extração de baixas

A extração de baixas faz uma chamada por parcela. Com muitas parcelas, isso é esperado. Para reduzir o tempo, diminua a janela de busca no `.env`:
```dotenv
CONTA_AZUL_LOOKBACK_YEARS=1
CONTA_AZUL_INSTALLMENT_LOOKBACK_MONTHS=6
```

### `WARNING: Tabela 'dim_pessoa' retornou 0 linhas`

O endpoint `/v1/pessoas` retornou vazio. Isso pode indicar falta de permissão no app OAuth ou que o módulo de pessoas não está habilitado no plano do Conta Azul. O pipeline continua normalmente — apenas esta tabela ficará vazia.

### `WARNING: Foram identificados N contratos referenciados em vendas que não vieram do endpoint de contratos`

Vendas estão vinculadas a contratos que o endpoint `/v1/contratos` não retornou (contratos muito antigos, fora da janela de datas, ou excluídos). O pipeline cria registros substitutos automaticamente a partir das vendas para não perder o vínculo analítico.

### Como ativar logs mais detalhados

No `.env`:
```dotenv
CONTA_AZUL_LOG_LEVEL=DEBUG
```

Isso exibirá todos os detalhes das chamadas HTTP, útil para diagnosticar erros de API.

---

## 13. Referência de todas as variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `CONTA_AZUL_CLIENT_ID` | Sim | — | ID do app OAuth no Conta Azul |
| `CONTA_AZUL_CLIENT_SECRET` | Sim | — | Secret do app OAuth no Conta Azul |
| `CONTA_AZUL_REDIRECT_URI` | Sim | `https://scua.com.br/` | URI de redirecionamento cadastrada no app |
| `CONTA_AZUL_SCOPE` | Não | `openid profile aws.cognito.signin.user.admin` | Escopos OAuth solicitados |
| `CONTA_AZUL_AUTH_BASE_URL` | Não | `https://auth.contaazul.com` | Base URL do servidor de autenticação |
| `CONTA_AZUL_API_BASE_URL` | Não | `https://api-v2.contaazul.com` | Base URL da API de dados |
| `CONTA_AZUL_TOKEN_STORE_PATH` | Não | `.secrets/conta_azul_tokens.json` | Onde salvar os tokens OAuth |
| `CONTA_AZUL_OUTPUT_DIR` | Não | `output` | Pasta raiz de saída dos arquivos Parquet |
| `CONTA_AZUL_LOG_LEVEL` | Não | `INFO` | Nível de log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CONTA_AZUL_TIMEOUT_SECONDS` | Não | `60` | Timeout (em segundos) de cada chamada HTTP |
| `CONTA_AZUL_PAGE_SIZE` | Não | `100` | Quantos registros por página na paginação |
| `CONTA_AZUL_LOOKBACK_YEARS` | Não | `3` | Quantos anos no passado buscar (janela de datas) |
| `CONTA_AZUL_LOOKAHEAD_YEARS` | Não | `3` | Quantos anos no futuro buscar (ex: parcelas futuras) |
| `CONTA_AZUL_INSTALLMENT_LOOKBACK_MONTHS` | Não | `12` | Quantos meses retroativos considerar para buscar baixas |
| `CONTA_AZUL_ENABLE_SALES` | Não | `true` | Ativa extração de vendas |
| `CONTA_AZUL_ENABLE_CONTRACTS` | Não | `true` | Ativa extração de contratos |
| `CONTA_AZUL_ENABLE_PEOPLE` | Não | `true` | Ativa extração de pessoas |
| `CONTA_AZUL_ENABLE_INVOICES` | Não | `false` | Ativa extração de notas fiscais (beta) |
| `CONTA_AZUL_ENABLE_INSTALLMENT_ENRICHMENT` | Não | `false` | Ativa enriquecimento de parcelas (necessário para baixas) |
| `CONTA_AZUL_ENABLE_ACQUITTANCES` | Não | `false` | Ativa extração de baixas (depende do enriquecimento) |

> **Nota sobre `ENABLE_ACQUITTANCES`:** Para que a extração de baixas funcione, tanto `ENABLE_INSTALLMENT_ENRICHMENT` quanto `ENABLE_ACQUITTANCES` precisam estar como `true`. As baixas são o detalhamento dos pagamentos efetivos — são a base do `fato_fluxo_caixa_realizado`.
