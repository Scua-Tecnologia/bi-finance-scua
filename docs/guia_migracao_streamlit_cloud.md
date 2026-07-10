# Guia de Migração: Sistema INTEIRO em nuvem gratuita e privada

Objetivo: tirar **todo o sistema** do servidor Linux próprio e hospedá-lo em nuvem
gerenciada, **sem servidor para administrar**, **sem custo** e **100% privado**.

Não existe uma única plataforma gratuita que hospede sozinha as três coisas que o
sistema precisa (app sempre ligado + tarefa agendada + banco de dados). Por isso a
arquitetura gratuita real usa três serviços gratuitos — **todos gerenciados a partir
de uma única conta: a conta corporativa do GitHub**. Você configura uma vez e esquece.

> Este guia substitui o plano de servidor próprio descrito em
> [requisito_migracao_servidor_proprio.md](requisito_migracao_servidor_proprio.md)
> e [guia_migracao_linux.md](guia_migracao_linux.md). O caminho aqui é o oposto:
> **sair** do Linux e voltar para nuvem gerenciada.

## É tudo grátis e privado? (sim)

| Peça | Serviço | Custo | Privado? | Cartão? |
|---|---|---|---|---|
| Código-fonte + ETL | GitHub (repo privado) + GitHub Actions | **R$ 0** | ✅ repo privado | Não |
| Dashboard | Streamlit Community Cloud (app privado) | **R$ 0** | ✅ app privado + login bcrypt | Não |
| Banco de dados | Supabase (plano Free) | **R$ 0** | ✅ acesso só via credencial | Não |

- **Um único ponto de gestão:** a conta corporativa do GitHub. O Community Cloud e o
  Supabase logam/associam a ela; você não gerencia servidor nenhum.
- **Nenhum serviço é pago e nenhum exige cartão de crédito.**
- **Atenção Supabase Free:** projetos gratuitos **pausam após ~7 dias sem acesso**.
  Como o ETL escreve no banco todo dia (via GitHub Actions), o projeto nunca fica
  ocioso — então na prática ele não pausa. Se algum dia pausar, basta reabrir o
  painel do Supabase para reativar.

---

## 1. Arquitetura alvo — o que fica onde

Só o **dashboard** muda de casa. O resto do sistema continua igual.

| Peça | Onde fica depois da migração | Muda? |
|---|---|---|
| Dashboard (`dashboard.py`) | **Streamlit Community Cloud** | ✅ sai do Linux |
| Banco de dados | **Supabase** (o mesmo de hoje) | ❌ não muda |
| ETL (`contaazul_bi/`) | **GitHub Actions** (agendado) | ⚠️ sai do Crontab do Linux, volta pro Actions |
| Tokens OAuth | Tabela `bi_oauth_tokens` no Supabase | ❌ não muda |
| Código-fonte | Repositório no **GitHub** (conta corporativa) | ⚠️ sai do GitLab |

Fluxo final (idêntico ao original em nuvem):

```text
GitHub Actions (ETL diário)  ->  Supabase (dados)  ->  Dashboard no Streamlit Community Cloud  ->  usuário
```

Consequências importantes:

- O `Dockerfile` e o `entrypoint.sh` **deixam de ser usados** nesta hospedagem (o
  Community Cloud instala o `requirements.txt` e roda `streamlit run` sozinho).
  Mantenha-os no repositório para portabilidade futura, mas não precisa mexer neles.
- Como o **Supabase não muda**, os tokens OAuth já salvos continuam válidos —
  **não é necessário reautorizar** o Conta Azul, exceto se as credenciais do app
  OAuth tiverem sido trocadas.

---

## 2. Pré-requisitos

- Acesso ao repositório atual no GitLab (`gitlab.com/scuacorp/bi-finance-scua`).
- Os valores atuais de: `DATABASE_URL`, `CONTA_AZUL_CLIENT_ID`,
  `CONTA_AZUL_CLIENT_SECRET`, `CONTA_AZUL_REDIRECT_URI` e o conteúdo do
  `secrets.toml` (usuários/hashes bcrypt).
- Um e-mail corporativo para a conta GitHub: `desenvolvimento@scua.com.br`.

---

## 3. Parte 1 — Conta corporativa e repositório no GitHub

Isto atende também à parte de **governança de acesso** do ticket: centralizar a
propriedade fora de contas pessoais.

### 3.1 Criar a conta / organização

1. Crie uma conta GitHub usando `desenvolvimento@scua.com.br`.
2. **Recomendado:** crie uma **Organização** GitHub (ex.: `scua`) com essa conta
   como dona. Organização dá controle de membros, papéis e continuidade —
   melhor governança do que um repositório numa conta pessoal.
3. Convide os desenvolvedores como membros com o papel adequado (Write/Maintain);
   reserve o papel de Admin/Owner à conta corporativa.

### 3.2 Trazer o código do GitLab para o GitHub

Opção A — **Importador do GitHub** (mais simples):

1. No GitHub: **New repository → Import a repository**.
2. URL de origem: `https://gitlab.com/scuacorp/bi-finance-scua.git`.
3. Defina o repositório como **Privado** (dados financeiros — nunca público).

Opção B — **Espelho via linha de comando** (preserva todas as branches e tags):

```bash
git clone --mirror https://gitlab.com/scuacorp/bi-finance-scua.git
cd bi-finance-scua.git
git push --mirror https://github.com/<org>/bi-finance-scua.git
```

> Deixe o repositório **privado**. O deploy no Community Cloud funciona com repo
> privado — você autoriza o acesso durante a conexão.

---

## 4. Parte 2 — ETL no GitHub Actions

O workflow [.github/workflows/etl_pipeline.yml](../.github/workflows/etl_pipeline.yml)
já existe e é feito para o GitHub Actions. Ao mover o repo para o GitHub, ele passa
a funcionar nativamente — **substituindo o Crontab do servidor Linux**.

1. No repositório GitHub: aba **Actions** → habilite os workflows.
2. **Settings → Secrets and variables → Actions → New repository secret**, crie:
   - `DATABASE_URL`
   - `CONTA_AZUL_CLIENT_ID`
   - `CONTA_AZUL_CLIENT_SECRET`
   - `CONTA_AZUL_REDIRECT_URI`
3. Agendamento: o workflow roda em `cron: '0 6 * * *'` (**06:00 UTC**, ~03:00 no
   horário de Brasília). Ajuste o cron no arquivo se quiser outro horário — lembre
   que o GitHub Actions usa **UTC**.
4. Teste um disparo manual: **Actions → ETL — Conta Azul → Supabase → Run workflow**.
   O primeiro passo (`oauth-status --force-refresh`) valida o OAuth antes do ETL.

> Como o Supabase é o mesmo, os tokens em `bi_oauth_tokens` continuam válidos.
> Só reautorize (`python -m contaazul_bi.main authorize`) se o app OAuth do
> Conta Azul tiver mudado — veja a seção 10 do [README](../README.md).

---

## 5. Parte 3 — Deploy do dashboard no Streamlit Community Cloud

1. Acesse **https://share.streamlit.io** e entre com a conta **GitHub corporativa**.
2. Autorize o Community Cloud a acessar a organização/repositório (inclusive privados).
3. **Create app → Deploy a public/private app from a repo**:
   - **Repository:** `<org>/bi-finance-scua`
   - **Branch:** `main`
   - **Main file path:** `dashboard.py`
4. **Advanced settings:**
   - **Python version:** selecione **3.12** (mesma versão usada em produção hoje).
   - **Secrets:** cole no formato TOML (ver seção 6 abaixo).
5. Clique em **Deploy**. O Community Cloud instala o `requirements.txt`
   (o `psycopg2-binary` funciona normalmente aqui — é ambiente Linux real, não navegador)
   e sobe o app numa URL `https://<algo>.streamlit.app`.

> Não é necessário `packages.txt` (sem dependências de sistema) nem mexer no
> `Dockerfile`. O `.streamlit/config.toml` do repositório é respeitado.

---

## 6. Parte 4 — Secrets do dashboard (ponto de atenção crítico)

No painel do app: **⋮ → Settings → Secrets**. Cole em formato TOML.

```toml
# ── NÍVEL SUPERIOR (vira variável de ambiente) ───────────────────────────────
# O dashboard lê DATABASE_URL via os.environ (dashboard.py: _get_db_engine).
# Por isso ela PRECISA ficar no topo, fora de qualquer bloco [ ].
DATABASE_URL = "postgresql://...:6543/postgres"

# ── Credenciais de login (lidas de st.secrets["credentials"]) ────────────────
[credentials.thiago]
name          = "Thiago Carvalho"
password_hash = "$2b$12$..."     # hash bcrypt
admin         = true

[credentials.victor]
name          = "Victor Maia"
password_hash = "$2b$12$..."

# ── Disparo manual de ETL pelo painel ⚙ (opcional) ───────────────────────────
[github_actions]
token       = "github_pat_..."   # Fine-grained PAT, permissão actions: read+write
repo_owner  = "<org>"
repo_name   = "bi-finance-scua"
# workflow_id  = "etl_pipeline.yml"   # opcional (padrão)
# workflow_ref = "main"               # opcional (padrão)
```

⚠️ **Erro mais comum:** colocar `DATABASE_URL` dentro de um bloco `[ ]`. O
Streamlit só exporta como variável de ambiente as chaves de **nível superior**;
blocos aninhados ficam acessíveis apenas via `st.secrets[...]`. Como o código lê
`os.environ.get("DATABASE_URL")`, se ela ficar aninhada o dashboard **não acha o
banco** e mostra "dados ainda não foram carregados".

- As variáveis `CONTA_AZUL_*` **não entram aqui** — são usadas só pelo ETL
  (GitHub Actions), não pelo dashboard.
- Editar os secrets reinicia o app automaticamente.

---

## 7. Parte 5 — Controle de acesso (app privado)

Duas camadas de proteção:

1. **Acesso ao app (Community Cloud):** em **Settings → Sharing**, deixe o app
   **privado** e libere por e-mail (**"Who can view this app"**). Só quem estiver
   na lista consegue **carregar** a página.
2. **Login da aplicação (bcrypt):** a tela de login existente (`_run_auth`)
   continua funcionando como segunda camada, com os usuários do bloco
   `[credentials]`.

Para máquinas compartilhadas, o recurso "Lembrar de mim" continua igual (tokens em
`bi_remember_tokens` no Supabase).

---

## 8. Parte 6 — Validação (smoke test) e cutover

Antes de desligar o servidor Linux, valide o app novo:

- [ ] O app abre na URL `*.streamlit.app` e pede login.
- [ ] Login com um usuário real funciona.
- [ ] As páginas **Resumo**, **Cenários**, **Receita** e **DRE** carregam dados.
- [ ] Editar e salvar um cenário persiste (grava em `bi_cenarios`).
- [ ] (Se admin) o botão ⚙ → **Executar ETL agora** dispara o workflow no GitHub.
- [ ] O ETL agendado roda no horário e conclui com sucesso na aba Actions.

Cutover:

1. Comunique a nova URL aos usuários (ou configure um domínio próprio apontando
   para o app, se desejar).
2. **Desligue o servidor Linux:**
   ```bash
   sudo docker rm -f bi-dashboard
   crontab -e   # remova a linha do ETL agendado
   ```
3. Se havia deploy no Railway ainda ativo, pause/remova para evitar duplicidade e custo.

---

## 9. Limitações e cuidados do Community Cloud

- **Recursos limitados** (tier gratuito): memória modesta por app. Este dashboard é
  leve (instância única, cache de 1h em `load_data`), então tende a caber bem.
- **Hibernação:** apps sem acesso por um período entram em repouso e **acordam no
  próximo acesso** (alguns segundos de espera na primeira carga). Aceitável para uso
  interno; se for inaceitável, use Railway/Render pago.
- **Secrets nunca no repositório.** Só no painel do Community Cloud e nos Secrets do
  GitHub Actions.
- **Sem tarefas agendadas no Community Cloud** — por isso o ETL fica no GitHub Actions.
- Se um dia o consumo crescer além do tier gratuito, a rota de fuga é trivial: o
  mesmo repositório com `Dockerfile` sobe em Railway/Render/Cloud Run sem reescrita.

---

## 10. Resumo do que fazer (checklist)

1. [ ] Criar conta/organização GitHub corporativa (`desenvolvimento@scua.com.br`).
2. [ ] Importar o repositório do GitLab para o GitHub (privado).
3. [ ] Configurar Secrets do GitHub Actions e testar o ETL manual.
4. [ ] Deploy no Community Cloud (branch `main`, `dashboard.py`, Python 3.12).
5. [ ] Configurar os Secrets do dashboard — `DATABASE_URL` **no nível superior**.
6. [ ] Deixar o app privado + lista de e-mails autorizados.
7. [ ] Rodar o smoke test.
8. [ ] Desligar container e Crontab do servidor Linux.
