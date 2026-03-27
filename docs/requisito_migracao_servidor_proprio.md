# Documento Tecnico - Migracao do BI Finance do Railway para Servidor Proprio

Data: 2026-03-27
Autor da analise: Codex

## 1. Objetivo

Este documento descreve os requisitos tecnicos para retirar a hospedagem do dashboard BI Finance do Railway e publica-lo em servidor proprio da empresa, sem perder as capacidades atuais da aplicacao.

O foco deste material e permitir que o time interno de desenvolvimento e infraestrutura execute a migracao com clareza de escopo, riscos, dependencias, backlog tecnico e criterios de aceite.

## 2. Resumo Executivo

O codigo atual esta pouco acoplado ao Railway. Na pratica, o Railway hoje entrega principalmente:

- build e execucao do container Docker
- injecao de variaveis de ambiente
- exposicao de porta publica
- healthcheck do processo Streamlit

A logica de negocio da aplicacao, o ETL e a persistencia de dados nao dependem do Railway.

Porem, a aplicacao continua dependente de servicos externos que nao sao substituidos automaticamente ao sair do Railway:

- Supabase/PostgreSQL
- GitHub Actions, caso o agendamento do ETL permaneca fora do servidor
- API OAuth/API REST da Conta Azul
- Google Fonts, no CSS do dashboard

Recomendacao tecnica:

1. Fase 1 recomendada: migrar apenas a hospedagem do dashboard para servidor proprio, mantendo Supabase e GitHub Actions.
2. Fase 2 opcional: migrar o ETL agendado para o servidor proprio, se a empresa quiser internalizar tambem a rotina operacional.

Essa abordagem reduz risco, acelera o go-live e evita misturar mudanca de hospedagem com mudanca de banco e de scheduler ao mesmo tempo.

## 3. Escopo e Premissas

### 3.1 Escopo deste documento

- hospedagem do dashboard Streamlit em servidor proprio
- requisitos de runtime, rede, seguranca, observabilidade e operacao
- requisitos adicionais caso o ETL tambem seja movido para o servidor
- ajustes necessarios no projeto para sustentacao em ambiente corporativo

### 3.2 Fora de escopo inicial

- substituir o Supabase por outro banco
- reescrever o dashboard em outro framework
- substituir a API da Conta Azul
- implementar SSO corporativo no lugar do login atual

### 3.3 Premissas

- o servidor proprio possui acesso de rede para o banco Supabase
- a empresa pode armazenar secrets no servidor de forma segura
- o repositorio continuara versionado no GitHub
- a equipe aceita manter Python 3.12 e Streamlit como stack principal

## 4. Diagnostico Tecnico do Estado Atual

### 4.1 Arquitetura atual

| Componente | Implementacao atual | Evidencia no repositorio | Impacto na migracao |
|---|---|---|---|
| Dashboard web | Streamlit executado via Docker | `Dockerfile`, `entrypoint.sh`, `dashboard.py` | Portavel para outro host com baixo esforco |
| Runtime web | Porta dinamica via `PORT` e bind em `0.0.0.0` | `entrypoint.sh` | Facil de operar atras de proxy reverso |
| Healthcheck | `/_stcore/health` | `Dockerfile` | Pode ser reutilizado no servidor proprio |
| Secrets do dashboard | `st.secrets` via `.streamlit/secrets.toml` ou `STREAMLIT_SECRETS` em base64 | `dashboard.py`, `entrypoint.sh` | Precisa de estrategia de secret management no novo host |
| Base de dados | Supabase/PostgreSQL via `DATABASE_URL` | `dashboard.py`, `contaazul_bi/oauth.py`, `contaazul_bi/supabase_writer.py` | Continua obrigatorio se o banco nao for migrado |
| ETL | GitHub Actions agendado | `.github/workflows/etl_pipeline.yml` | Pode permanecer externo na Fase 1 |
| OAuth Conta Azul | Tokens persistidos no Supabase quando `DATABASE_URL` esta presente | `contaazul_bi/oauth.py` | Bom para ambiente sem arquivo local de token |
| Persistencia de cenarios | Dashboard grava `bi_cenarios` no banco | `dashboard.py` | O app precisa de permissao de escrita, nao apenas leitura |
| Fallback local | Leitura de parquet/JSON em `output/` quando nao ha banco | `dashboard.py`, `contaazul_bi/utils.py` | Util para desenvolvimento; nao e o fluxo real de producao |

### 4.2 Acoplamentos diretos com Railway

Os acoplamentos diretos identificados foram poucos:

- uso da variavel `PORT` para definir a porta do Streamlit
- uso opcional da variavel `STREAMLIT_SECRETS` para injetar o `secrets.toml`
- existencia de `HEALTHCHECK` Docker apontando para `/_stcore/health`
- README e comentarios orientados ao deploy em Railway

Conclusao: nao ha dependencia forte de SDK, API ou recurso proprietario do Railway no codigo da aplicacao.

### 4.3 Dependencias reais de producao

Mesmo sem Railway, o sistema continua dependendo de:

- `DATABASE_URL` valida para PostgreSQL/Supabase
- credenciais OAuth da Conta Azul
- credenciais do dashboard em `.streamlit/secrets.toml`
- conectividade HTTP de saida
- processo Python/Streamlit supervisionado

### 4.4 Comportamentos relevantes para a migracao

#### Dashboard

- O dashboard abre conexao com banco somente se `DATABASE_URL` existir.
- Se `DATABASE_URL` nao existir, ele tenta ler arquivos locais em `output/analytics`.
- Em build Docker atual, a pasta `output/` nao entra na imagem por causa do `.dockerignore`.
- Na pratica, o modo de producao atual depende do banco, nao do fallback local.
- O carregamento de dados usa `@st.cache_data(ttl=3600)`, o que permite ate 1 hora de defasagem apos uma carga nova do ETL.

#### Autenticacao

- O login usa hashes bcrypt em `st.secrets["credentials"]`.
- O bloqueio por tentativas e o timeout de sessao usam `st.session_state`.
- Isso funciona bem em instancia unica, mas nao implementa sessao centralizada nem lockout compartilhado entre replicas.

#### ETL

- O ETL grava artefatos locais em `output/raw`, `output/analytics` e `output/meta`.
- Mesmo com `DATABASE_URL`, o ETL continua gerando parquet local para debug e rastreabilidade.
- O ETL substitui tabelas analytics com `if_exists="replace"`, ou seja, exige privilegios de DDL no schema `bi_analytics`.
- O workflow atual roda em GitHub Actions com cron `0 10 * * *`.
- Em 2026-03-27, `10:00 UTC` corresponde a `07:00` no fuso `America/Sao_Paulo`.

#### OAuth

- O comando de autorizacao inicial pode abrir navegador localmente, mas tambem aceita `--redirected-url` ou `--code`.
- Isso permite operar em ambiente headless, desde que exista runbook claro.

## 5. Recomendacao de Arquitetura Alvo

### 5.1 Fase 1 recomendada

Migrar somente o dashboard para servidor proprio e manter:

- Supabase como banco
- GitHub Actions como scheduler do ETL
- fluxo atual de OAuth armazenando tokens no Supabase

Arquitetura alvo recomendada:

```text
Usuarios internos
    |
HTTPS / VPN / Rede interna
    |
Proxy reverso corporativo
    |
Container ou servico Streamlit no servidor proprio
    |
PostgreSQL no Supabase

GitHub Actions
    |
ETL diario
    |
API Conta Azul + Supabase
```

Vantagens:

- menor mudanca de arquitetura
- menor risco operacional
- nenhuma reescrita necessaria para o ETL no primeiro momento
- rollback simples

### 5.2 Fase 2 opcional

Migrar tambem o ETL para o servidor proprio.

Quando faz sentido:

- politica corporativa exige retirar rotinas do GitHub Actions
- necessidade de controle local do scheduler
- restricoes de governanca sobre execucao em nuvem publica

Impacto adicional:

- sera preciso agendar `python -m contaazul_bi.main run` no servidor
- sera preciso definir timezone do scheduler
- sera preciso garantir conectividade com `auth.contaazul.com` e `api-v2.contaazul.com`
- sera preciso gerenciar logs, retry operacional e alertas localmente

## 6. Requisitos Tecnicos

### 6.1 Requisitos funcionais

- O dashboard deve permanecer acessivel via navegador sem instalacao local.
- O login atual por usuario/senha deve continuar funcional.
- O dashboard deve continuar lendo as tabelas analytics do banco.
- O dashboard deve continuar gravando cenarios em `bi_cenarios`.
- O healthcheck da aplicacao deve ser monitoravel.
- Deve existir procedimento de restart, rollback e troca de secrets.

### 6.2 Requisitos de infraestrutura

| Item | Requisito |
|---|---|
| Sistema operacional | Recomendado Linux x86_64 |
| Runtime | Recomendado Docker Engine ou Podman com execucao de container |
| Proxy reverso | Nginx, Traefik, Apache ou equivalente corporativo |
| Exposicao externa | Preferencialmente HTTPS em subdominio proprio, por exemplo `bi.interno.empresa` |
| Porta interna da app | `8501` para o processo Streamlit |
| Healthcheck | `GET /_stcore/health` |
| Segredos | Variaveis de ambiente seguras e/ou arquivo montado `.streamlit/secrets.toml` |
| Logs | Captura de stdout/stderr com retencao e rotacao |
| Monitoracao | Status do processo, uso de CPU/RAM e disponibilidade do endpoint |
| Backup | Secrets, manifests de deploy e banco gerenciado segundo politica da empresa |

### 6.3 Requisitos de rede

#### Entrada

- Porta 443 no proxy reverso
- Porta 80 somente se houver redirecionamento controlado para HTTPS
- Porta 8501 restrita ao host local ou a rede interna entre proxy e app

#### Saida

Para Fase 1:

- acesso ao PostgreSQL/Supabase
- opcionalmente acesso a `fonts.googleapis.com` e `fonts.gstatic.com`, se a fonte remota for mantida

Para Fase 2:

- tudo da Fase 1
- acesso HTTPS a `https://auth.contaazul.com`
- acesso HTTPS a `https://api-v2.contaazul.com`

### 6.4 Requisitos de capacidade

Estimativa inicial para o dashboard em instancia unica:

- 2 vCPU
- 4 GB RAM
- 10 GB de disco para logs, imagem e area de trabalho

Estimativa inicial caso dashboard e ETL rodem no mesmo host:

- 4 vCPU
- 8 GB RAM
- 30 GB de disco persistente

Observacao: estas estimativas foram inferidas pela stack atual e devem ser validadas com monitoracao apos a entrada em producao.

### 6.5 Requisitos de seguranca

- `DATABASE_URL`, `CONTA_AZUL_CLIENT_ID`, `CONTA_AZUL_CLIENT_SECRET` e `CONTA_AZUL_REDIRECT_URI` nao podem ser armazenados em repositorio.
- O `secrets.toml` do dashboard deve ser montado em runtime ou injetado por secret manager.
- O processo deve rodar com usuario nao privilegiado.
- O acesso web deve ficar protegido por HTTPS e, idealmente, tambem por VPN/rede interna.
- O acesso SSH/RDP ao servidor deve seguir politica corporativa.
- O time deve definir politica de rotacao de credenciais.

## 7. Ajustes Necessarios para Viabilizar a Migracao

### 7.1 Ajustes obrigatorios para a Fase 1

#### 1. Padronizar a estrategia de deploy on-premise

Entregavel esperado:

- `compose.yaml` ou equivalente corporativo
- politica de restart
- definicao de volumes, env vars e healthcheck

Motivo:

- hoje o repositorio possui `Dockerfile`, mas nao possui manifesto de execucao para ambiente corporativo

#### 2. Definir estrategia oficial de secrets

O projeto hoje suporta:

- `.streamlit/secrets.toml`
- `STREAMLIT_SECRETS` em base64

O time interno deve escolher um padrao operacional:

- montar `.streamlit/secrets.toml` em volume seguro
- ou manter a injecao via variavel de ambiente

#### 3. Definir a URL de acesso do dashboard

Recomendacao:

- publicar em dominio ou subdominio proprio

Motivo:

- o `Streamlit` atual nao esta configurado com `baseUrlPath`; publicar em subcaminho como `/bi/finance` pode exigir ajuste adicional em configuracao e proxy

#### 4. Validar conectividade real com o banco

Ponto importante:

- o dashboard de producao depende do banco
- o fallback local nao entra no build Docker atual porque `output/` esta no `.dockerignore`

Resultado esperado:

- o servidor proprio deve conseguir abrir conexao com o Supabase antes do cutover

#### 5. Padronizar credenciais e privilegios do banco

Achado importante:

- o dashboard precisa ler `bi_analytics`
- o dashboard tambem precisa escrever `bi_cenarios`
- o ETL precisa escrever `bi_oauth_tokens`
- o ETL recria tabelas analytics com `if_exists="replace"`
- o repositorio mistura orientacoes de conexao com banco: `.env.example` pede conexao direta para ETL, enquanto a documentacao menciona uso de pooler em partes do README

Recomendacao minima:

- separar credencial do dashboard e credencial do ETL

Modelo recomendado:

- `DATABASE_URL_DASHBOARD`: leitura em `bi_analytics` + leitura/escrita em `bi_cenarios`
- `DATABASE_URL_ETL`: DDL/DML em `bi_analytics` + leitura/escrita em `bi_oauth_tokens`

Hoje o codigo usa uma unica `DATABASE_URL`, portanto essa separacao exigira pequeno ajuste de configuracao/codigo e e fortemente recomendada.

### 7.2 Ajustes obrigatorios caso o ETL tambem migre para o servidor

#### 1. Substituir o agendamento do GitHub Actions

Alternativas:

- `cron`
- `systemd timer`
- scheduler corporativo
- orquestrador ja adotado internamente

#### 2. Garantir disco gravavel e persistente

Motivo:

- o ETL grava parquet e `run_summary.json` em `output/`

#### 3. Formalizar o processo de autorizacao OAuth em ambiente headless

Runbook minimo:

1. executar `python -m contaazul_bi.main authorize --no-browser`
2. abrir a URL de autorizacao a partir de uma maquina com navegador
3. capturar a URL final de redirecionamento
4. concluir com `--redirected-url` ou `--code`

#### 4. Definir timezone oficial do scheduler

Motivo:

- o agendamento atual usa cron UTC no GitHub Actions
- o horario desejado pelo negocio deve ser explicitamente revalidado ao migrar

### 7.3 Ajustes recomendados, mas nao bloqueantes

#### 1. Reduzir ou invalidar o cache do dashboard apos ETL

Motivo:

- `load_data()` usa cache de 3600 segundos
- apos uma carga nova, o usuario pode continuar vendo dados antigos por ate 1 hora

Opcoes:

- reduzir o TTL
- reiniciar a app apos ETL
- implementar mecanismo de invalidacao

#### 2. Eliminar dependencia de Google Fonts externo

Motivo:

- o CSS importa fonte do Google em runtime
- em rede corporativa fechada, isso pode falhar

Opcoes:

- empacotar a fonte localmente
- usar stack de fontes locais

#### 3. Melhorar a estrategia de escrita no banco

Motivo:

- `to_sql(... if_exists="replace")` recria tabelas
- isso exige privilegios amplos e pode gerar indisponibilidade transitoria durante a carga

Melhoria recomendada:

- escrita em staging + rename/swap controlado
- ou rotina de truncate/insert com DDL minimo

#### 4. Criar testes de fumaca automatizados

Nao foram encontrados testes automatizados no repositorio atual.

Recomendacao minima:

- teste de healthcheck
- teste de conexao com banco
- teste de login com usuario valido
- teste de leitura das tabelas principais
- teste de escrita em `bi_cenarios`

#### 5. Evoluir autenticacao se houver exigencia de HA ou SSO

Motivo:

- o login atual e simples e suficiente para instancia unica
- nao ha SSO, auditoria central nem compartilhamento de lockout entre replicas

## 8. Riscos e Gaps Identificados

| Risco/GAP | Impacto | Severidade | Acao sugerida |
|---|---|---|---|
| Dependencia total do banco em producao | app sobe, mas fica sem dados se nao conectar ao Supabase | Alta | validar conectividade e credenciais antes do cutover |
| Uso potencial de credencial ampla no dashboard | risco de privilegio excessivo | Alta | separar usuario de app e usuario de ETL |
| ETL recriando tabelas com `replace` | risco de indisponibilidade transitoria e necessidade de DDL | Alta | revisar estrategia de escrita em fase posterior |
| Cache de 1 hora no dashboard | dados podem parecer defasados apos ETL | Media | reduzir TTL ou invalidar cache |
| Ausencia de testes automatizados | aumenta risco de regressao no deploy | Media | criar smoke tests minimos |
| Dependencia de Google Fonts externo | possivel degradacao visual em rede fechada | Baixa | empacotar fonte ou trocar stack |
| Lockout e sessao sem centralizacao | limitacao para escalonamento horizontal | Baixa | manter instancia unica ou refatorar auth |
| Divergencia documental sobre banco/pooler | pode confundir operacao | Media | padronizar documentacao e env vars |

## 9. Criterios de Aceite

Considerar a migracao da Fase 1 concluida quando:

- o dashboard estiver acessivel no servidor proprio via HTTPS
- o login com usuarios atuais estiver funcionando
- a leitura das tabelas `fato_contas_a_receber` e `fato_contas_a_pagar` estiver funcional
- a edicao de cenarios estiver persistindo em `bi_cenarios`
- o endpoint `/_stcore/health` responder com sucesso
- existir procedimento validado de restart e rollback
- logs de aplicacao estiverem acessiveis ao time de operacao

Considerar a Fase 2 concluida quando, alem dos itens acima:

- o ETL agendado rodar no servidor proprio
- houver pelo menos 3 execucoes consecutivas com sucesso
- o refresh OAuth ocorrer sem intervencao manual durante o periodo de validacao
- os artefatos de log e `run_summary.json` estiverem acessiveis para diagnostico

## 10. Plano Sugerido de Execucao

### Fase 1 - Migrar apenas o dashboard

1. Provisionar servidor, proxy reverso e mecanismo de secrets.
2. Validar saida de rede para o Supabase.
3. Gerar manifesto de execucao on-premise.
4. Publicar ambiente de homologacao.
5. Validar smoke tests funcionais e healthcheck.
6. Configurar DNS interno ou URL definitiva.
7. Realizar cutover controlado.
8. Monitorar por pelo menos 5 dias uteis.

### Fase 2 - Internalizar o ETL

1. Definir scheduler local e timezone oficial.
2. Provisionar pasta persistente para `output/`.
3. Migrar env vars/secrets do job.
4. Executar autorizacao OAuth com runbook headless.
5. Agendar o job e monitorar logs.
6. Desativar o workflow no GitHub Actions somente apos estabilizacao.

## 11. Backlog Tecnico Sugerido

| Prioridade | Item | Tipo |
|---|---|---|
| Alta | Criar manifesto de deploy on-premise (`compose.yaml` ou equivalente) | Infra |
| Alta | Definir estrategia de secrets para `.streamlit/secrets.toml` | Infra |
| Alta | Separar credenciais de banco entre dashboard e ETL | Codigo/Seguranca |
| Alta | Publicar dashboard em URL raiz ou ajustar configuracao para subpath | Infra/Codigo |
| Alta | Criar runbook de operacao e rollback | Operacao |
| Media | Ajustar cache de dados do dashboard | Codigo |
| Media | Criar smoke tests de deploy | Codigo/QA |
| Media | Padronizar documentacao de conexao com banco | Documentacao |
| Media | Remover dependencia de Google Fonts externo | Codigo |
| Media | Revisar estrategia `if_exists="replace"` do ETL | Codigo |
| Baixa | Evoluir autenticacao para SSO/reverse proxy auth | Arquitetura |

## 12. Conclusao

A migracao de hospedagem do Railway para servidor proprio e viavel com baixo impacto estrutural no projeto, desde que a equipe trate a migracao como um tema de operacao e infraestrutura, e nao como uma simples troca de host.

O caminho mais seguro e:

1. mover primeiro apenas o dashboard
2. manter banco e ETL como estao
3. separar privilegios de banco
4. criar manifestos, runbooks e smoke tests
5. decidir depois se o ETL tambem deve sair do GitHub Actions

Com isso, a empresa reduz dependencia do Railway sem criar um projeto paralelo de replatform completo.
