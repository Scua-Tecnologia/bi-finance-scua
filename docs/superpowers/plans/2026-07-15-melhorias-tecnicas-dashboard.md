# Melhorias Técnicas do Dashboard — Plano de Implementação

> **Para executores:** este plano é feito para ser executado tarefa a tarefa, com revisão entre tarefas. Os passos usam checkbox (`- [ ]`) para rastreio. Sub-skill recomendada: `superpowers:subagent-driven-development` ou `superpowers:executing-plans`.

**Goal:** Elevar a qualidade técnica do dashboard (estabilidade de sessão, observabilidade, segurança e login) **sem alterar nenhuma regra de negócio, cálculo ou fluxo funcional**.

**Architecture:** Aplicação Streamlit de arquivo único ([dashboard.py](../../../dashboard.py), ~3070 linhas) que lê um star schema no Supabase (ETL separado em `contaazul_bi/`). As mudanças são cirúrgicas e localizadas: transporte de cookie, camada de logging, tratamento de exceções, escaping de HTML e apresentação da tela de login. Nenhuma função de cálculo (`calc_*`), figura (`fig_*`) ou filtro (`_filtrar_*`) tem sua lógica alterada.

**Tech Stack:** Python 3.12, Streamlit ≥1.55, SQLAlchemy 2.x + psycopg2, bcrypt, Plotly, Supabase (PostgreSQL). Deploy no Streamlit Community Cloud; ETL no GitHub Actions.

## Global Constraints

Aplicam-se a **todas** as tarefas, sem exceção:

- **NÃO** alterar regras de negócio, cálculos, métricas, indicadores ou resultados apresentados.
- **NÃO** alterar o fluxo funcional (páginas, filtros, navegação, ordem de execução em `main()`).
- **NÃO** reescrever a aplicação nem refatorar a arquitetura por completo.
- Toda mudança deve **preservar compatibilidade** com o código existente e ser **incremental e de baixo risco**.
- **Python ≥ 3.12** obrigatório (o código usa f-strings com aspas aninhadas, ex. [dashboard.py:683](../../../dashboard.py#L683)).
- **Streamlit ≥ 1.55** (já em `requirements.txt`).
- Código, comentários, logs e mensagens em **português**.
- **Commits frequentes** — um por tarefa concluída e verificada.
- **Regressão zero de negócio:** antes de qualquer mudança visual/funcional, capturar o baseline de KPIs (Task 0.2) e confirmar que continuam idênticos ao final de cada fase.

**Definição de "verificação" neste projeto:** não há suíte de testes nem linter. A verificação de cada tarefa é feita em **runtime**: subir o app (`streamlit run dashboard.py`), exercitar o fluxo afetado e observar o comportamento, comparando com o baseline quando pertinente.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade | Ação neste plano |
|---------|------------------|------------------|
| [dashboard.py](../../../dashboard.py) | App Streamlit inteiro | Modificado em pontos localizados (cookies, logging, exceções, login) |
| [requirements.txt](../../../requirements.txt) | Dependências do dashboard | + `extra-streamlit-components` |
| [contaazul_bi/logging_utils.py](../../../contaazul_bi/logging_utils.py) | `setup_logging()` (já existe) | Reutilizado pelo dashboard (sem alteração) |
| `docs/superpowers/baseline/` | Prints/valores de referência de KPIs | Criado na Task 0.2 (não versionar imagens pesadas se preferir) |

> **Decisão de escopo:** a extração do monólito em vários arquivos (`auth.py`, `theme.py`, `data.py`) é **explicitamente deixada como Fase 5, prioridade baixa e opcional**, conforme pedido do usuário de não fatiar em muitos arquivos agora.

---

## Visão geral das fases

| Fase | Objetivo | Prioridade | Depende de |
|------|----------|-----------|-----------|
| **0** | Preparação e baseline de regressão | Pré-requisito | — |
| **1** | Estabilidade de sessão (cookie confiável) — resolve "lembrar de mim" e queda de sessão | **Crítica** | Fase 0 |
| **2** | Observabilidade (logging + exceções + erros sanitizados) | **Alta** | Fase 0 |
| **3** | Endurecimento (escaping XSS, cache de DDL) | **Média** | Fase 2 |
| **4** | Redesenho da tela de login | **Alta (percepção)** | Fase 0 (independente das demais) |
| **5** | Higiene de código e extração gradual (opcional) | **Baixa** | — |

Fases 1, 2 e 4 são **independentes entre si** e podem ser paralelizadas em branches separadas. A Fase 3 depende da 2 (usa o `logger`). A ordem recomendada de entrega é 0 → 1 → 2 → 3 → 4 → 5, mas a Fase 4 (login) pode ser antecipada por ter alto valor de percepção e risco baixíssimo.

---

## Fase 0 — Preparação e baseline

### Task 0.1: Branch de trabalho e dependência

**Files:**
- Modify: [requirements.txt](../../../requirements.txt)

- [ ] **Step 1: Criar branch a partir de `main`**

```bash
git checkout main && git pull
git checkout -b melhorias-tecnicas
```

- [ ] **Step 2: Adicionar a dependência do gerenciador de cookies**

Adicionar a linha ao final de `requirements.txt`:

```
extra-streamlit-components>=0.1.71
```

- [ ] **Step 3: Instalar no ambiente local**

Run: `pip install -r requirements.txt`
Expected: instala `extra-streamlit-components` sem conflito com `streamlit>=1.55`.

- [ ] **Step 4: Confirmar runtime Python ≥ 3.12 no Community Cloud**

Verificar/definir a versão de Python do app no painel do Streamlit Community Cloud (ou criar `runtime.txt` se o projeto passar a usá-lo). O `Dockerfile` já fixa `python:3.12-slim`; o objetivo é garantir paridade no Community Cloud.
Expected: Python 3.12+ confirmado (senão as f-strings aninhadas quebram no boot).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "chore: adiciona extra-streamlit-components para cookies confiaveis"
```

### Task 0.2: Baseline de regressão (rede de segurança)

**Files:**
- Create: `docs/superpowers/baseline/README.md` (anotações dos valores)

**Interfaces:**
- Produces: um conjunto de valores/prints de referência usado como critério de "comportamento inalterado" em todas as fases seguintes.

- [ ] **Step 1: Subir o app no baseline (código atual, sem mudanças)**

Run: `streamlit run dashboard.py`

- [ ] **Step 2: Registrar KPIs de referência**

Com um ano/mês/centro fixos (ex.: ano corrente, "Todos os centros", "Todas as categorias"), anotar em `docs/superpowers/baseline/README.md` os valores exibidos em **cada página**: Resumo de Caixa (saldos, runway, projeção 4 meses), Cenários, Receita/Eficiência (MRR), DRE (todas as linhas). Print de tela de cada página ajuda.
Expected: um documento com os números que **devem permanecer idênticos** ao final de cada fase.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/baseline/README.md
git commit -m "docs: registra baseline de KPIs para regressao"
```

---

## Fase 1 — Estabilidade de sessão (Crítica)

**Contexto:** hoje o cookie "remember-me" é gravado injetando `<script>` via `st.html(payload, unsafe_allow_javascript=True)` ([dashboard.py:550-579](../../../dashboard.py#L550-L579)) e lido via `st.context.cookies` ([dashboard.py:860](../../../dashboard.py#L860)). Esse mecanismo é não-confiável (script injetado não executa de forma garantida; `st.context.cookies` só reflete o carregamento inicial). A lógica de auth (selector/validator com hash no banco) **permanece intacta** — trocamos apenas o **transporte** do cookie por um componente bidirecional.

### Task 1.1: Introduzir o CookieManager e ler cookies de forma confiável

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — imports (topo), novo helper, e `main()` ([dashboard.py:2901](../../../dashboard.py#L2901))

**Interfaces:**
- Produces:
  - `_cookie_manager() -> stx.CookieManager` (cacheado com `@st.cache_resource`)
  - `_read_cookie(name: str) -> str | None` — substitui as leituras de `st.context.cookies.get(...)` relativas ao remember-me.

- [ ] **Step 1: Adicionar import**

No topo de [dashboard.py](../../../dashboard.py) (junto aos demais imports, ~linha 20):

```python
import extra_streamlit_components as stx
```

- [ ] **Step 2: Adicionar helpers do cookie manager**

Logo após `_parse_remember_cookie` ([dashboard.py:600](../../../dashboard.py#L600)):

```python
@st.cache_resource
def _cookie_manager() -> "stx.CookieManager":
    # key fixa evita múltiplas instâncias do componente na árvore
    return stx.CookieManager(key="bi_finance_cookies")


def _read_cookie(name: str) -> str | None:
    """Lê um cookie do navegador via componente bidirecional (confiável em reruns)."""
    try:
        return _cookie_manager().get(name)
    except Exception:
        return None
```

- [ ] **Step 3: Montar o CookieManager cedo em `main()`**

Em `main()`, logo no início (antes de `_run_auth()`), garantir que o componente montou lendo todos os cookies uma vez. Substituir a sequência atual de `_flush_cookie_write()` por:

```python
def main() -> None:
    _restore_theme_from_cookie()
    P = _get_palette()
    st.session_state["_active_palette"] = P
    _inject_css(P)
    _inject_theme_js()
    _cookie_manager().get_all()   # monta o componente e popula cookies neste ciclo
    _run_auth()
    data = load_data()
    ...
```

> As chamadas antigas `_flush_cookie_write()` em `main()` ([dashboard.py:2907](../../../dashboard.py#L2907) e [2909](../../../dashboard.py#L2909)) serão removidas nas Tasks 1.2/1.3 junto com a função.

- [ ] **Step 4: Verificação em runtime**

Run: `streamlit run dashboard.py`
Expected: app sobe sem erro de import; tela de login aparece; console sem exceção do componente.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat(auth): adiciona CookieManager bidirecional para leitura de cookies"
```

### Task 1.2: Gravar o cookie remember-me pelo CookieManager

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — `_run_auth` set/clear ([dashboard.py:714-722](../../../dashboard.py#L714-L722)), logout ([dashboard.py:3049-3053](../../../dashboard.py#L3049-L3053)), `_try_restore_session_from_cookie` ([dashboard.py:859-929](../../../dashboard.py#L859-L929))

**Interfaces:**
- Consumes: `_cookie_manager()`, `_read_cookie()` (Task 1.1); `_issue_remember_token`, `_revoke_remember_token` (inalterados).
- Produces: gravação/remoção de cookie via `_cookie_manager().set(...)` / `.delete(...)`.

- [ ] **Step 1: Substituir a gravação no sucesso do login**

Em `_run_auth`, no bloco `if ok:` ([dashboard.py:713-722](../../../dashboard.py#L713-L722)), trocar as chamadas `_queue_cookie_write(...)` por:

```python
            if ok:
                remember_selector = None
                cm = _cookie_manager()
                if remember_me and remember_me_available:
                    issued = _issue_remember_token(username)
                    if issued:
                        remember_selector, remember_cookie = issued
                        cm.set(
                            REMEMBER_COOKIE_NAME, remember_cookie,
                            max_age=REMEMBER_ME_MAX_AGE_SECONDS,
                            same_site="lax", secure=True, key="set_remember",
                        )
                else:
                    _revoke_remember_token()
                    cm.delete(REMEMBER_COOKIE_NAME, key="del_remember")
                # ...restante do bloco (_set_authenticated_session, zerar tentativas, st.rerun) inalterado...
```

- [ ] **Step 2: Substituir a gravação no logout**

No botão "Sair" ([dashboard.py:3049-3053](../../../dashboard.py#L3049-L3053)):

```python
            if st.button("Sair", use_container_width=True):
                _revoke_remember_token()
                _cookie_manager().delete(REMEMBER_COOKIE_NAME, key="del_remember_logout")
                _clear_authenticated_session()
                st.rerun()
```

- [ ] **Step 3: Ajustar a restauração via cookie para usar `_read_cookie` e o novo set**

Em `_try_restore_session_from_cookie` ([dashboard.py:859-929](../../../dashboard.py#L859-L929)):
- trocar `st.context.cookies.get(REMEMBER_COOKIE_NAME)` (linha 860) por `_read_cookie(REMEMBER_COOKIE_NAME)`;
- trocar os `_queue_cookie_write("clear")` por `_cookie_manager().delete(REMEMBER_COOKIE_NAME, key="del_restore")`;
- trocar o `_queue_cookie_write("set", ...)` final (linha 928) por:

```python
    _cookie_manager().set(
        REMEMBER_COOKIE_NAME, f"{selector}.{new_validator}",
        max_age=REMEMBER_ME_MAX_AGE_SECONDS,
        same_site="lax", secure=True, key="set_restore",
    )
    return True
```

Também em `_revoke_remember_token` ([dashboard.py:847](../../../dashboard.py#L847)), trocar `st.context.cookies.get(...)` por `_read_cookie(...)`.

- [ ] **Step 4: Verificação em runtime (o teste central do plano)**

Run: `streamlit run dashboard.py` (com `DATABASE_URL` configurada — remember-me exige banco).
1. Login **marcando** "Lembrar de mim". Confirmar entrada.
2. Fechar a aba, abrir de novo a URL → **deve entrar direto**, sem pedir senha.
3. Login **sem** marcar → fechar/reabrir → **deve pedir senha**.
4. "Sair" → reabrir → **deve pedir senha**.
Expected: os quatro cenários passam. (Antes desta fase, o cenário 2 falhava.)

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "feat(auth): grava/le remember-me via CookieManager (corrige lembrar de mim)"
```

### Task 1.3: Remover o mecanismo antigo de injeção de `<script>` para cookies

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — remover `_queue_cookie_write` ([dashboard.py:546-547](../../../dashboard.py#L546)) e `_flush_cookie_write` ([dashboard.py:550-579](../../../dashboard.py#L550-L579)); remover as chamadas remanescentes em `_run_auth` ([dashboard.py:659](../../../dashboard.py#L659)) e `main()`.

**Interfaces:**
- Consumes: nada novo. Remove código morto após Task 1.2.

- [ ] **Step 1: Remover funções e chamadas órfãs**

Excluir `_queue_cookie_write` e `_flush_cookie_write`. Remover a chamada `_flush_cookie_write()` em [dashboard.py:659](../../../dashboard.py#L659) e as duas em `main()`. Fazer uma busca por `_queue_cookie_write` e `_flush_cookie_write` para garantir zero referências restantes.

- [ ] **Step 2: Verificação de referências**

Run: `grep -n "_queue_cookie_write\|_flush_cookie_write" dashboard.py`
Expected: **nenhuma** ocorrência.

- [ ] **Step 3: Verificação em runtime (regressão)**

Run: `streamlit run dashboard.py` → repetir os 4 cenários da Task 1.2 + navegar nas 4 páginas.
Expected: tudo funciona; KPIs idênticos ao baseline (Task 0.2).

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "refactor(auth): remove injecao manual de script para cookies (codigo morto)"
```

> **Nota (fora de escopo desta fase):** o cookie de **tema** (`_inject_theme_js`, [dashboard.py:419-441](../../../dashboard.py#L419)) usa o mesmo truque de `<script>`. Não é crítico (só afeta detecção de tema "sistema"). Migrá-lo para o CookieManager é candidato à Fase 5.

---

## Fase 2 — Observabilidade (Alta)

**Contexto:** o dashboard não usa `logging` (0 ocorrências) e tem 19 blocos `except Exception` que engolem o erro. O ETL já tem `setup_logging()` pronto em [contaazul_bi/logging_utils.py](../../../contaazul_bi/logging_utils.py). Objetivo: registrar erros **sem mudar o comportamento** (os fallbacks continuam iguais).

### Task 2.1: Inicializar logging no dashboard

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — imports/topo

**Interfaces:**
- Produces: `logger = logging.getLogger("dashboard")`, disponível para todas as tarefas seguintes.

- [ ] **Step 1: Adicionar setup de logging no topo**

Após os imports e antes de `st.set_page_config` (~linha 22):

```python
import logging
from contaazul_bi.logging_utils import setup_logging

setup_logging(os.environ.get("LOG_LEVEL", "INFO"))  # idempotente (force=True)
logger = logging.getLogger("dashboard")
```

- [ ] **Step 2: Verificação em runtime**

Run: `streamlit run dashboard.py`
Expected: no stdout aparece pelo menos uma linha no formato `... | INFO | dashboard | ...` quando você adicionar um `logger.info("dashboard iniciado")` temporário (remova depois) — ou simplesmente confirmar que o import não quebra e o app sobe.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat(obs): inicializa logging no dashboard reutilizando logging_utils do ETL"
```

### Task 2.2: Registrar exceções nos `except` silenciosos

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — os blocos `except Exception` em, ao menos: `_load_cenarios` ([473](../../../dashboard.py#L473)), `_current_user_agent` ([589](../../../dashboard.py#L589)), `_ensure_auth_storage` ([799](../../../dashboard.py#L799), [801](../../../dashboard.py#L801)), `_get_palette`/`_restore_theme_from_cookie` ([70](../../../dashboard.py#L70)/[83](../../../dashboard.py#L83)), `_ga_get_latest_run` ([2806](../../../dashboard.py#L2806)), `_ga_dispatch` ([2821](../../../dashboard.py#L2821)), `_load_from_database`/`load_data`, `_is_admin` ([2875](../../../dashboard.py#L2875)).

**Interfaces:**
- Consumes: `logger` (Task 2.1).

- [ ] **Step 1: Trocar swallow silencioso por log com stack**

Padrão de substituição (o `return`/fallback **não muda**, só ganha uma linha de log). Exemplo em `_load_cenarios` ([473-474](../../../dashboard.py#L473)):

```python
        except Exception:
            logger.exception("Falha ao carregar cenários do banco; usando default")
            # (o fluxo segue para 'return default' exatamente como antes)
```

Aplicar o mesmo padrão a cada bloco listado, com mensagem específica ao contexto. **Regra:** onde hoje é `except: pass`, vira `except: logger.exception("<contexto>")` **mantendo** qualquer `return`/`pass` subsequente. Não capturar exceções novas nem alterar o que é retornado.

> Exceções que são fluxo de controle esperado e barulhento (ex.: leitura de `st.context` antes do request) podem usar `logger.debug(...)` em vez de `logger.exception(...)` para não poluir. Use `debug` em `_current_user_agent`, `_get_palette`, `_restore_theme_from_cookie`; use `exception` no resto.

- [ ] **Step 2: Verificação de cobertura**

Run: `grep -n "except Exception" dashboard.py`
Expected: cada ocorrência tem, na linha seguinte, um `logger.` (exception/debug) — nenhum `pass`/`return` "nu" sem log associado.

- [ ] **Step 3: Verificação em runtime**

Provocar um erro controlado (ex.: apontar `DATABASE_URL` para um host inválido temporariamente) e confirmar que aparece stack trace no stdout **e** que o app ainda cai no fallback esperado (Parquet/local) sem quebrar.
Expected: log com stack + comportamento de fallback idêntico ao atual.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat(obs): loga excecoes antes dos fallbacks (sem mudar comportamento)"
```

### Task 2.3: Sanitizar mensagens de erro exibidas ao usuário

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — `_save_cenarios` ([509](../../../dashboard.py#L509))

- [ ] **Step 1: Não expor a exceção crua na UI**

Substituir [dashboard.py:508-509](../../../dashboard.py#L508):

```python
        except Exception:
            logger.exception("Erro ao salvar cenários no banco")
            st.error("Não foi possível salvar os cenários agora. Tente novamente ou contate o suporte.")
```

- [ ] **Step 2: Verificação em runtime**

Simular falha de escrita (banco indisponível) ao salvar um cenário.
Expected: usuário vê a mensagem genérica; o stack completo vai só para o log.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "fix(sec): nao expoe excecao crua do banco na UI ao salvar cenarios"
```

---

## Fase 3 — Endurecimento (Média)

### Task 3.1: Escapar valores dinâmicos em HTML (superfície de XSS)

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — pontos com `unsafe_allow_html=True` que interpolam dados vindos do banco/Conta Azul (nomes de categoria/cliente, `_display_name` em [3046](../../../dashboard.py#L3046)).

**Interfaces:**
- Produces: uso de `html.escape(...)` em qualquer string dinâmica interpolada em HTML.

- [ ] **Step 1: Import**

No topo: `import html` (biblioteca padrão).

- [ ] **Step 2: Escapar o nome de exibição no rodapé da sidebar**

Em [dashboard.py:3042](../../../dashboard.py#L3042):

```python
            nome_exib = html.escape(str(st.session_state.get("_display_name", st.session_state["_username"])))
```

- [ ] **Step 3: Escapar nomes vindos de dados em rankings/KPIs renderizados como HTML**

Auditar `_ranking_categorias_despesas` ([1733](../../../dashboard.py#L1733)), `_ranking_clientes_cr` ([1778](../../../dashboard.py#L1778)) e qualquer `kpi_card(...)`/`st.markdown(..., unsafe_allow_html=True)` que injete `nome`/`label` derivado de dados. Envolver o valor dinâmico em `html.escape(...)` **apenas na fronteira de renderização** (não alterar os DataFrames nem os cálculos).

- [ ] **Step 4: Verificação em runtime**

Run: app + navegar em Resumo (rankings) e conferir que os nomes aparecem normalmente (escaping não muda texto legível). Se possível, testar um nome com caractere `<`/`&` e confirmar que renderiza como texto, não como HTML.
Expected: rótulos idênticos ao baseline; nenhum HTML interpretado a partir de dados.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "fix(sec): escapa valores dinamicos em HTML para fechar superficie de XSS"
```

### Task 3.2: Cachear a DDL de `_ensure_auth_storage`

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — `_ensure_auth_storage` ([755](../../../dashboard.py#L755))

- [ ] **Step 1: Rodar a DDL uma vez por processo**

Adicionar o decorator `@st.cache_resource` acima de `def _ensure_auth_storage()` ([dashboard.py:755](../../../dashboard.py#L755)). O corpo permanece idêntico (CREATE TABLE IF NOT EXISTS / INDEX / RLS já são idempotentes; o cache só evita repetir a cada rerun).

- [ ] **Step 2: Verificação em runtime**

Run: app + login. Confirmar que o remember-me continua disponível e funcional (a função ainda retorna `True` quando há banco).
Expected: comportamento idêntico; menos idas ao banco por rerun.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "perf(auth): cacheia DDL de _ensure_auth_storage (uma vez por processo)"
```

### Task 3.3 (opcional): Lockout de login persistente no banco

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — `_run_auth` lockout ([665-740](../../../dashboard.py#L665-L740)); reutiliza a infra de `_ensure_auth_storage`.

> **Status:** opcional / prioridade Média-Fase 2. O lockout atual vive só em `session_state`, então é contornável reconectando. Persistir por `username` no banco fecha isso, mas é a mudança mais invasiva desta fase. **Só implementar se o time decidir que vale** — descreve-se aqui para completude.

- [ ] **Step 1: Tabela de tentativas** — criar `bi_login_attempts (username TEXT PK, attempts INT, locked_until TIMESTAMPTZ)` dentro de `_ensure_auth_storage` (mesmo padrão de RLS da `bi_remember_tokens`).
- [ ] **Step 2: Ler/escrever contador no banco** em vez de `session_state`, mantendo os mesmos limites (`_MAX_ATTEMPTS = 5`, `_LOCKOUT_SECONDS = 300`) e as **mesmas mensagens** ao usuário.
- [ ] **Step 3: Verificação** — 5 tentativas erradas bloqueiam; abrir nova aba **não** zera o bloqueio.
- [ ] **Step 4: Commit** — `feat(sec): persiste lockout de login no banco`.

---

## Fase 4 — Redesenho da tela de login (Alta percepção, risco baixíssimo)

**Contexto:** ver mockup e análise. A **lógica de autenticação não muda** — apenas apresentação (CSS + card) e, opcionalmente, `st.button` → `st.form_submit_button` para "enviar com Enter".

### Task 4.1: CSS específico do login

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — final de `_inject_css` ([89-416](../../../dashboard.py#L89-L416))

- [ ] **Step 1: Adicionar bloco de estilos do login**

Antes do fechamento `</style>` em `_inject_css`, acrescentar (usa as cores de `P`, então light/dark já funcionam):

```python
.login-card {{
    max-width: 380px; margin: 6vh auto 0; padding: 40px 36px;
    background: {P['BG_CARD']}; border: 1px solid {P['BORDER']};
    border-radius: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 12px 40px rgba(0,0,0,.10);
}}
.login-brand {{ text-align:center; margin-bottom: 22px; }}
.login-brand .logo {{ font-size: 2rem; font-weight: 800; letter-spacing:-.03em; color:{P['BLUE']}; }}
.login-brand .prod {{ font-size:.72rem; letter-spacing:.18em; text-transform:uppercase;
    color:{P['TEXT_SECONDARY']}; font-weight:600; margin-top:4px; }}
.stApp [data-testid="stTextInput"] input {{
    background:{P['BG_APP']} !important; border:1px solid {P['BORDER']} !important;
    border-radius:10px !important; color:{P['TEXT_PRIMARY']} !important;
}}
.stApp [data-testid="stTextInput"] input:focus {{
    border-color:{P['BLUE']} !important; box-shadow:0 0 0 3px rgba(0,70,150,.22) !important;
}}
```

- [ ] **Step 2: Verificação em runtime**

Run: app (deslogado) → o CSS carrega antes do login (já é chamado em `main()` antes de `_run_auth`).
Expected: sem erro; estilos disponíveis (visível após Task 4.2).

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "style(login): adiciona CSS do card de login (light/dark via paleta)"
```

### Task 4.2: Reestruturar o layout do formulário de login

**Files:**
- Modify: [dashboard.py](../../../dashboard.py) — bloco de apresentação em `_run_auth` ([678-742](../../../dashboard.py#L678-L742))

**Interfaces:**
- Consumes: classe `.login-card`/`.login-brand` (Task 4.1).
- **Preserva:** todo o corpo `if submitted:` (validação bcrypt, lockout, remember, `_set_authenticated_session`, `st.rerun`) — **idêntico** ao atual (linhas [701-740](../../../dashboard.py#L701-L740)).

- [ ] **Step 1: Trocar cabeçalho de texto pelo card + wordmark e envolver em `st.form`**

Substituir [dashboard.py:678-700](../../../dashboard.py#L678-L700) por:

```python
    _, col, _ = st.columns([1, 1.15, 1])
    with col:
        remember_me_available = _ensure_auth_storage()
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown(
            "<div class='login-brand'>"
            "<div class='logo'>Scua</div>"
            "<div class='prod'>Finance · BI</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            username = st.text_input("Usuário", key="_login_username")
            password = st.text_input("Senha", type="password", key="_login_password")
            remember_me = st.checkbox(
                f"Lembrar de mim neste navegador por {REMEMBER_ME_DAYS} dias",
                value=False, disabled=not remember_me_available,
                help="Mantém este navegador autorizado mesmo após fechar a aba. "
                     "Evite usar em máquinas compartilhadas.",
            )
            submitted = st.form_submit_button("Entrar", use_container_width=True, type="primary")
        if not remember_me_available:
            st.caption("O recurso de lembrar de mim só fica disponível quando o banco está configurado.")
```

- [ ] **Step 2: Ligar o corpo do login ao `submitted` e fechar o card**

Trocar `if st.button("Entrar", ...):` ([dashboard.py:700](../../../dashboard.py#L700)) por `if submitted:` — **o restante do bloco permanece igual**. Após o bloco, antes de `st.stop()`, fechar o card:

```python
        st.markdown('</div>', unsafe_allow_html=True)
    st.stop()
```

- [ ] **Step 3: Verificação em runtime (funcional + visual)**

Run: `streamlit run dashboard.py`
1. Login correto → entra. 2. Login errado → mensagem + contador. 3. 5 erros → lockout 5 min. 4. Enter no campo de senha **envia** o form. 5. Alternar tema claro/escuro → card legível nos dois.
Expected: comportamento de auth **idêntico** ao baseline; visual do card aplicado.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "style(login): card centralizado, wordmark e st.form (Enter envia)"
```

### Task 4.3 (opcional): Logo oficial

**Files:**
- Create: `assets/logo_scua.png` (asset fornecido pelo time)
- Modify: [dashboard.py](../../../dashboard.py) — `.login-brand`

- [ ] **Step 1:** Se o time fornecer o logo, colocá-lo em `assets/logo_scua.png` (path já esperado por `LOGO_PATH`, [dashboard.py:445](../../../dashboard.py#L445)) e trocar o wordmark textual por `st.image(str(LOGO_PATH), width=140)` dentro do `.login-brand`, com fallback para o texto se `LOGO_PATH` não existir.
- [ ] **Step 2:** Verificar render em runtime. **Step 3:** Commit `style(login): usa logo oficial da Scua quando disponivel`.

---

## Fase 5 — Higiene de código e extração gradual (Baixa, opcional)

> Tudo aqui é **opcional e incremental**. Fazer **uma tarefa por PR**, cada uma validando igualdade de resultado contra o baseline. Nenhuma altera cálculo.

### Task 5.1: Unificar filtros duplicados

**Files:** [dashboard.py](../../../dashboard.py) — `_filtrar_centro`/`_filtrar_centro_real` ([1155-1171](../../../dashboard.py#L1155)), `_filtrar_categoria`/`_filtrar_categoria_real` ([1173-1186](../../../dashboard.py#L1173))

- [ ] **Step 1:** Extrair a coluna variável como parâmetro, mantendo wrappers finos com os nomes atuais (compatibilidade). **Step 2:** Verificar que cada página produz KPIs idênticos ao baseline. **Step 3:** Commit `refactor: unifica _filtrar_* mantendo wrappers`.

### Task 5.2: Centralizar `PAID_STATUSES`

**Files:** `contaazul_bi/config.py`, `contaazul_bi/main.py`, `contaazul_bi/transformers/analytics.py`

- [ ] **Step 1:** Confirmar com o negócio **qual** conjunto é o correto (hoje `main.py` inclui `"ACQUITTED"` e `analytics.py` não). **Step 2:** Definir a constante única em `config.py` e importar nos dois lugares — **sem mudar o comportamento vigente em produção** (se os conjuntos divergentes forem intencionais, documentar e NÃO unificar). **Step 3:** Rodar o ETL em ambiente de teste e comparar tabelas. **Step 4:** Commit.

> ⚠️ Esta é a única tarefa que toca o ETL e que **pode** afetar resultado se o conjunto "correto" for escolhido errado. Tratar com cuidado extra e validação de dados.

### Task 5.3: Migrar cookie de tema para o CookieManager

**Files:** [dashboard.py](../../../dashboard.py) — `_inject_theme_js` ([419](../../../dashboard.py#L419)), `_get_palette` ([62](../../../dashboard.py#L62)), `_restore_theme_from_cookie` ([77](../../../dashboard.py#L77))

- [ ] Substituir a escrita/leitura dos cookies `app_theme_pref`/`app_dark_scheme` pelo `_cookie_manager()`, eliminando o último uso de `<script>` injetado. Verificar troca de tema persistente entre sessões. Commit.

### Task 5.4: Extração modular gradual

**Files:** criar `auth.py`, depois `theme.py`, depois `data.py`; [dashboard.py](../../../dashboard.py) passa a importar.

- [ ] Uma extração por PR, **preservando assinaturas públicas**. Ordem sugerida: `auth.py` (linhas [530-929](../../../dashboard.py#L530-L929)) → `theme.py` ([30-441](../../../dashboard.py#L30-L441)) → `data.py` ([747-1030](../../../dashboard.py#L747-L1030)). Verificar boot + navegação após cada uma. Commits separados.

---

## Self-Review (cobertura do escopo)

- **"Lembrar de mim" quebrado** → Fase 1 (Tasks 1.1–1.3). ✔
- **Sessão expira com frequência** → resolvido por Fase 1 (restauração via cookie confiável). ✔
- **Logging / stack traces / rastreamento de erros** → Fase 2. ✔
- **Exceções exibidas ao usuário** → Task 2.3. ✔
- **Autenticação/autorização/sessão/JWT** → esclarecido (não há JWT; sessão = `session_state` + remember-me); endurecimento em Fases 1 e 3. ✔
- **Exposição de dados sensíveis / XSS** → Task 3.1. ✔
- **Session State / cache Streamlit** → Task 3.2 (DDL cacheada) + Fase 1. ✔
- **Tela de login (visual + UX + responsividade)** → Fase 4. ✔
- **Qualidade: duplicação, legibilidade, organização** → Fase 5. ✔
- **Arquitetura (fatiamento gradual, prioridade baixa)** → Task 5.4. ✔

**Restrições globais respeitadas:** nenhuma tarefa altera `calc_*`/`fig_*`/regras de negócio; a única que toca o ETL (5.2) é explicitamente marcada como sensível e condicionada à validação. Cada fase termina com verificação contra o baseline da Task 0.2.
