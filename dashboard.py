"""
Dashboard Financeiro – Scua
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import uuid
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text

# ─── Configuracao da pagina ────────────────────────────────────────────────────
st.set_page_config(
    page_title="BI Finance – Scua",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Paleta ───────────────────────────────────────────────────────────────────
BG_APP         = "#f5f5f7"
BG_CARD        = "#ffffff"
BG_SIDEBAR     = "#ffffff"
BLUE           = "#004696"
ORANGE         = "#eb6b06"
TEXT_PRIMARY   = "#1d1d1f"
TEXT_SECONDARY = "#6e6e73"
BORDER         = "#d2d2d7"
GREEN          = "#1a7f4b"
GREEN_LIGHT    = "#28a865"
RED            = "#c0392b"
RED_LIGHT      = "#e74c3c"
WHITE          = "#ffffff"

_PROJECT_ROOT = Path(__file__).parent
LOGO_PATH = _PROJECT_ROOT / "assets" / "logo_scua.png"
CENARIOS_PATH = _PROJECT_ROOT / "output" / "cenarios.json"
REMEMBER_COOKIE_NAME = "bi_finance_remember"
SESSION_TIMEOUT_SECONDS = 8 * 3600
try:
    REMEMBER_ME_DAYS = max(1, int(os.environ.get("BI_REMEMBER_ME_DAYS", "30")))
except ValueError:
    REMEMBER_ME_DAYS = 30
REMEMBER_ME_MAX_AGE_SECONDS = REMEMBER_ME_DAYS * 24 * 3600


def _load_cenarios() -> dict:
    """Carrega cenários do Supabase (produção) ou do arquivo JSON local (desenvolvimento)."""
    default: dict = {"projecoes": [], "renovacao_ativa": True, "contratos_excluidos": []}

    engine = _get_db_engine()
    if engine is not None:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT projecoes, renovacao_ativa, contratos_excluidos FROM bi_cenarios WHERE id = 1")
                ).mappings().fetchone()
            if row:
                return {
                    "projecoes": row["projecoes"] if isinstance(row["projecoes"], list) else json.loads(row["projecoes"] or "[]"),
                    "renovacao_ativa": bool(row["renovacao_ativa"]),
                    "contratos_excluidos": row["contratos_excluidos"] if isinstance(row["contratos_excluidos"], list) else json.loads(row["contratos_excluidos"] or "[]"),
                }
        except Exception:
            pass
        return default

    # Fallback: arquivo local (desenvolvimento)
    if not CENARIOS_PATH.exists():
        return default
    try:
        return json.loads(CENARIOS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_cenarios(c: dict) -> None:
    """Salva cenários no Supabase (produção) ou em arquivo JSON local (desenvolvimento)."""
    engine = _get_db_engine()
    if engine is not None:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO bi_cenarios (id, projecoes, renovacao_ativa, contratos_excluidos, atualizado_em)
                        VALUES (1, CAST(:p AS jsonb), :r, CAST(:e AS jsonb), NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            projecoes           = EXCLUDED.projecoes,
                            renovacao_ativa     = EXCLUDED.renovacao_ativa,
                            contratos_excluidos = EXCLUDED.contratos_excluidos,
                            atualizado_em       = NOW()
                    """),
                    {
                        "p": json.dumps(c.get("projecoes", []), ensure_ascii=False, default=str),
                        "r": bool(c.get("renovacao_ativa", True)),
                        "e": json.dumps(c.get("contratos_excluidos", []), ensure_ascii=False, default=str),
                    },
                )
        except Exception as exc:
            st.error(f"Erro ao salvar cenários no banco: {exc}")
        return

    # Fallback: arquivo local (desenvolvimento)
    CENARIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CENARIOS_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _extrair_nomes_categoria(data: dict) -> list[str]:
    nomes: set[str] = set()
    for df in [data["cr"], data["cp"]]:
        for val in df["categorias"].dropna():
            try:
                for item in val:
                    if isinstance(item, dict) and item.get("nome"):
                        nomes.add(item["nome"])
            except TypeError:
                pass
    return sorted(nomes)


def _set_authenticated_session(username: str, display_name: str, remember_selector: str | None = None) -> None:
    st.session_state["_authenticated"] = True
    st.session_state["_username"] = username
    st.session_state["_display_name"] = display_name
    st.session_state["_login_ts"] = time.time()
    if remember_selector:
        st.session_state["_remember_selector"] = remember_selector
    else:
        st.session_state.pop("_remember_selector", None)


def _clear_authenticated_session() -> None:
    for key in ["_authenticated", "_username", "_display_name", "_login_ts", "_remember_selector"]:
        st.session_state.pop(key, None)


def _queue_cookie_write(action: str, value: str | None = None) -> None:
    st.session_state["_auth_cookie_op"] = {"action": action, "value": value or ""}


def _flush_cookie_write() -> None:
    op = st.session_state.pop("_auth_cookie_op", None)
    if not op:
        return

    cookie_name = json.dumps(REMEMBER_COOKIE_NAME)
    if op["action"] == "set":
        cookie_value = json.dumps(op["value"])
        payload = f"""
<div style="display:none"></div>
<script>
(() => {{
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = {cookie_name} + "=" + encodeURIComponent({cookie_value}) +
    "; path=/; max-age={REMEMBER_ME_MAX_AGE_SECONDS}; SameSite=Lax" + secure;
}})();
</script>
"""
    else:
        payload = f"""
<div style="display:none"></div>
<script>
(() => {{
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = {cookie_name} +
    "=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax" + secure;
}})();
</script>
"""
    st.html(payload, unsafe_allow_javascript=True)


def _hash_remember_validator(validator: str) -> str:
    return hashlib.sha256(validator.encode("utf-8")).hexdigest()


def _current_user_agent() -> str:
    try:
        return str(st.context.headers.get("User-Agent", ""))[:512]
    except Exception:
        return ""


def _parse_remember_cookie(raw_cookie: str | None) -> tuple[str, str] | None:
    if not raw_cookie:
        return None
    decoded = urllib.parse.unquote(str(raw_cookie)).strip()
    selector, sep, validator = decoded.partition(".")
    if not sep or not selector or not validator:
        return None
    return selector, validator

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

* {{ box-sizing: border-box; }}

.stApp {{
    background-color: {BG_APP} !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}
/* Padding-top maior para nao sobrepor o header fixo */
.block-container {{
    padding: 5rem 1.5rem 3rem 1.5rem !important;
    max-width: 100% !important;
}}
/* Colunas do Streamlit: remove gap padrao excessivo */
[data-testid="stHorizontalBlock"] {{
    gap: 0.5rem !important;
}}
header[data-testid="stHeader"] {{
    background-color: rgba(245,245,247,0.95) !important;
    backdrop-filter: blur(12px);
    border-bottom: 1px solid {BORDER} !important;
}}
section[data-testid="stSidebar"] {{
    background-color: {BG_SIDEBAR} !important;
    border-right: 1px solid {BORDER} !important;
    font-family: 'Inter', -apple-system, sans-serif;
}}
/* NAO usar * no sidebar — quebra o font de icones do Streamlit */
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span:not([data-testid]),
section[data-testid="stSidebar"] div[class*="label"],
section[data-testid="stSidebar"] label {{
    color: {TEXT_PRIMARY};
    font-family: 'Inter', -apple-system, sans-serif;
}}
section[data-testid="stSidebar"] .stRadio > div {{
    gap: 2px;
}}
section[data-testid="stSidebar"] .stRadio label {{
    color: {TEXT_SECONDARY} !important;
    font-size: 0.875rem !important;
    font-weight: 500;
    padding: 8px 10px !important;
    border-radius: 8px;
    transition: background 0.15s;
}}
section[data-testid="stSidebar"] .stSelectbox > div > div {{
    background-color: {BG_APP} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 8px !important;
    color: {TEXT_PRIMARY} !important;
    font-size: 0.875rem;
}}
section[data-testid="stSidebar"] .stSelectbox label {{
    color: {TEXT_SECONDARY} !important;
    font-size: 0.72rem !important;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
section[data-testid="stSidebar"] .stSelectbox svg {{
    fill: {TEXT_SECONDARY} !important;
}}

/* Cards KPI — container queries para escalar com a largura real do card */
.kpi-card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 16px 14px 14px 14px;
    margin-bottom: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.06);
    transition: box-shadow 0.2s ease, transform 0.2s ease;
    min-width: 0;
    overflow: visible;
    position: relative;
    /* Declara o card como container para que cqw funcione nos filhos */
    container-type: inline-size;
    container-name: kpi;
}}
.kpi-card:hover {{
    box-shadow: 0 2px 8px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.10);
    transform: translateY(-2px);
}}
.kpi-label {{
    /*
     * cqw = 1% da largura do card (nao do viewport).
     * Sidebar fechado: card ~225px → 5.5cqw ≈ 12.4px → capped 0.70rem
     * Sidebar aberto:  card ~175px → 5.5cqw ≈  9.6px → ~0.60rem
     */
    font-size: clamp(0.58rem, 5.5cqw, 0.70rem);
    color: {TEXT_SECONDARY};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}
.kpi-value {{
    /*
     * Sidebar fechado: card ~225px → 10.7cqw ≈ 24px = 1.50rem (maximo)
     * Sidebar aberto:  card ~175px → 10.7cqw ≈ 18.7px ≈ 1.17rem
     */
    font-size: clamp(0.90rem, 10.7cqw, 1.50rem);
    font-weight: 700;
    color: {TEXT_PRIMARY};
    line-height: 1.15;
    letter-spacing: -0.02em;
    overflow-wrap: break-word;
    word-break: break-word;
}}
.kpi-value.positivo {{ color: {GREEN}; }}
.kpi-value.negativo {{ color: {RED}; }}
.kpi-value.neutro   {{ color: {BLUE}; }}

/* Barra de filtros */
.filter-bar {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 10px 18px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}}
.filter-bar-label {{
    font-size: 0.70rem;
    color: {TEXT_SECONDARY};
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-right: 6px;
}}
.filter-tag {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: {BG_APP};
    border: 1px solid {BORDER};
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 0.80rem;
    font-weight: 500;
}}
.filter-tag .label {{ color: {TEXT_SECONDARY}; font-size: 0.72rem; }}
.filter-tag .value {{ color: {BLUE}; font-weight: 600; }}

/* Titulos */
.page-title {{
    font-size: 1.60rem;
    font-weight: 700;
    color: {TEXT_PRIMARY};
    margin: 0 0 2px 0;
    letter-spacing: -0.02em;
}}
.page-subtitle {{
    font-size: 0.875rem;
    color: {TEXT_SECONDARY};
    margin-bottom: 20px;
}}

/* Container nativo do Plotly no Streamlit */
[data-testid="stPlotlyChart"] {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
    padding: 8px 12px 4px 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.06);
    margin-bottom: 12px;
    overflow: hidden !important;
}}
[data-testid="stPlotlyChart"] > div {{
    overflow: hidden !important;
}}
/* Esconde scrollbar de qualquer container interno do Streamlit */
[data-testid="stPlotlyChart"] ::-webkit-scrollbar {{
    display: none !important;
}}

/* Divider */
.divider {{
    height: 1px;
    background: {BORDER};
    margin: 14px 0;
}}

/* Warn box */
.warn-box {{
    background: #fff8f0;
    border: 1px solid #fcd3a8;
    border-left: 3px solid {ORANGE};
    border-radius: 10px;
    padding: 11px 16px;
    font-size: 0.82rem;
    color: {TEXT_PRIMARY};
    margin-bottom: 16px;
}}

/* Sidebar estrutura */
.sidebar-section {{
    font-size: 0.68rem;
    color: {TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    margin: 18px 0 6px 0;
}}
.sidebar-subtitle {{
    font-size: 0.72rem;
    color: {TEXT_SECONDARY};
    margin-bottom: 4px;
}}

/* Checkboxes compactos na sidebar */
section[data-testid="stSidebar"] .stCheckbox label p {{
    font-size: 0.80rem !important;
    color: {TEXT_PRIMARY} !important;
    line-height: 1.3;
}}
section[data-testid="stSidebar"] .stCheckbox {{
    margin-bottom: 0px !important;
    padding: 1px 0 !important;
}}

/* Expander de categorias — nao tocar em SVG/icones */
section[data-testid="stSidebar"] .stExpander {{
    border: 1px solid {BORDER} !important;
    border-radius: 8px !important;
    background: {BG_APP} !important;
}}
section[data-testid="stSidebar"] .stExpander summary p {{
    font-size: 0.875rem !important;
    color: {TEXT_PRIMARY} !important;
    font-weight: 500;
}}
section[data-testid="stSidebar"] .stTextInput input {{
    font-size: 0.82rem !important;
    padding: 6px 10px !important;
    border-radius: 7px !important;
    border: 1px solid {BORDER} !important;
    background: {BG_CARD} !important;
    color: {TEXT_PRIMARY} !important;
}}
.kpi-info {{ position: relative; flex-shrink: 0; margin-left: 4px; }}
.kpi-info > summary {{
    list-style: none; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    width: 15px; height: 15px; border-radius: 50%;
    border: 1.5px solid #a0a0a8; color: #a0a0a8;
    font-size: 0.58rem; font-weight: 700; font-style: italic;
    user-select: none; margin-top: 1px;
    transition: color 0.15s, border-color 0.15s;
}}
.kpi-info > summary::-webkit-details-marker {{ display: none; }}
.kpi-info > summary:hover {{ color: {BLUE}; border-color: {BLUE}; }}
.kpi-info > .kpi-info-box {{ display: none; }}
.kpi-info[open] > .kpi-info-box {{
    display: block; position: absolute;
    right: 0; top: calc(100% + 4px); z-index: 9999;
    background: #fff; border: 1px solid #d2d2d7; border-radius: 10px;
    padding: 10px 12px; min-width: 200px; max-width: 260px;
    font-size: 0.70rem; line-height: 1.55; color: #3d3d3f;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    white-space: normal; font-weight: 400;
    text-transform: none; letter-spacing: 0;
}}
</style>
""", unsafe_allow_html=True)

# ─── Constantes ────────────────────────────────────────────────────────────────
MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Marco", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}
MESES_ABREV = {
    1: "jan", 2: "fev", 3: "mar", 4: "abr",
    5: "mai", 6: "jun", 7: "jul", 8: "ago",
    9: "set", 10: "out", 11: "nov", 12: "dez",
}


# ─── Autenticação ─────────────────────────────────────────────────────────────
def _run_auth() -> None:
    """
    Exibe o formulário de login se o usuário não estiver autenticado.

    Credenciais são lidas de .streamlit/secrets.toml (local) ou dos secrets
    configurados no painel do serviço de hospedagem (produção):

        [credentials.thiago]
        name         = "Thiago Carvalho"
        password_hash = "$2b$12$..."   # bcrypt hash

    Se as credenciais não puderem ser carregadas, o acesso é negado (fail closed).
    """
    import bcrypt as _bcrypt
    session_expired = False

    if st.session_state.get("_authenticated"):
        login_ts = st.session_state.get("_login_ts", 0.0)
        if time.time() - login_ts > SESSION_TIMEOUT_SECONDS:
            _clear_authenticated_session()
            session_expired = True
        else:
            return

    # Tenta carregar credenciais do secrets.toml / variáveis de ambiente
    try:
        creds = st.secrets["credentials"]
        creds_dict = dict(creds)
        has_creds = len(creds_dict) > 0
    except Exception:
        creds_dict = {}
        has_creds = False

    if not has_creds:
        # Credenciais ausentes ou ilegíveis: bloqueia acesso (fail closed)
        st.error(
            "Credenciais de acesso não configuradas. "
            "Configure `[credentials]` em secrets.toml antes de usar o dashboard."
        )
        st.stop()

    if _try_restore_session_from_cookie(creds_dict):
        return
    _flush_cookie_write()

    # ── Lockout por tentativas excessivas ─────────────────────────────────────
    _MAX_ATTEMPTS = 5
    _LOCKOUT_SECONDS = 300  # 5 minutos

    attempts = st.session_state.get("_login_attempts", 0)
    locked_until = st.session_state.get("_locked_until", 0.0)
    now = time.time()

    if locked_until > now:
        remaining = int(locked_until - now)
        st.error(f"Muitas tentativas incorretas. Tente novamente em {remaining} segundos.")
        st.stop()

    if session_expired:
        st.info("Sua sessão expirou. Faça login novamente.")

    # ── Tela de login ─────────────────────────────────────────────────────────
    _, col, _ = st.columns([1, 1, 1])
    with col:
        remember_me_available = _ensure_auth_storage()
        st.markdown(
            f"<div style='text-align:center;padding:48px 0 28px 0;'>"
            f"<div style='font-size:1.60rem;font-weight:700;color:{TEXT_PRIMARY};"
            f"letter-spacing:-0.02em;'>BI Finance</div>"
            f"<div style='font-size:0.875rem;color:{TEXT_SECONDARY};margin-top:4px;'>Scua</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        username = st.text_input("Usuário", key="_login_username")
        password = st.text_input("Senha", type="password", key="_login_password")
        remember_me = st.checkbox(
            f"Lembrar de mim neste navegador por {REMEMBER_ME_DAYS} dias",
            value=False,
            disabled=not remember_me_available,
            help="Mantém este navegador autorizado mesmo após fechar a aba. Evite usar em máquinas compartilhadas.",
        )
        if not remember_me_available:
            st.caption("O recurso de lembrar de mim só fica disponível quando o banco de dados está configurado.")

        if st.button("Entrar", use_container_width=True, type="primary"):
            user_data_raw = creds_dict.get(username)
            user_data = dict(user_data_raw) if user_data_raw else None
            ok = False
            if user_data:
                try:
                    ok = _bcrypt.checkpw(
                        password.encode("utf-8"),
                        str(user_data.get("password_hash", "")).encode("utf-8"),
                    )
                except Exception:
                    ok = False

            if ok:
                remember_selector = None
                if remember_me and remember_me_available:
                    issued = _issue_remember_token(username)
                    if issued:
                        remember_selector, remember_cookie = issued
                        _queue_cookie_write("set", remember_cookie)
                else:
                    _revoke_remember_token()
                    _queue_cookie_write("clear")

                _set_authenticated_session(
                    username=username,
                    display_name=str(user_data.get("name", username)),
                    remember_selector=remember_selector,
                )
                st.session_state["_login_attempts"] = 0
                st.session_state["_locked_until"] = 0.0
                st.rerun()
            else:
                attempts += 1
                st.session_state["_login_attempts"] = attempts
                remaining_attempts = _MAX_ATTEMPTS - attempts
                if attempts >= _MAX_ATTEMPTS:
                    st.session_state["_locked_until"] = now + _LOCKOUT_SECONDS
                    st.error(f"Muitas tentativas incorretas. Tente novamente em {_LOCKOUT_SECONDS // 60} minutos.")
                else:
                    st.error(f"Usuário ou senha incorretos. Tentativas restantes: {remaining_attempts}.")

    st.stop()


# ─── Conexão com banco de dados ───────────────────────────────────────────────
@st.cache_resource
def _get_db_engine():
    """Retorna um SQLAlchemy engine para o Supabase, ou None se DATABASE_URL não estiver configurada."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None
    return create_engine(database_url, pool_pre_ping=True)


def _ensure_auth_storage() -> bool:
    engine = _get_db_engine()
    if engine is None:
        return False

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS bi_remember_tokens (
                    selector TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    user_agent TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_bi_remember_tokens_username
                ON bi_remember_tokens (username)
            """))
            try:
                conn.execute(text("ALTER TABLE bi_remember_tokens ENABLE ROW LEVEL SECURITY"))
                conn.execute(text("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                              FROM pg_policies
                             WHERE schemaname = 'public'
                               AND tablename = 'bi_remember_tokens'
                               AND policyname = 'service_role full access on remember tokens'
                        ) THEN
                            CREATE POLICY "service_role full access on remember tokens"
                              ON bi_remember_tokens
                             FOR ALL
                              TO service_role
                           USING (true)
                      WITH CHECK (true);
                        END IF;
                    END
                    $$;
                """))
            except Exception:
                pass
    except Exception:
        return False

    return True


def _issue_remember_token(username: str) -> tuple[str, str] | None:
    engine = _get_db_engine()
    if engine is None or not _ensure_auth_storage():
        return None

    selector = secrets.token_hex(12)
    validator = secrets.token_hex(32)
    token_hash = _hash_remember_validator(validator)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bi_remember_tokens WHERE expires_at <= NOW()"))
        conn.execute(
            text("""
                INSERT INTO bi_remember_tokens (
                    selector, username, token_hash, created_at, last_used_at, expires_at, user_agent
                )
                VALUES (
                    :selector, :username, :token_hash, NOW(), NOW(),
                    NOW() + (:remember_days || ' days')::interval, :user_agent
                )
            """),
            {
                "selector": selector,
                "username": username,
                "token_hash": token_hash,
                "remember_days": REMEMBER_ME_DAYS,
                "user_agent": _current_user_agent(),
            },
        )

    return selector, f"{selector}.{validator}"


def _revoke_remember_token(selector: str | None = None) -> None:
    engine = _get_db_engine()
    if engine is None or not _ensure_auth_storage():
        return

    effective_selector = selector or st.session_state.get("_remember_selector")
    if not effective_selector:
        parsed = _parse_remember_cookie(st.context.cookies.get(REMEMBER_COOKIE_NAME))
        effective_selector = parsed[0] if parsed else None
    if not effective_selector:
        return

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM bi_remember_tokens WHERE selector = :selector"),
            {"selector": effective_selector},
        )


def _try_restore_session_from_cookie(creds_dict: dict[str, object]) -> bool:
    parsed = _parse_remember_cookie(st.context.cookies.get(REMEMBER_COOKIE_NAME))
    if not parsed:
        return False

    engine = _get_db_engine()
    if engine is None or not _ensure_auth_storage():
        return False

    selector, validator = parsed
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bi_remember_tokens WHERE expires_at <= NOW()"))
        row = conn.execute(
            text("""
                SELECT username, token_hash
                  FROM bi_remember_tokens
                 WHERE selector = :selector
                   AND expires_at > NOW()
            """),
            {"selector": selector},
        ).mappings().fetchone()

        if not row:
            _queue_cookie_write("clear")
            return False

        expected_hash = str(row["token_hash"] or "")
        if not hmac.compare_digest(expected_hash, _hash_remember_validator(validator)):
            conn.execute(
                text("DELETE FROM bi_remember_tokens WHERE selector = :selector"),
                {"selector": selector},
            )
            _queue_cookie_write("clear")
            return False

        username = str(row["username"])
        user_data_raw = creds_dict.get(username)
        user_data = dict(user_data_raw) if user_data_raw else None
        if not user_data:
            conn.execute(
                text("DELETE FROM bi_remember_tokens WHERE selector = :selector"),
                {"selector": selector},
            )
            _queue_cookie_write("clear")
            return False

        new_validator = secrets.token_hex(32)
        conn.execute(
            text("""
                UPDATE bi_remember_tokens
                   SET token_hash = :token_hash,
                       last_used_at = NOW(),
                       expires_at = NOW() + (:remember_days || ' days')::interval,
                       user_agent = :user_agent
                 WHERE selector = :selector
            """),
            {
                "selector": selector,
                "token_hash": _hash_remember_validator(new_validator),
                "remember_days": REMEMBER_ME_DAYS,
                "user_agent": _current_user_agent(),
            },
        )

    _set_authenticated_session(
        username=username,
        display_name=str(user_data.get("name", username)),
        remember_selector=selector,
    )
    _queue_cookie_write("set", f"{selector}.{new_validator}")
    return True


def _deserialize_json_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Colunas object que foram serializadas como JSON string pelo ETL são
    desserializadas de volta para listas/dicts Python após a leitura do banco.
    """
    out = df.copy()
    for col in out.select_dtypes(include="object").columns:
        sample = out[col].dropna().head(5)
        if sample.apply(lambda v: isinstance(v, str) and v[:1] in ("[", "{")).any():
            out[col] = out[col].apply(
                lambda v: json.loads(v) if isinstance(v, str) else v
            )
    return out


def _load_from_database(engine) -> dict[str, pd.DataFrame]:
    """Carrega os DataFrames de analytics diretamente do Supabase."""
    db_mapping = {
        "saldos":     "fato_saldos_contas",
        "cr":         "fato_contas_a_receber",
        "cp":         "fato_contas_a_pagar",
        "realizado":  "fato_fluxo_caixa_realizado",
        "vendas":     "fato_vendas",
        "contratos":  "fato_contratos",
        "centros":    "dim_centro_custo",
        "categorias": "dim_categoria",
    }
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    for key, table_name in db_mapping.items():
        try:
            df = pd.read_sql_table(table_name, engine, schema="bi_analytics")
            frames[key] = _deserialize_json_columns(df)
        except Exception as exc:
            missing.append(table_name)
            st.warning(
                f"Tabela `{table_name}` não encontrada no banco: {exc}\n\n"
                "Execute o pipeline ETL para popular o Supabase."
            )

    return frames


# ─── Carregamento de dados ─────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_data() -> dict[str, pd.DataFrame]:
    # ── Supabase (produção) ────────────────────────────────────────────────────
    engine = _get_db_engine()
    if engine is not None:
        frames = _load_from_database(engine)
    else:
        # ── Fallback: Parquet local (desenvolvimento) ──────────────────────────
        base = _PROJECT_ROOT / "output" / "analytics"
        mapping = {
            "saldos":     "fato_saldos_contas.parquet",
            "cr":         "fato_contas_a_receber.parquet",
            "cp":         "fato_contas_a_pagar.parquet",
            "realizado":  "fato_fluxo_caixa_realizado.parquet",
            "vendas":     "fato_vendas.parquet",
            "contratos":  "fato_contratos.parquet",
            "centros":    "dim_centro_custo.parquet",
            "categorias": "dim_categoria.parquet",
        }
        if not base.exists():
            st.error(
                f"Diretório de dados não encontrado: `{base}`\n\n"
                "Execute o pipeline ETL antes de abrir o dashboard:\n"
                "```\npython -m contaazul_bi.main run\n```"
            )
            st.stop()

        frames = {}
        missing: list[str] = []
        for k, filename in mapping.items():
            path = base / filename
            if not path.exists():
                missing.append(filename)
                continue
            try:
                frames[k] = pd.read_parquet(path)
            except Exception as exc:
                st.error(f"Erro ao ler `{filename}`: {exc}")
                st.stop()

        if missing:
            st.warning(
                "Os seguintes arquivos de dados estão ausentes e podem causar erros:\n"
                + "\n".join(f"- `{f}`" for f in missing)
                + "\n\nReexecute o pipeline ETL para regenerá-los."
            )

    # ── Normalização de datas (comum a ambas as fontes) ────────────────────────
    for col in ["data_vencimento", "data_competencia"]:
        if "cr" in frames:
            frames["cr"][col] = pd.to_datetime(frames["cr"][col], errors="coerce")
        if "cp" in frames:
            frames["cp"][col] = pd.to_datetime(frames["cp"][col], errors="coerce")
    if "realizado" in frames:
        frames["realizado"]["data_pagamento"] = pd.to_datetime(
            frames["realizado"]["data_pagamento"], errors="coerce"
        )
    if "vendas" in frames:
        frames["vendas"]["data"] = pd.to_datetime(frames["vendas"]["data"], errors="coerce")
    return frames


# ─── Formatacao ───────────────────────────────────────────────────────────────
def fmt_brl(valor: float) -> str:
    sinal = "-" if valor < 0 else ""
    v = abs(valor)
    if v >= 1_000_000:
        return f"{sinal}{v / 1_000_000:,.2f} Mi".replace(",", "X").replace(".", ",").replace("X", ".")
    if v >= 1_000:
        return f"{sinal}{v / 1_000:,.2f} Mil".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sinal}{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def kpi_card(label: str, valor: float | str, prefix: str = "R$ ", cor: str = "normal", info: str = "") -> str:
    if isinstance(valor, str):
        val_html = f'<div class="kpi-value neutro">{valor}</div>'
    else:
        val_fmt = fmt_brl(valor)
        if cor == "auto":
            css = "negativo" if valor < 0 else "positivo"
        elif cor in ("negativo", "positivo", "neutro"):
            css = cor
        else:
            css = ""
        val_html = f'<div class="kpi-value {css}">{prefix}{val_fmt}</div>'
    info_html = ""
    if info:
        info_html = (
            f'<details class="kpi-info">'
            f'<summary>i</summary>'
            f'<div class="kpi-info-box">{info}</div>'
            f'</details>'
        )
    return f"""
    <div class="kpi-card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div class="kpi-label" style="min-width:0;flex:1;">{label}</div>
            {info_html}
        </div>
        {val_html}
    </div>"""


def filter_bar_html(ano: int, mes: int, centro: str, cat_label: str = "") -> str:
    centro_txt = centro    if centro    else "Todos os centros"
    cat_txt    = cat_label if cat_label else "Todas as categorias"
    return f"""
    <div class="filter-bar">
        <span class="filter-bar-label">Filtros ativos</span>
        <span class="filter-tag">
            <span class="label">Ano</span>&nbsp;<span class="value">{ano}</span>
        </span>
        <span class="filter-tag">
            <span class="label">Mes</span>&nbsp;<span class="value">{MESES_PT[mes]}</span>
        </span>
        <span class="filter-tag">
            <span class="label">Centro</span>&nbsp;<span class="value">{centro_txt}</span>
        </span>
        <span class="filter-tag">
            <span class="label">Categorias</span>&nbsp;<span class="value">{cat_txt}</span>
        </span>
    </div>"""


def render_chart(fig: go.Figure) -> None:
    # Extrai o titulo antes de zerar — title={"text":""} e necessario para
    # impedir que versoes novas do Streamlit renderizem o titulo Plotly como HTML
    titulo = (fig.layout.title.text or "").strip()

    fig.update_layout(
        paper_bgcolor=BG_CARD,
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_PRIMARY, family="Inter, -apple-system, sans-serif", size=11),
        # String vazia explicita — None e no-op no Plotly e Streamlit exibe "undefined"
        title={"text": ""},
        margin=dict(l=10, r=10, t=54, b=56),
        legend=dict(
            orientation="h", y=-0.16, x=0.5, xanchor="center",
            font=dict(size=10, color=TEXT_SECONDARY),
            bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(
            gridcolor="#ebebeb", zerolinecolor=BORDER,
            tickfont=dict(size=10, color=TEXT_SECONDARY),
            linecolor=BORDER, showgrid=True,
        ),
        yaxis=dict(
            gridcolor="#ebebeb", zerolinecolor=BORDER,
            tickfont=dict(size=10, color=TEXT_SECONDARY),
            linecolor=BORDER, showgrid=True,
        ),
    )

    if titulo:
        fig.add_annotation(
            text=f"<b>{titulo}</b>",
            x=0.5,   xref="x domain",
            y=1.0,   yref="paper",
            xanchor="center", yanchor="bottom",
            yshift=8,
            font=dict(size=13, color=TEXT_PRIMARY,
                       family="Inter, -apple-system, sans-serif"),
            showarrow=False,
        )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ─── Filtros ──────────────────────────────────────────────────────────────────
def _match_json_array(val, nomes_sel: list[str]) -> bool:
    """Verifica se ao menos uma entrada do array (list ou ndarray) bate com nomes_sel."""
    try:
        return any(isinstance(i, dict) and i.get("nome", "") in nomes_sel for i in val)
    except TypeError:
        return False


def _filtrar_centro(df: pd.DataFrame, centros_sel: list[str]) -> pd.DataFrame:
    if not centros_sel:
        return df
    if "centro_custo_nome" in df.columns:
        return df[df["centro_custo_nome"].isin(centros_sel)]
    if "centros_de_custo" in df.columns:
        return df[df["centros_de_custo"].apply(lambda v: _match_json_array(v, centros_sel))]
    return df


def _filtrar_centro_real(df: pd.DataFrame, centros_sel: list[str]) -> pd.DataFrame:
    if not centros_sel:
        return df
    if "centro_custo_nome" in df.columns:
        return df[df["centro_custo_nome"].isin(centros_sel)]
    return df


def _filtrar_categoria(df: pd.DataFrame, cats_sel: list[str]) -> pd.DataFrame:
    """Filtra CR/CP pelo array JSON 'categorias'."""
    if not cats_sel:
        return df
    return df[df["categorias"].apply(lambda v: _match_json_array(v, cats_sel))]


def _filtrar_categoria_real(df: pd.DataFrame, cats_sel: list[str]) -> pd.DataFrame:
    """Filtra realizado pela coluna desnormalizada 'categoria_nome'."""
    if not cats_sel:
        return df
    return df[df["categoria_nome"].isin(cats_sel)]


# ─── Calculos ─────────────────────────────────────────────────────────────────
def _calc_runway(data: dict, centros_sel: list[str], cats_sel: list[str], hoje: pd.Timestamp) -> tuple[int, str, dict]:
    cr = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    saldo_atual = data["saldos"]["saldo_atual"].sum()

    cr_fut = (cr[(cr.data_vencimento >= hoje) & (cr.status_normalizado != "ACQUITTED")]
              [["data_vencimento", "nao_pago"]].assign(sinal=lambda x: x["nao_pago"]))
    cp_fut = (cp[(cp.data_vencimento >= hoje) & (cp.status_normalizado != "ACQUITTED")]
              [["data_vencimento", "nao_pago"]].assign(sinal=lambda x: -x["nao_pago"]))

    fluxo = (
        pd.concat([cr_fut[["data_vencimento", "sinal"]], cp_fut[["data_vencimento", "sinal"]]])
        .groupby("data_vencimento")["sinal"].sum().sort_index()
    )
    saldo_proj = saldo_atual + fluxo.cumsum()
    negativos  = saldo_proj[saldo_proj < 0]

    if negativos.empty:
        return 999, "Sem previsao", {}

    data_neg = negativos.index[0]
    runway   = max((data_neg - hoje).days, 0)
    proj_df  = saldo_proj.reset_index()
    proj_df.columns = ["data", "saldo"]
    proj_df["periodo"] = proj_df["data"].dt.to_period("M")
    saldo_por_mes = {p: g["saldo"].iloc[-1] for p, g in proj_df.groupby("periodo")}
    return runway, data_neg.strftime("%d/%m/%Y"), saldo_por_mes


def calc_resumo(data: dict, ano: int, mes: int, centros_sel: list[str], cats_sel: list[str]) -> dict:
    cr   = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp   = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    real = _filtrar_categoria_real(_filtrar_centro_real(data["realizado"].copy(), centros_sel), cats_sel)
    hoje = pd.Timestamp.today().normalize()

    # Saldo atual: nunca filtrado — fotografia do momento atual
    saldo_atual = data["saldos"]["saldo_atual"].sum()

    # Entradas/Saidas: logica hibrida (realizadas ate ontem + previstas a partir de hoje)
    ontem   = hoje - pd.Timedelta(days=1)
    mes_fim = pd.Timestamp(ano, mes, 1) + pd.offsets.MonthEnd(0)

    r_mes    = real[(real.data_pagamento.dt.year == ano) & (real.data_pagamento.dt.month == mes)
                    & (real.data_pagamento <= ontem)]
    rec_real_hibrido = r_mes[r_mes.tipo_evento_origem == "RECEITA"]["valor_composicao.valor_liquido"].sum()
    des_real_hibrido = r_mes[r_mes.tipo_evento_origem == "DESPESA"]["valor_composicao.valor_liquido"].sum()

    cr_pend = cr[(cr.data_vencimento.dt.year == ano) & (cr.data_vencimento.dt.month == mes) &
                 (cr.status_normalizado == "PENDING") & (cr.data_vencimento >= hoje)]
    cp_pend = cp[(cp.data_vencimento.dt.year == ano) & (cp.data_vencimento.dt.month == mes) &
                 (cp.status_normalizado == "PENDING") & (cp.data_vencimento >= hoje)]

    entradas = rec_real_hibrido + cr_pend["nao_pago"].sum()
    saidas   = des_real_hibrido + cp_pend["nao_pago"].sum()

    # ── Cards de detalhe — Receitas ───────────────────────────────────────────
    # Receitas em Aberto: vencimento >= hoje e <= fim do mes, nao liquidado (filtrado)
    rec_aberto = cr[(cr.data_vencimento >= hoje) & (cr.data_vencimento <= mes_fim)
                    & (cr.status_normalizado != "ACQUITTED")]["nao_pago"].sum()

    # Receitas Realizadas: liquidado dentro do mes/ano (filtrado)
    r_mes_real = real[(real.data_pagamento.dt.year == ano) & (real.data_pagamento.dt.month == mes)]
    rec_realizado = r_mes_real[r_mes_real.tipo_evento_origem == "RECEITA"]["valor_composicao.valor_liquido"].sum()

    # CR Vencidas: vencimento < hoje, nao liquidado — NUNCA filtrado
    cr_raw = data["cr"]
    cr_vencidas = cr_raw[(cr_raw.data_vencimento < hoje)
                         & (cr_raw.status_normalizado != "ACQUITTED")]["nao_pago"].sum()

    # ── Cards de detalhe — Despesas ───────────────────────────────────────────
    # Despesas em Aberto: vencimento >= hoje e <= fim do mes, nao liquidado (filtrado)
    des_aberto = cp[(cp.data_vencimento >= hoje) & (cp.data_vencimento <= mes_fim)
                    & (cp.status_normalizado != "ACQUITTED")]["nao_pago"].sum()

    # Despesas Realizadas: liquidado dentro do mes/ano (filtrado)
    _real_desp_mes = r_mes_real[r_mes_real.tipo_evento_origem == "DESPESA"]
    des_realizado  = _real_desp_mes["valor_composicao.valor_liquido"].sum()

    # Breakdown de CP por vencimento e grupo DRE (operacional amplo vs nao-operacional)
    cp_mes = cp[(cp.data_vencimento.dt.year == ano) & (cp.data_vencimento.dt.month == mes)]
    if not cp_mes.empty:
        _cp_dre = _explode_e_mapear_dre(cp_mes, data["categorias"])
        def _s_cp(grupo: str) -> float:
            return float(_cp_dre.loc[_cp_dre["dre_grupo"] == grupo, "valor"].sum())
        desp_oper_real = (
            _s_cp("Custos Operacionais") +
            _s_cp("Despesas Operacionais") +
            _s_cp("Deduções da Receita Bruta")
        )
        desp_nao_oper_real = float(_cp_dre["valor"].sum()) - desp_oper_real
    else:
        desp_oper_real = desp_nao_oper_real = 0.0

    # CP Vencidas: vencimento < hoje, nao liquidado — NUNCA filtrado
    cp_raw = data["cp"]
    cp_vencidas = cp_raw[(cp_raw.data_vencimento < hoje)
                         & (cp_raw.status_normalizado != "ACQUITTED")]["nao_pago"].sum()

    # Runway e Funding: nunca filtrados — projecao futura completa
    runway, funding_date, _ = _calc_runway(data, [], [], hoje)

    return dict(
        saldo_atual=saldo_atual, entradas=entradas, saidas=saidas,
        liquido=entradas - saidas, runway=runway, funding_date=funding_date,
        rec_aberto=rec_aberto, rec_realizado=rec_realizado, cr_vencidas=cr_vencidas,
        des_aberto=des_aberto, des_realizado=des_realizado, cp_vencidas=cp_vencidas,
        desp_oper_real=desp_oper_real, desp_nao_oper_real=desp_nao_oper_real,
    )


def calc_proj_4_meses(
    data: dict,
    centros_sel: list[str] | None = None,
    cats_sel: list[str] | None = None,
) -> dict[str, float]:
    """Caixa liquido projetado por mes para mes atual + 3 proximos."""
    hoje  = pd.Timestamp.today().normalize()
    ontem = hoje - pd.Timedelta(days=1)
    centros_sel = centros_sel or []
    cats_sel    = cats_sel    or []
    cr   = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp   = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    real = _filtrar_categoria_real(_filtrar_centro_real(data["realizado"].copy(), centros_sel), cats_sel)
    periodos = pd.period_range(hoje.to_period("M"), periods=4, freq="M")
    result: dict[str, float] = {}
    for p in periodos:
        m_ini = p.start_time.normalize()
        m_fim = p.end_time.normalize()
        # Realizadas (somente para o mes atual, dias ja ocorridos)
        if p == hoje.to_period("M"):
            r_p = real[(real.data_pagamento >= m_ini) & (real.data_pagamento <= ontem)]
            ent = r_p[r_p.tipo_evento_origem == "RECEITA"]["valor_composicao.valor_liquido"].sum()
            sai = r_p[r_p.tipo_evento_origem == "DESPESA"]["valor_composicao.valor_liquido"].sum()
        else:
            ent, sai = 0.0, 0.0
        # Previstas: PENDING com vencimento no mes (a partir de hoje para o mes atual)
        venc_ini = hoje if p == hoje.to_period("M") else m_ini
        cr_p = cr[(cr.data_vencimento >= venc_ini) & (cr.data_vencimento <= m_fim)
                  & (cr.status_normalizado == "PENDING")]
        cp_p = cp[(cp.data_vencimento >= venc_ini) & (cp.data_vencimento <= m_fim)
                  & (cp.status_normalizado == "PENDING")]
        ent += cr_p["nao_pago"].sum()
        sai += cp_p["nao_pago"].sum()
        result[str(p)] = ent - sai
    return result


def calc_receita(data: dict, ano: int, mes: int, centros_sel: list[str], cats_sel: list[str]) -> dict:
    cr  = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    vnd = data["vendas"].copy()

    vnd_mes = vnd[(vnd.data.dt.year == ano) & (vnd.data.dt.month == mes)]
    mrr     = vnd_mes["total"].sum()

    n_clientes   = int(vnd_mes["cliente.nome"].dropna().nunique()) if not vnd_mes.empty else 0
    ticket_medio = mrr / n_clientes if n_clientes > 0 else 0.0

    cr_comp        = cr[(cr.data_competencia.dt.year == ano) & (cr.data_competencia.dt.month == mes)]
    nao_recorrente = max(cr_comp["total"].sum() - mrr, 0)

    vnd_ano       = vnd[vnd.data.dt.year == ano].copy()
    vnd_ano["mes"] = vnd_ano.data.dt.month
    mrr_serie     = vnd_ano.groupby("mes")["total"].sum().reindex(range(1, 13), fill_value=0)

    return dict(mrr=mrr, arr=mrr * 12, nao_recorrente=nao_recorrente, mrr_serie=mrr_serie,
                n_clientes=n_clientes, ticket_medio=ticket_medio)


# ─── Graficos ─────────────────────────────────────────────────────────────────
def _saldo_anchor_para_ano(data: dict, saldo_atual: float, ano: int) -> float:
    """
    Calcula o saldo bancario correto para usar como ancora do grafico anual.

    Mesma logica de _saldo_anchor_para_mes, mas na granularidade de ano:

      - Ano atual  : retorna saldo_atual
      - Ano passado: saldo_atual - net_realizado(1/jan/ano+1 .. ultima_data_global)
                     → ancora no saldo real ao final de dezembro daquele ano
      - Ano futuro : saldo_atual + net_pendente(ultima_data_global+1 .. 31/dez/ano-1)
                     → ancora no saldo projetado ao final do ano anterior

    Usa dados NAO filtrados — o saldo bancario nao conhece filtros.
    """
    hoje = pd.Timestamp.today().normalize()

    if ano == hoje.year:
        return saldo_atual

    real_all = data["realizado"]
    cr_all   = data["cr"]
    cp_all   = data["cp"]

    r_datas = real_all["data_pagamento"].dropna().dt.normalize()
    if r_datas.empty:
        return saldo_atual
    ultima_data_global = r_datas.max()

    def _net_real(de: pd.Timestamp, ate: pd.Timestamp) -> float:
        sub = real_all[(real_all.data_pagamento >= de) & (real_all.data_pagamento <= ate)]
        e = sub[sub.tipo_evento_origem == "RECEITA"]["valor_composicao.valor_liquido"].sum()
        s = sub[sub.tipo_evento_origem == "DESPESA"]["valor_composicao.valor_liquido"].sum()
        return e - s

    def _net_pend(de: pd.Timestamp, ate: pd.Timestamp) -> float:
        cr_p = cr_all[(cr_all.data_vencimento >= de) & (cr_all.data_vencimento <= ate)
                      & (cr_all.status_normalizado == "PENDING")]
        cp_p = cp_all[(cp_all.data_vencimento >= de) & (cp_all.data_vencimento <= ate)
                      & (cp_all.status_normalizado == "PENDING")]
        return cr_p["nao_pago"].sum() - cp_p["nao_pago"].sum()

    if ano < hoje.year:
        # Subtrai tudo que foi realizado entre 1/jan/ano+1 e ultima_data_global
        bridge = _net_real(pd.Timestamp(ano + 1, 1, 1), ultima_data_global)
        return saldo_atual - bridge

    if ano > hoje.year:
        # Adiciona pendentes entre ultima_data_global e 31/dez/ano-1
        bridge = _net_pend(ultima_data_global + pd.Timedelta(days=1),
                           pd.Timestamp(ano - 1, 12, 31))
        return saldo_atual + bridge

    return saldo_atual


def _saldo_anchor_para_mes(data: dict, saldo_atual: float, ano: int, mes: int) -> float:
    """
    Calcula o saldo bancario correto para usar como ancora da linha de saldo
    do mes alvo.

    O problema: saldo_atual e sempre o saldo de HOJE. Para visualizar outros
    meses, e necessario 'viajar' pelo fluxo de caixa:

      - Mes atual  : retorna saldo_atual diretamente (a funcao de serie ja ancora)
      - Mes passado: saldo_atual - net_realizado(mes_fim+1 .. ultima_data_global)
                     → reconstroi o saldo do fim daquele mes subtraindo tudo
                       que aconteceu depois
      - Mes futuro : saldo_atual + net_pendente(ultima_data_global+1 .. mes_ini-1)
                     → projeta o saldo ate o inicio do mes futuro adicionando
                       todos os pendentes que vencem antes dele

    Usa dados NAOOO filtrados — o saldo bancario nao conhece filtros de categoria.
    """
    hoje    = pd.Timestamp.today().normalize()
    mes_ini = pd.Timestamp(ano, mes, 1)
    mes_fim = mes_ini + pd.offsets.MonthEnd(0)

    # Mes atual: ancora interna da _serie_diaria_hibrida ja funciona corretamente
    if mes_ini <= hoje <= mes_fim:
        return saldo_atual

    real_all = data["realizado"]
    cr_all   = data["cr"]
    cp_all   = data["cp"]

    r_datas = real_all["data_pagamento"].dropna().dt.normalize()
    if r_datas.empty:
        return saldo_atual
    ultima_data_global = r_datas.max()

    def _net_real(de: pd.Timestamp, ate: pd.Timestamp) -> float:
        sub = real_all[(real_all.data_pagamento >= de) & (real_all.data_pagamento <= ate)]
        e = sub[sub.tipo_evento_origem == "RECEITA"]["valor_composicao.valor_liquido"].sum()
        s = sub[sub.tipo_evento_origem == "DESPESA"]["valor_composicao.valor_liquido"].sum()
        return e - s

    def _net_pend(de: pd.Timestamp, ate: pd.Timestamp) -> float:
        cr_p = cr_all[(cr_all.data_vencimento >= de) & (cr_all.data_vencimento <= ate)
                      & (cr_all.status_normalizado == "PENDING")]
        cp_p = cp_all[(cp_all.data_vencimento >= de) & (cp_all.data_vencimento <= ate)
                      & (cp_all.status_normalizado == "PENDING")]
        return cr_p["nao_pago"].sum() - cp_p["nao_pago"].sum()

    if mes_fim < hoje:
        # Mes passado: desconta tudo que foi realizado entre o fim do mes e hoje
        bridge = _net_real(mes_fim + pd.Timedelta(days=1), ultima_data_global)
        return saldo_atual - bridge

    if mes_ini > hoje:
        # Mes futuro: adiciona pendentes entre o ultimo realizado e o inicio do mes
        bridge = _net_pend(ultima_data_global + pd.Timedelta(days=1),
                           mes_ini - pd.Timedelta(days=1))
        return saldo_atual + bridge

    return saldo_atual


def _serie_diaria_hibrida(real: pd.DataFrame, cr: pd.DataFrame, cp: pd.DataFrame,
                           ano: int, mes: int, saldo_atual: float) -> pd.DataFrame:
    """
    Saldo absoluto ancorado na ultima data com dado realizado.

    O parametro saldo_atual deve ja estar ajustado para o mes alvo via
    _saldo_anchor_para_mes — ele representa o saldo bancario no ponto de
    referencia interno do mes (ultima_data para meses passados/atual,
    dia anterior ao mes para meses futuros).

    Separacao limpa das fontes:
      - Dias <= ultima_data: net de 'realizado' APENAS
      - Dias >  ultima_data: net de PENDING (CR/CP) APENAS
    """
    mes_ini    = pd.Timestamp(ano, mes, 1)
    mes_fim    = mes_ini + pd.offsets.MonthEnd(0)
    idx        = pd.date_range(mes_ini, mes_fim, freq="D")

    # ── Realizados do mes ─────────────────────────────────────────────────────
    r = real[(real.data_pagamento >= mes_ini) & (real.data_pagamento <= mes_fim)].copy()
    r["data"] = r["data_pagamento"].dt.normalize()
    r["liq"]  = r["valor_composicao.valor_liquido"]
    ent_r = r[r.tipo_evento_origem == "RECEITA"].groupby("data")["liq"].sum()
    sai_r = r[r.tipo_evento_origem == "DESPESA"].groupby("data")["liq"].sum()
    net_r = (ent_r.reindex(idx, fill_value=0) - sai_r.reindex(idx, fill_value=0)).fillna(0)

    # Ultima data com pagamento realizado (pode ser anterior a mes_fim)
    ultima_data = r["data"].max() if not r.empty else mes_ini - pd.Timedelta(days=1)

    # ── Pendentes do mes (apenas para dias APOS ultima_data) ─────────────────
    cr_p  = cr[(cr.data_vencimento > ultima_data) & (cr.data_vencimento <= mes_fim)
               & (cr.status_normalizado == "PENDING")]
    cp_p  = cp[(cp.data_vencimento > ultima_data) & (cp.data_vencimento <= mes_fim)
               & (cp.status_normalizado == "PENDING")]
    ent_p = cr_p.groupby(cr_p.data_vencimento.dt.normalize())["nao_pago"].sum()
    sai_p = cp_p.groupby(cp_p.data_vencimento.dt.normalize())["nao_pago"].sum()
    net_p = (ent_p.reindex(idx, fill_value=0) - sai_p.reindex(idx, fill_value=0)).fillna(0)

    # ── Barras: realizadas + previstas ───────────────────────────────────────
    serie = pd.DataFrame(index=idx)
    serie["entradas"] = (ent_r.reindex(idx, fill_value=0).fillna(0) +
                         ent_p.reindex(idx, fill_value=0).fillna(0))
    serie["saidas"]   = (sai_r.reindex(idx, fill_value=0).fillna(0) +
                         sai_p.reindex(idx, fill_value=0).fillna(0))

    # ── Linha de saldo: ancora em ultima_data = saldo_atual ──────────────────
    # Para dias <= ultima_data: reconstrucao historica via cumnet realizado
    # Para dias >  ultima_data: projecao via cumnet pendente a partir de saldo_atual
    cumnet_r = net_r.cumsum()
    ref_r    = cumnet_r.get(ultima_data, cumnet_r.iloc[-1] if not cumnet_r.empty else 0.0)

    cumnet_p_raw  = net_p.cumsum()
    # Pendentes acumulam a partir de ultima_data (ref = 0 no ponto ultima_data)
    # Nos dias <= ultima_data, net_p = 0, entao cumnet_p_raw = 0 nesse trecho

    serie["saldo"] = saldo_atual + (cumnet_r - ref_r) + cumnet_p_raw
    return serie


def _serie_mensal(real: pd.DataFrame, cr: pd.DataFrame, cp: pd.DataFrame,
                  ano: int, saldo_atual: float = 0.0,
                  ultima_real: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    Serie mensal hibrida. A linha de saldo reflete o saldo projetado ao
    ULTIMO DIA de cada mes.

    Pendentes filtrados a partir de ultima_real (data exata, nao fronteira de mes),
    garantindo que o restante do mes atual (apos o ultimo realizado) seja
    incluido como projecao — e nao ignorado.
    """
    r = real[real.data_pagamento.dt.year == ano].copy()
    r["mes"] = r["data_pagamento"].dt.month
    r["liq"] = r["valor_composicao.valor_liquido"]
    ent_r = r[r.tipo_evento_origem == "RECEITA"].groupby("mes")["liq"].sum()
    sai_r = r[r.tipo_evento_origem == "DESPESA"].groupby("mes")["liq"].sum()

    ultimo_mes_real = int(r["mes"].max()) if not r.empty else 0

    # Data exata de corte — necessaria para incluir pendentes do mes atual
    if ultima_real is None:
        ultima_real = (r["data_pagamento"].dt.normalize().max()
                       if not r.empty
                       else pd.Timestamp(ano, 1, 1) - pd.Timedelta(days=1))

    # Pendentes: vencimento APOS ultima_real (inclui restante do mes corrente)
    cr_a = cr[(cr.data_vencimento > ultima_real) &
              (cr.data_vencimento.dt.year == ano)].copy()
    cp_a = cp[(cp.data_vencimento > ultima_real) &
              (cp.data_vencimento.dt.year == ano)].copy()
    cr_a["mes"] = cr_a.data_vencimento.dt.month
    cp_a["mes"] = cp_a.data_vencimento.dt.month
    ent_p = cr_a[cr_a.status_normalizado == "PENDING"].groupby("mes")["nao_pago"].sum()
    sai_p = cp_a[cp_a.status_normalizado == "PENDING"].groupby("mes")["nao_pago"].sum()

    idx = range(1, 13)
    s = pd.DataFrame(index=idx)
    s["entradas"] = ent_r.reindex(idx, fill_value=0) + ent_p.reindex(idx, fill_value=0)
    s["saidas"]   = sai_r.reindex(idx, fill_value=0) + sai_p.reindex(idx, fill_value=0)

    net_r_mes = (ent_r.reindex(idx, fill_value=0) - sai_r.reindex(idx, fill_value=0)).fillna(0)
    net_p_mes = (ent_p.reindex(idx, fill_value=0) - sai_p.reindex(idx, fill_value=0)).fillna(0)

    cumnet_r = net_r_mes.cumsum()
    cumnet_p = net_p_mes.cumsum()

    if ultimo_mes_real > 0:
        ref_r = cumnet_r.get(ultimo_mes_real, 0.0)
        s["saldo"] = saldo_atual + (cumnet_r - ref_r) + cumnet_p
    else:
        # Ano sem realizados (futuro): ancora em saldo_atual e acumula pendentes
        s["saldo"] = saldo_atual + cumnet_p
    return s


def _bar_saldo_chart(x, ent, sai, saldo, lbl_ent, lbl_sai, titulo: str,
                     lbl_saldo: list | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x, y=ent, name="Entradas",
        marker_color=GREEN, opacity=0.90,
        marker_line=dict(width=0),
        text=lbl_ent, textposition="outside",
        textfont=dict(size=8, color=TEXT_PRIMARY),
    ))
    fig.add_trace(go.Bar(
        x=x, y=[-v for v in sai], name="Saidas",
        marker_color=RED, opacity=0.90,
        marker_line=dict(width=0),
        text=lbl_sai, textposition="outside",
        textfont=dict(size=8, color=TEXT_PRIMARY),
    ))
    scatter_mode = "lines+markers+text" if lbl_saldo else "lines+markers"
    fig.add_trace(go.Scatter(
        x=x, y=saldo, name="Saldo",
        mode=scatter_mode,
        text=lbl_saldo,
        textposition="top center",
        textfont=dict(size=8, color=TEXT_PRIMARY),
        line=dict(color="#000000", width=2),
        marker=dict(size=4, color="#000000"),
    ))
    fig.update_layout(title=titulo, barmode="relative", yaxis_tickformat=",.0f")
    return fig


def fig_fluxo_diario(serie: pd.DataFrame, titulo: str) -> go.Figure:
    return _bar_saldo_chart(
        x=list(serie.index),
        ent=serie["entradas"].tolist(),
        sai=serie["saidas"].tolist(),
        saldo=serie["saldo"].tolist(),
        lbl_ent=[""] * len(serie),
        lbl_sai=[""] * len(serie),
        titulo=titulo,
    )


def fig_fluxo_mensal(serie: pd.DataFrame, ano: int, titulo: str) -> go.Figure:
    lbs = [f"{MESES_ABREV[m]} {ano}" for m in serie.index]
    return _bar_saldo_chart(
        x=lbs,
        ent=serie["entradas"].tolist(),
        sai=serie["saidas"].tolist(),
        saldo=serie["saldo"].tolist(),
        lbl_ent=[f"{v/1000:.1f}k" if v > 0 else "" for v in serie["entradas"]],
        lbl_sai=[f"-{v/1000:.1f}k" if v > 0 else "" for v in serie["saidas"]],
        titulo=titulo,
    )


def fig_cenarios_mensal(cr: pd.DataFrame, cp: pd.DataFrame, ano: int) -> go.Figure:
    idx  = range(1, 13)
    cr_a = cr[cr.data_vencimento.dt.year == ano].copy()
    cr_a["mes"] = cr_a.data_vencimento.dt.month
    cp_a = cp[cp.data_vencimento.dt.year == ano].copy()
    cp_a["mes"] = cp_a.data_vencimento.dt.month
    ent       = cr_a.groupby("mes")["total"].sum().reindex(idx, fill_value=0)
    sai       = cp_a.groupby("mes")["total"].sum().reindex(idx, fill_value=0)
    saldo_acum = (ent - sai).cumsum()
    lbs = [f"{MESES_ABREV[m]} {ano}" for m in idx]
    return _bar_saldo_chart(
        x=lbs,
        ent=ent.tolist(),
        sai=sai.tolist(),
        saldo=saldo_acum.tolist(),
        lbl_ent=[f"{v/1000:.1f}k" if v > 1000 else "" for v in ent],
        lbl_sai=[f"-{v/1000:.1f}k" if v > 1000 else "" for v in sai],
        titulo="Fluxo de Caixa | Mensal",
    )


def fig_caixa_proj(saldo_proj: dict) -> go.Figure:
    if not saldo_proj:
        return go.Figure()
    periodos = sorted(saldo_proj.keys())[:6]
    lbs  = [f"{MESES_ABREV[p.month]}/{p.year}" for p in periodos]
    vals = [saldo_proj[p] for p in periodos]
    cores = [GREEN if v >= 0 else RED for v in vals]
    tpos = ["inside" if v < 0 else "outside" for v in vals]
    fig = go.Figure(go.Bar(
        x=lbs, y=vals, marker_color=cores, marker_line=dict(width=0), opacity=0.90,
        text=[fmt_brl(v) for v in vals],
        textposition=tpos,
        textfont=dict(size=11),
    ))
    fig.update_traces(textfont_color="#ffffff")
    fig.update_layout(title="Caixa Liquido Projetado", showlegend=False, yaxis_tickformat=",.0f")
    return fig


def fig_proj_4_meses(proj: dict[str, float]) -> go.Figure:
    """Grafico 2: Caixa liquido projetado — mes atual + 3 proximos. Sem filtros."""
    if not proj:
        return go.Figure()
    periodos_str = list(proj.keys())
    vals  = list(proj.values())
    lbs   = []
    for p_str in periodos_str:
        p = pd.Period(p_str, freq="M")
        lbs.append(f"{MESES_ABREV[p.month]}/{p.year}")
    cores = [GREEN if v >= 0 else RED for v in vals]
    # Para barras negativas "inside" garante que o rotulo fique visivel dentro da barra.
    tpos = ["inside" if v < 0 else "outside" for v in vals]
    fig = go.Figure(go.Bar(
        x=lbs, y=vals,
        marker_color=cores, marker_line=dict(width=0), opacity=0.90,
        text=[fmt_brl(v) for v in vals],
        textposition=tpos,
        textfont=dict(size=11),
    ))
    # Ajustar cor de texto individualmente via update_traces nao e suportado por item;
    # usamos uma cor unica — branco funciona bem sobre vermelho e verde
    fig.update_traces(textfont_color="#ffffff")
    fig.update_layout(
        title="Caixa Liquido Projetado — Proximos 4 Meses",
        showlegend=False, yaxis_tickformat=",.0f",
    )
    return fig


def fig_mrr(serie: pd.Series, ano: int) -> go.Figure:
    lbs = [f"{MESES_ABREV[m]} {ano}" for m in serie.index]
    fig = go.Figure(go.Bar(
        x=lbs, y=serie.values, marker_color=BLUE, marker_line=dict(width=0), opacity=0.85,
        text=[f"{v/1000:.0f}k" if v > 0 else "" for v in serie.values],
        textposition="inside",
        textfont=dict(size=9, color="#ffffff"),
    ))
    fig.update_layout(title="MRR | Projetado", showlegend=False, yaxis_tickformat=",.0f")
    return fig


# ─── Paginas ───────────────────────────────────────────────────────────────────
def _page_header(titulo: str, subtitulo: str, ano: int, mes: int, centro: str, categoria: str) -> None:
    st.markdown(f'<div class="page-title">{titulo}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="page-subtitle">{subtitulo}</div>', unsafe_allow_html=True)
    st.markdown(filter_bar_html(ano, mes, centro, categoria), unsafe_allow_html=True)


def _ranking_categorias_despesas(
    cp_f: pd.DataFrame, cr_f: pd.DataFrame, ano: int, mes: int, top_n: int = 10
) -> pd.DataFrame:
    """Ranking das maiores categorias de despesas no período, por data de vencimento."""
    cp_periodo = cp_f[
        (cp_f["data_vencimento"].dt.year == ano) & (cp_f["data_vencimento"].dt.month == mes)
    ]
    cr_periodo = cr_f[
        (cr_f["data_vencimento"].dt.year == ano) & (cr_f["data_vencimento"].dt.month == mes)
    ]

    receita_total  = pd.to_numeric(cr_periodo["total"], errors="coerce").fillna(0).sum()
    despesa_total  = pd.to_numeric(cp_periodo["total"], errors="coerce").fillna(0).sum()

    rows = []
    for _, row in cp_periodo.iterrows():
        valor = pd.to_numeric(row.get("total"), errors="coerce")
        if pd.isna(valor):
            valor = 0.0
        cats = row.get("categorias") or []
        cats = [c for c in cats if isinstance(c, dict) and c.get("nome")]
        if cats:
            v_por_cat = valor / len(cats)
            for c in cats:
                rows.append({"Categoria": c["nome"], "Valor": v_por_cat})
        else:
            rows.append({"Categoria": "(Sem categoria)", "Valor": valor})

    if not rows:
        return pd.DataFrame()

    ranking = (
        pd.DataFrame(rows)
        .groupby("Categoria", as_index=False)["Valor"].sum()
        .sort_values("Valor", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    ranking["% Despesa"] = (ranking["Valor"] / despesa_total * 100) if despesa_total > 0 else 0.0
    ranking["% Receita"] = (ranking["Valor"] / receita_total * 100) if receita_total > 0 else 0.0

    return ranking


def _ranking_clientes_cr(cr_f: pd.DataFrame, ano: int, mes: int, top_n: int = 5) -> pd.DataFrame:
    """Ranking dos maiores clientes por CR no período, por data de vencimento."""
    cr_periodo = cr_f[
        (cr_f["data_vencimento"].dt.year == ano) & (cr_f["data_vencimento"].dt.month == mes)
    ].copy()

    if cr_periodo.empty:
        return pd.DataFrame()

    cr_periodo["_cliente"] = cr_periodo.apply(
        lambda r: r.get("cliente.nome") or r.get("cliente.id") or "(Sem cliente)", axis=1
    )
    cr_periodo["_valor"] = pd.to_numeric(cr_periodo["total"], errors="coerce").fillna(0)

    ranking = (
        cr_periodo.groupby("_cliente", as_index=False)["_valor"].sum()
        .rename(columns={"_cliente": "Cliente", "_valor": "Valor"})
        .sort_values("Valor", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    total_cr = cr_periodo["_valor"].sum()
    ranking["% do CR"] = (ranking["Valor"] / total_cr * 100) if total_cr > 0 else 0.0

    return ranking


def pagina_resumo(data: dict, ano: int, mes: int, centros_sel: list[str], centro_label: str,
                  cats_sel: list[str], cat_label: str) -> None:
    _page_header("Resumo de Caixa", "Visao executiva do caixa", ano, mes, centro_label, cat_label)

    m = calc_resumo(data, ano, mes, centros_sel, cats_sel)

    # ── Linha 1: fotografia atual ─────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.markdown(kpi_card("Saldo de Caixa Atual",  m["saldo_atual"],
        info="Soma dos saldos atuais de todas as contas bancárias. Atualizado a cada execução do ETL. Não responde a filtros de categoria ou centro."),
        unsafe_allow_html=True)
    c2.markdown(kpi_card("Entradas de Caixa",     m["entradas"], cor="positivo",
        info="Estimativa híbrida: receitas já recebidas (base pagamento, até ontem) + receitas pendentes com vencimento no mês (base vencimento, a partir de hoje)."),
        unsafe_allow_html=True)
    c3.markdown(kpi_card("Saidas de Caixa",       m["saidas"],   cor="negativo",
        info="Estimativa híbrida: despesas já pagas (base pagamento, até ontem) + despesas pendentes com vencimento no mês (base vencimento, a partir de hoje)."),
        unsafe_allow_html=True)
    c4.markdown(kpi_card("Caixa Liquido",         m["liquido"],  cor="auto",
        info="Entradas menos Saídas do período. Positivo indica geração de caixa; negativo indica consumo."),
        unsafe_allow_html=True)
    c5.markdown(kpi_card(
        "Runway",
        f"{m['runway']} dias" if m["runway"] < 999 else "Sem previsao",
        prefix="", cor="neutro",
        info="Dias que o saldo atual sustenta as despesas comprometidas futuras. Calculado sem filtros — considera a empresa inteira.",
    ), unsafe_allow_html=True)
    c6.markdown(kpi_card("Necessidade de Funding", m["funding_date"], prefix="", cor="neutro",
        info="Data estimada em que o caixa se esgota com base nas saídas comprometidas. Exibe \"Sem previsão\" se não há risco iminente. Sem filtros."),
        unsafe_allow_html=True)

    # ── Linha 2: Receitas ─────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:18px 0 6px 0;'>Receitas</div>",
        unsafe_allow_html=True,
    )
    r1, r2, r3 = st.columns(3)
    r1.markdown(kpi_card("Receitas em Aberto",    m["rec_aberto"],   cor="positivo",
        info="CR não liquidadas com vencimento entre hoje e o fim do mês. Mostra o que ainda será recebido no período."),
        unsafe_allow_html=True)
    r2.markdown(kpi_card("Receitas Realizadas",   m["rec_realizado"], cor="positivo",
        info="Receitas efetivamente recebidas no mês (data de pagamento registrada). Responde aos filtros ativos."),
        unsafe_allow_html=True)
    r3.markdown(kpi_card("Contas a Receber Vencidas", m["cr_vencidas"], cor="negativo",
        info="CR vencidas antes de hoje e ainda em aberto. Indica inadimplência ou atraso de clientes. Sem filtros."),
        unsafe_allow_html=True)

    # ── Linha 3: Despesas ─────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:18px 0 6px 0;'>Despesas</div>",
        unsafe_allow_html=True,
    )
    d1, d2, d3 = st.columns(3)
    d1.markdown(kpi_card("Despesas em Aberto",    m["des_aberto"],   cor="negativo",
        info="CP não liquidadas com vencimento entre hoje e o fim do mês. Compromissos financeiros pendentes no período."),
        unsafe_allow_html=True)
    d2.markdown(kpi_card("Despesas Realizadas",   m["des_realizado"], cor="negativo",
        info="Despesas efetivamente pagas no mês (data de pagamento). Responde aos filtros ativos."),
        unsafe_allow_html=True)
    d3.markdown(kpi_card("Contas a Pagar Vencidas", m["cp_vencidas"], cor="negativo",
        info="CP vencidas antes de hoje e ainda em aberto. Obrigações em atraso. Sem filtros."),
        unsafe_allow_html=True)

    # ── Linha 3b: Estrutura das despesas realizadas (base caixa) ──────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:10px 0 6px 0;'>"
        f"Estrutura de Despesas Previstas — Vencimento | {MESES_PT[mes]} {ano}</div>",
        unsafe_allow_html=True,
    )
    e1, e2, _ = st.columns(3)
    _rec_base = m["rec_realizado"] if m["rec_realizado"] else None
    for _ecol, _elabel, _ev, _einfo in [
        (e1, "Desp. Operacional (CP)",     m["desp_oper_real"],
         "CP com vencimento no mês: Custos Operacionais + Despesas Operacionais + Deduções da Receita Bruta (ISS, PIS, COFINS). Inclui pagas e pendentes."),
        (e2, "Desp. Nao-Operacional (CP)", m["desp_nao_oper_real"],
         "Tudo mais nas CP do mês: despesas financeiras, investimentos, IRPJ/CSLL e categorias sem mapeamento DRE. Complemento do Operacional."),
    ]:
        _ecol.markdown(kpi_card(_elabel, _ev, cor="negativo", info=_einfo), unsafe_allow_html=True)
        _pct_txt = f"{_ev / _rec_base * 100:.1f}% da Rec. Realizada" if _rec_base else "—"
        _ecol.markdown(
            f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};margin-top:-6px;"
            f"padding:0 4px;'>{_pct_txt}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Grafico 1 (sensivel) + Grafico 2 (nao sensivel) ──────────────────────
    cr_f   = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp_f   = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    real_f = _filtrar_categoria_real(_filtrar_centro_real(data["realizado"].copy(), centros_sel), cats_sel)

    g1, g2 = st.columns([3, 2])
    with g1:
        saldo_ancora = _saldo_anchor_para_mes(data, m["saldo_atual"], ano, mes)
        serie_dia = _serie_diaria_hibrida(real_f, cr_f, cp_f, ano, mes, saldo_ancora)
        render_chart(fig_fluxo_diario(
            serie_dia,
            f"Fluxo de Caixa — {MESES_PT[mes]} {ano}",
        ))
    with g2:
        proj_4m = calc_proj_4_meses(data, centros_sel=centros_sel, cats_sel=cats_sel)
        render_chart(fig_proj_4_meses(proj_4m))

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Grafico 3: Fluxo Anual (sensivel, ignora filtro de mes) ─────────────
    ultima_real_global = data["realizado"]["data_pagamento"].dt.normalize().dropna().max()
    saldo_anchor_ano   = _saldo_anchor_para_ano(data, m["saldo_atual"], ano)
    serie_anual = _serie_mensal(
        real_f, cr_f, cp_f, ano,
        saldo_atual=saldo_anchor_ano,
        ultima_real=ultima_real_global,
    )
    render_chart(fig_fluxo_mensal(serie_anual, ano, f"Fluxo de Caixa — Anual {ano}"))

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:18px 0 6px 0;'>"
        f"Ranking de Categorias de Despesas — Top 10 | {MESES_PT[mes]} {ano}</div>",
        unsafe_allow_html=True,
    )
    ranking_df = _ranking_categorias_despesas(cp_f, cr_f, ano, mes)
    if ranking_df.empty:
        st.info("Nenhuma despesa com vencimento no período selecionado.")
    else:
        display_df = ranking_df.copy()
        display_df["Valor"] = display_df["Valor"].apply(
            lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Categoria": st.column_config.TextColumn("Categoria", width="medium"),
                "Valor": st.column_config.TextColumn("Valor Nominal", width="small"),
                "% Despesa": st.column_config.ProgressColumn(
                    "% da Despesa Total", format="%.1f%%", min_value=0, max_value=100
                ),
                "% Receita": st.column_config.ProgressColumn(
                    "% da Receita do Período", format="%.1f%%", min_value=0, max_value=100
                ),
            },
        )


def _projecoes_serie_mensal(projecoes: list, ano: int) -> pd.DataFrame:
    """
    Converts manual projections to monthly entradas/saidas for the given year.
    data_ini and data_fim are always stored as first-of-month, so the check
    ini <= m_ts <= fim is exact and unambiguous.
    """
    idx = range(1, 13)
    ent = pd.Series(0.0, index=idx)
    sai = pd.Series(0.0, index=idx)
    for p in projecoes:
        try:
            ini = pd.Timestamp(p["data_ini"]).replace(day=1)
            fim = pd.Timestamp(p["data_fim"]).replace(day=1)
            val = float(p["valor_mensal"])
        except Exception:
            continue
        for m in idx:
            m_ts = pd.Timestamp(ano, m, 1)
            if ini <= m_ts <= fim:
                if p["tipo"] == "entrada":
                    ent[m] += val
                else:
                    sai[m] += val
    return pd.DataFrame({"entradas": ent, "saidas": sai})


def _contratos_elegiveis(data: dict, excluidos: list) -> pd.DataFrame:
    """Returns contracts eligible for auto-renewal (elegivel_renovacao_sem_churn=True, not excluded)."""
    c = data["contratos"].copy()
    # Filter eligible and not manually excluded
    eligible = c[c["elegivel_renovacao_sem_churn"] == True].copy()
    eligible = eligible[~eligible["id"].isin(excluidos)].copy()
    # Parse dates
    for col in ["competencia_inicio_renovacao", "competencia_fim_renovacao"]:
        eligible[col] = pd.to_datetime(eligible[col], errors="coerce")
    eligible = eligible.dropna(subset=["competencia_inicio_renovacao", "valor_base_renovacao_num"])
    return eligible


def _contratos_renovacao_nao_faturada(data: dict, excluidos: list) -> list[dict]:
    """
    For each eligible renewal contract, scans forward month by month from
    competencia_inicio_renovacao and detects the first month where the client's
    CR drops by >= 50% of the contract value vs the previous month.
    That drop signals the ERP stopped generating invoices for this contract.
    Returns a list of {ini: add_from_date, fim, val} for contracts that need
    renovation added (from add_from onwards through fim).
    """
    eligible = _contratos_elegiveis(data, excluidos)
    if eligible.empty:
        return []
    cr_cli_mes = (
        data["cr"]
        .dropna(subset=["data_vencimento", "cliente.id"])
        .assign(mes=lambda x: x["data_vencimento"].dt.to_period("M"))
        .groupby(["cliente.id", "mes"])["nao_pago"]
        .sum()
    )
    resultado = []
    for _, row in eligible.iterrows():
        ini = row["competencia_inicio_renovacao"]
        fim = row["competencia_fim_renovacao"]
        val = float(row["valor_base_renovacao_num"])
        cid = row.get("cliente.id")
        if pd.isna(ini) or pd.isna(fim):
            continue
        prev_cr = cr_cli_mes.get((cid, (ini - pd.DateOffset(months=1)).to_period("M")), 0.0)
        if prev_cr == 0:
            # No CR in month before renewal → contract not yet invoiced at all
            add_from = ini
        else:
            add_from = None
            current = ini
            while current <= fim:
                curr_cr = cr_cli_mes.get((cid, current.to_period("M")), 0.0)
                if curr_cr < prev_cr - val * 0.5:
                    add_from = current
                    break
                prev_cr = curr_cr
                current = (current + pd.DateOffset(months=1)).normalize()
        if add_from is not None and add_from <= fim:
            resultado.append({"ini": add_from, "fim": fim, "val": val})
    return resultado


def _renovacao_serie_mensal(data: dict, excluidos: list, ano: int) -> pd.DataFrame:
    """
    Returns monthly entradas from auto-renewal contracts for the given year.
    Uses forward-scan gap detection to find from which month each contract's
    renovation should be added (avoids double-counting with existing CR invoices).
    """
    a_adicionar = _contratos_renovacao_nao_faturada(data, excluidos)
    idx = range(1, 13)
    ent = pd.Series(0.0, index=idx)
    for item in a_adicionar:
        ini, fim, val = item["ini"], item["fim"], item["val"]
        for m in idx:
            m_ts = pd.Timestamp(ano, m, 1)
            if ini <= m_ts <= fim:
                ent[m] += val
    return pd.DataFrame({"entradas": ent, "saidas": pd.Series(0.0, index=idx)})


def _serie_mensal_com_cenario(
    real: pd.DataFrame, cr: pd.DataFrame, cp: pd.DataFrame,
    ano: int, saldo_atual: float, ultima_real: pd.Timestamp,
    proj_df: pd.DataFrame, renov_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Extends _serie_mensal with manual projections and auto-renewal.
    Projections only affect FUTURE months (>= current month of current year).
    The cumulative delta from projections propagates forward in the saldo line.
    """
    base = _serie_mensal(real, cr, cp, ano, saldo_atual, ultima_real)

    hoje = pd.Timestamp.today().normalize()
    idx = range(1, 13)

    # Extra net per month from projections (only future months)
    delta_ent = proj_df["entradas"] + renov_df["entradas"]
    delta_sai = proj_df["saidas"] + renov_df["saidas"]
    delta_net = delta_ent - delta_sai

    # Zero out past months (projections don't retroactively change realized saldo)
    for m in idx:
        m_ts = pd.Timestamp(ano, m, 1)
        if ano < hoje.year or (ano == hoje.year and m_ts.month < hoje.month):
            delta_net[m] = 0.0

    # Add extra entries/exits to bars
    base["entradas"] = base["entradas"] + delta_ent
    base["saidas"]   = base["saidas"]   + delta_sai

    # Add cumulative delta to saldo line (propagates forward)
    base["saldo"] = base["saldo"] + delta_net.cumsum()
    return base


def calc_proj_4_meses_cenario(
    data: dict,
    projecoes: list,
    excluidos: list,
    centros_sel: list[str] | None = None,
    cats_sel: list[str] | None = None,
) -> dict[str, float]:
    """
    4-month projection including manual projections and auto-renewal.
    Sensitive to category and cost center filters.
    """
    base = calc_proj_4_meses(data, centros_sel=centros_sel, cats_sel=cats_sel)
    hoje = pd.Timestamp.today().normalize()
    periodos = pd.period_range(hoje.to_period("M"), periods=4, freq="M")

    # Add projections
    for p_str, val in base.items():
        p = pd.Period(p_str, freq="M")
        m_ini = p.start_time.normalize()
        m_fim = p.end_time.normalize()
        ano_p = p.year
        mes_p = p.month
        m_ts  = pd.Timestamp(ano_p, mes_p, 1)

        # Manual projections
        for proj in projecoes:
            try:
                ini = pd.Timestamp(proj["data_ini"]).replace(day=1)
                fim = pd.Timestamp(proj["data_fim"]).replace(day=1)
                v = float(proj["valor_mensal"])
            except Exception:
                continue
            if ini <= m_ts <= fim:
                base[p_str] += v if proj["tipo"] == "entrada" else -v

        # Auto renewal — only contracts not yet invoiced in CR
        a_adicionar = _contratos_renovacao_nao_faturada(data, excluidos)
        for item in a_adicionar:
            if item["ini"] <= m_ts <= item["fim"]:
                base[p_str] += item["val"]
    return base


def calc_resumo_cenario(
    data: dict, ano: int, mes: int,
    centros_sel: list, cats_sel: list,
    projecoes: list, excluidos: list, renovacao_ativa: bool,
) -> dict:
    """
    Extends calc_resumo with manual projections and auto-renewal.
    Adds projection amounts for the selected month/year to entradas/saidas.
    """
    base = calc_resumo(data, ano, mes, centros_sel, cats_sel)
    hoje = pd.Timestamp.today().normalize()
    mes_ini = pd.Timestamp(ano, mes, 1)
    mes_fim = mes_ini + pd.offsets.MonthEnd(0)

    extra_ent = 0.0
    extra_sai = 0.0

    # Manual projections active in the selected month
    m_ts = pd.Timestamp(ano, mes, 1)
    for p in projecoes:
        try:
            ini = pd.Timestamp(p["data_ini"]).replace(day=1)
            fim = pd.Timestamp(p["data_fim"]).replace(day=1)
            val = float(p["valor_mensal"])
        except Exception:
            continue
        if ini <= m_ts <= fim:
            if p["tipo"] == "entrada":
                extra_ent += val
            else:
                extra_sai += val

    # Auto renewal for the selected month — only contracts not yet invoiced in CR
    if renovacao_ativa:
        m_ts = pd.Timestamp(ano, mes, 1)
        for item in _contratos_renovacao_nao_faturada(data, excluidos):
            if item["ini"] <= m_ts <= item["fim"]:
                extra_ent += item["val"]

    ent = base["entradas"] + extra_ent
    sai = base["saidas"]   + extra_sai

    # Recalculate runway with scenario
    # Build augmented monthly flows for runway estimate
    runway = base["runway"]
    funding_date = base["funding_date"]

    return dict(
        saldo_atual=base["saldo_atual"],
        entradas=ent, saidas=sai, liquido=ent - sai,
        runway=runway, funding_date=funding_date,
        extra_ent=extra_ent, extra_sai=extra_sai,
    )


def pagina_cenarios(data: dict, ano: int, mes: int, centros_sel: list[str], centro_label: str,
                    cats_sel: list[str], cat_label: str) -> None:
    _page_header("Cenarios de Caixa", "Fluxo projetado com cenarios manuais e renovacao automatica",
                 ano, mes, centro_label, cat_label)

    # ── Load state ───────────────────────────────────────────────────────────
    cen = _load_cenarios()
    projecoes:  list = cen["projecoes"]
    excluidos:  list = cen["contratos_excluidos"]
    renov_ativa: bool = cen["renovacao_ativa"]

    # ── Cards ─────────────────────────────────────────────────────────────────
    m = calc_resumo_cenario(data, ano, mes, centros_sel, cats_sel,
                            projecoes, excluidos, renov_ativa)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.markdown(kpi_card("Saldo de Caixa Atual", m["saldo_atual"],
        info="Soma dos saldos atuais de todas as contas bancárias. Atualizado a cada execução do ETL. Não responde a filtros de categoria ou centro."),
        unsafe_allow_html=True)
    c2.markdown(kpi_card("Entradas de Caixa",    m["entradas"], cor="positivo",
        info="Estimativa híbrida: receitas já recebidas (base pagamento, até ontem) + receitas pendentes com vencimento no mês (base vencimento, a partir de hoje)."),
        unsafe_allow_html=True)
    c3.markdown(kpi_card("Saidas de Caixa",      m["saidas"],   cor="negativo",
        info="Estimativa híbrida: despesas já pagas (base pagamento, até ontem) + despesas pendentes com vencimento no mês (base vencimento, a partir de hoje)."),
        unsafe_allow_html=True)
    c4.markdown(kpi_card("Caixa Liquido",        m["liquido"],  cor="auto",
        info="Entradas menos Saídas do período. Positivo indica geração de caixa; negativo indica consumo."),
        unsafe_allow_html=True)
    c5.markdown(kpi_card(
        "Runway",
        f"{m['runway']} dias" if m["runway"] < 999 else "Sem previsao",
        prefix="", cor="neutro",
        info="Dias que o saldo atual sustenta as despesas comprometidas futuras. Calculado sem filtros — considera a empresa inteira.",
    ), unsafe_allow_html=True)
    c6.markdown(kpi_card("Necessidade de Funding", m["funding_date"], prefix="", cor="neutro",
        info="Data estimada em que o caixa se esgota com base nas saídas comprometidas. Exibe \"Sem previsão\" se não há risco iminente. Sem filtros."),
        unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Projecoes manuais ─────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:0 0 8px 0;'>Projecoes Manuais</div>",
        unsafe_allow_html=True,
    )

    # Edit state
    editando_id = st.session_state.get("_cen_editando")

    with st.expander("Adicionar projecao" if not editando_id else "Editar projecao", expanded=bool(editando_id)):
        proj_edit = next((p for p in projecoes if p["id"] == editando_id), {}) if editando_id else {}
        with st.form("form_projecao", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            nome = col_a.text_input("Identificacao", value=proj_edit.get("nome", ""))
            tipo = col_b.selectbox("Tipo", ["entrada", "saida"],
                                   index=["entrada", "saida"].index(proj_edit.get("tipo", "entrada")))

            # Defaults: normalize existing value to first-of-month
            hoje_ts  = pd.Timestamp.today().normalize()
            anos_opt = list(range(hoje_ts.year - 1, hoje_ts.year + 6))
            if "data_ini" in proj_edit:
                _ts_ini = pd.Timestamp(proj_edit["data_ini"])
                def_mes_ini, def_ano_ini = _ts_ini.month, _ts_ini.year
            else:
                def_mes_ini, def_ano_ini = hoje_ts.month, hoje_ts.year
            if "data_fim" in proj_edit:
                _ts_fim = pd.Timestamp(proj_edit["data_fim"])
                def_mes_fim, def_ano_fim = _ts_fim.month, _ts_fim.year
            else:
                def_mes_fim, def_ano_fim = hoje_ts.month, hoje_ts.year

            col_c, col_d, col_e, col_f, col_g = st.columns([1.2, 1, 1.2, 1, 1.5])
            mes_ini  = col_c.selectbox("Mes inicio",  list(MESES_PT.keys()),
                                       format_func=lambda m: MESES_PT[m],
                                       index=def_mes_ini - 1)
            ano_ini  = col_d.selectbox("Ano inicio",  anos_opt,
                                       index=anos_opt.index(def_ano_ini) if def_ano_ini in anos_opt else 0)
            mes_fim  = col_e.selectbox("Mes fim",     list(MESES_PT.keys()),
                                       format_func=lambda m: MESES_PT[m],
                                       index=def_mes_fim - 1)
            ano_fim  = col_f.selectbox("Ano fim",     anos_opt,
                                       index=anos_opt.index(def_ano_fim) if def_ano_fim in anos_opt else 0)
            valor_mensal = col_g.number_input("Valor mensal (R$)", min_value=0.01, step=100.0,
                                              value=float(proj_edit.get("valor_mensal", 1000.0)),
                                              format="%.2f")

            data_ini = pd.Timestamp(ano_ini, mes_ini, 1)
            data_fim = pd.Timestamp(ano_fim, mes_fim, 1)

            # Total preview
            if data_ini <= data_fim:
                n_meses = (data_fim.year - data_ini.year) * 12 + (data_fim.month - data_ini.month) + 1
                st.caption(f"Total projetado: R$ {valor_mensal * n_meses:,.2f} ({n_meses} meses)")

            col_btn1, col_btn2 = st.columns([1, 3])
            submitted = col_btn1.form_submit_button("Salvar")
            cancelado = col_btn2.form_submit_button("Cancelar") if editando_id else False

            if cancelado:
                st.session_state["_cen_editando"] = None
                st.rerun()

            if submitted and nome and data_ini <= data_fim:
                ini_str = data_ini.strftime("%Y-%m-01")
                fim_str = data_fim.strftime("%Y-%m-01")
                if editando_id:
                    for p in projecoes:
                        if p["id"] == editando_id:
                            p.update({"nome": nome, "tipo": tipo,
                                      "data_ini": ini_str, "data_fim": fim_str,
                                      "valor_mensal": valor_mensal})
                    st.session_state["_cen_editando"] = None
                else:
                    projecoes.append({
                        "id": str(uuid.uuid4()),
                        "nome": nome, "tipo": tipo,
                        "data_ini": ini_str, "data_fim": fim_str,
                        "valor_mensal": valor_mensal,
                    })
                cen["projecoes"] = projecoes
                _save_cenarios(cen)
                st.rerun()

    # Table of existing projections
    if projecoes:
        header = st.columns([3, 1.5, 1.5, 1.5, 1.5, 1.5, 1])
        for lbl, col in zip(["Identificacao", "Tipo", "Data Inicial", "Data Final",
                              "Valor Mensal", "Total Projetado", ""], header):
            col.markdown(
                f"<div style='font-size:0.68rem;font-weight:600;color:{TEXT_SECONDARY};"
                f"text-transform:uppercase;letter-spacing:0.05em;padding:4px 0;'>{lbl}</div>",
                unsafe_allow_html=True,
            )
        for p in projecoes:
            ini = pd.Timestamp(p["data_ini"])
            fim = pd.Timestamp(p["data_fim"])
            n_m = (fim.year - ini.year) * 12 + (fim.month - ini.month) + 1
            total = p["valor_mensal"] * n_m
            tipo_lbl = "Entrada" if p["tipo"] == "entrada" else "Saida"
            cor_tipo = GREEN if p["tipo"] == "entrada" else RED
            row = st.columns([3, 1.5, 1.5, 1.5, 1.5, 1.5, 1])
            row[0].markdown(f"<div style='font-size:0.875rem;padding:6px 0;'>{p['nome']}</div>",
                            unsafe_allow_html=True)
            row[1].markdown(f"<div style='font-size:0.875rem;color:{cor_tipo};padding:6px 0;'>{tipo_lbl}</div>",
                            unsafe_allow_html=True)
            row[2].markdown(f"<div style='font-size:0.875rem;padding:6px 0;'>{ini.strftime('%m/%Y')}</div>",
                            unsafe_allow_html=True)
            row[3].markdown(f"<div style='font-size:0.875rem;padding:6px 0;'>{fim.strftime('%m/%Y')}</div>",
                            unsafe_allow_html=True)
            row[4].markdown(f"<div style='font-size:0.875rem;padding:6px 0;'>{fmt_brl(p['valor_mensal'])}</div>",
                            unsafe_allow_html=True)
            row[5].markdown(f"<div style='font-size:0.875rem;padding:6px 0;'>{fmt_brl(total)}</div>",
                            unsafe_allow_html=True)
            btn_col1, btn_col2 = row[6].columns(2)
            if btn_col1.button("✎", key=f"edit_{p['id']}", help="Editar"):
                st.session_state["_cen_editando"] = p["id"]
                st.rerun()
            if btn_col2.button("✕", key=f"del_{p['id']}", help="Excluir"):
                cen["projecoes"] = [x for x in projecoes if x["id"] != p["id"]]
                _save_cenarios(cen)
                st.rerun()
    else:
        st.markdown(
            f"<div style='font-size:0.82rem;color:{TEXT_SECONDARY};padding:8px 0 16px 0;'>"
            "Nenhuma projecao cadastrada.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Renovacao automatica ──────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:0 0 8px 0;'>Renovacao Automatica de Contratos</div>",
        unsafe_allow_html=True,
    )

    col_tog, _ = st.columns([2, 4])
    novo_renov = col_tog.toggle("Ativar premissa de renovacao", value=renov_ativa, key="_cen_renov")
    if novo_renov != renov_ativa:
        cen["renovacao_ativa"] = novo_renov
        _save_cenarios(cen)
        st.rerun()

    todos_elegiveis = _contratos_elegiveis(data, [])  # all eligible (before exclusions)
    elegiveis_ativos = _contratos_elegiveis(data, excluidos)  # after exclusions

    if todos_elegiveis.empty:
        st.markdown(
            f"<div style='font-size:0.82rem;color:{TEXT_SECONDARY};padding:4px 0 12px 0;'>"
            "Nenhum contrato elegivel identificado para renovacao automatica.</div>",
            unsafe_allow_html=True,
        )
    else:
        with st.expander(f"{len(elegiveis_ativos)} contrato(s) na premissa — {len(todos_elegiveis)} elegivel(is) no total"):
            hdr = st.columns([3, 2, 2, 2, 1])
            for lbl, col in zip(["Cliente", "Inicio Renovacao", "Fim Renovacao", "Valor Mensal", ""], hdr):
                col.markdown(
                    f"<div style='font-size:0.68rem;font-weight:600;color:{TEXT_SECONDARY};"
                    f"text-transform:uppercase;letter-spacing:0.05em;padding:4px 0;'>{lbl}</div>",
                    unsafe_allow_html=True,
                )
            for _, row in todos_elegiveis.iterrows():
                excluido = row["id"] in excluidos
                r = st.columns([3, 2, 2, 2, 1])
                nome_c = str(row.get("cliente.nome", row["id"]))
                ini_r  = row["competencia_inicio_renovacao"]
                fim_r  = row["competencia_fim_renovacao"]
                val_r  = float(row["valor_base_renovacao_num"])
                cor_nome = TEXT_SECONDARY if excluido else TEXT_PRIMARY
                r[0].markdown(f"<div style='font-size:0.875rem;color:{cor_nome};padding:6px 0;'>"
                              f"{'~~' if excluido else ''}{nome_c}{'~~' if excluido else ''}</div>",
                              unsafe_allow_html=True)
                r[1].markdown(f"<div style='font-size:0.875rem;color:{cor_nome};padding:6px 0;'>"
                              f"{ini_r.strftime('%m/%Y') if pd.notna(ini_r) else '—'}</div>",
                              unsafe_allow_html=True)
                r[2].markdown(f"<div style='font-size:0.875rem;color:{cor_nome};padding:6px 0;'>"
                              f"{fim_r.strftime('%m/%Y') if pd.notna(fim_r) else '—'}</div>",
                              unsafe_allow_html=True)
                r[3].markdown(f"<div style='font-size:0.875rem;color:{cor_nome};padding:6px 0;'>"
                              f"{fmt_brl(val_r)}</div>", unsafe_allow_html=True)
                btn_lbl = "Ativar" if excluido else "Desativar"
                r[4].markdown(
                    "<style>div[data-testid='stButton'] button{font-size:0.72rem!important;"
                    "padding:4px 8px!important;}</style>",
                    unsafe_allow_html=True,
                )
                if r[4].button(btn_lbl, key=f"excl_{row['id']}"):
                    if excluido:
                        cen["contratos_excluidos"] = [x for x in excluidos if x != row["id"]]
                    else:
                        cen["contratos_excluidos"] = excluidos + [row["id"]]
                    _save_cenarios(cen)
                    st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Graficos ──────────────────────────────────────────────────────────────
    excluidos_ef = excluidos if renov_ativa else list(todos_elegiveis["id"]) if not todos_elegiveis.empty else []

    cr_f   = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp_f   = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    real_f = _filtrar_categoria_real(_filtrar_centro_real(data["realizado"].copy(), centros_sel), cats_sel)

    proj_df  = _projecoes_serie_mensal(projecoes, ano)
    renov_df = _renovacao_serie_mensal(data, excluidos_ef, ano)

    g1, g2 = st.columns([3, 2])
    with g1:
        saldo_ancora = _saldo_anchor_para_mes(data, data["saldos"]["saldo_atual"].sum(), ano, mes)
        serie_dia = _serie_diaria_hibrida(real_f, cr_f, cp_f, ano, mes, saldo_ancora)
        # Overlay projections on daily chart (monthly distribution for selected month)
        m_ini_ts = pd.Timestamp(ano, mes, 1)
        m_fim_ts = m_ini_ts + pd.offsets.MonthEnd(0)
        extra_ent_mes = proj_df.loc[mes, "entradas"] + (renov_df.loc[mes, "entradas"] if renov_ativa else 0.0)
        extra_sai_mes = proj_df.loc[mes, "saidas"]
        # Distribute evenly across future days in the month
        hoje_ts = pd.Timestamp.today().normalize()
        futuros = [d for d in serie_dia.index if d > hoje_ts]
        if futuros and (extra_ent_mes > 0 or extra_sai_mes > 0):
            n_fut = len(futuros)
            for d in futuros:
                serie_dia.loc[d, "entradas"] += extra_ent_mes / n_fut
                serie_dia.loc[d, "saidas"]   += extra_sai_mes / n_fut
            # Recompute saldo from today onwards
            for i, d in enumerate(serie_dia.index):
                if d > hoje_ts:
                    prev = serie_dia.index[serie_dia.index.get_loc(d) - 1]
                    delta = serie_dia.loc[d, "entradas"] - serie_dia.loc[d, "saidas"]
                    serie_dia.loc[d, "saldo"] = serie_dia.loc[prev, "saldo"] + delta
        render_chart(fig_fluxo_diario(serie_dia, f"Fluxo de Caixa com Cenario — {MESES_PT[mes]} {ano}"))
    with g2:
        proj_4m = calc_proj_4_meses_cenario(data, projecoes, excluidos_ef, centros_sel=centros_sel, cats_sel=cats_sel)
        render_chart(fig_proj_4_meses(proj_4m))

    st.markdown("<br>", unsafe_allow_html=True)

    ultima_real_global = data["realizado"]["data_pagamento"].dt.normalize().dropna().max()
    saldo_anchor_ano   = _saldo_anchor_para_ano(data, m["saldo_atual"], ano)
    serie_anual = _serie_mensal_com_cenario(
        real_f, cr_f, cp_f, ano, saldo_anchor_ano, ultima_real_global,
        proj_df, renov_df if renov_ativa else pd.DataFrame({"entradas": pd.Series(0.0, index=range(1,13)),
                                                             "saidas": pd.Series(0.0, index=range(1,13))}),
    )
    render_chart(fig_fluxo_mensal(serie_anual, ano, f"Fluxo de Caixa com Cenario — Anual {ano}"))


def pagina_receita(data: dict, ano: int, mes: int, centros_sel: list[str], centro_label: str,
                   cats_sel: list[str], cat_label: str) -> None:
    _page_header(
        "Receita e Eficiencia",
        "Indicadores de receita recorrente e nao recorrente",
        ano, mes, centro_label, cat_label,
    )
    r = calc_receita(data, ano, mes, centros_sel, cats_sel)

    c1, c2, c3 = st.columns(3)
    c1.markdown(kpi_card("MRR — Receita Recorrente Mensal", r["mrr"],           cor="positivo",
        info="Soma das vendas registradas no Conta Azul com data no mês selecionado. Representa a receita recorrente gerada por contratos."),
        unsafe_allow_html=True)
    c2.markdown(kpi_card("ARR — Receita Recorrente Anual",  r["arr"],           cor="positivo",
        info="MRR × 12. Projeção anualizada da receita recorrente com base no mês atual."),
        unsafe_allow_html=True)
    c3.markdown(kpi_card("Receita Nao Recorrente",          r["nao_recorrente"],
        info="Diferença entre o total de CR por competência e o MRR do período. Representa receitas pontuais ou avulsas fora dos contratos recorrentes."),
        unsafe_allow_html=True)

    r1, r2, _ = st.columns(3)
    r1.markdown(kpi_card("Ticket Medio por Cliente", r["ticket_medio"],          cor="positivo",
        info="MRR dividido pelo número de clientes únicos com venda no mês. Indica o valor médio de receita recorrente gerada por cliente ativo."),
        unsafe_allow_html=True)
    r2.markdown(kpi_card("Clientes Ativos no Mes",   str(r["n_clientes"]), prefix="", cor="neutro",
        info="Clientes distintos com pelo menos uma venda registrada no mês (base fato_vendas)."),
        unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    render_chart(fig_mrr(r["mrr_serie"], ano))

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:18px 0 6px 0;'>"
        f"Top 5 Maiores Clientes — CR | {MESES_PT[mes]} {ano}</div>",
        unsafe_allow_html=True,
    )
    cr_f = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    ranking_cli = _ranking_clientes_cr(cr_f, ano, mes)
    if ranking_cli.empty:
        st.info("Nenhuma receita a receber com vencimento no período selecionado.")
    else:
        display_cli = ranking_cli.copy()
        display_cli["Valor"] = display_cli["Valor"].apply(
            lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
        st.dataframe(
            display_cli,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Cliente": st.column_config.TextColumn("Cliente", width="medium"),
                "Valor": st.column_config.TextColumn("Valor Nominal", width="small"),
                "% do CR": st.column_config.ProgressColumn(
                    "% do CR do Período", format="%.1f%%", min_value=0, max_value=100
                ),
            },
        )


# ─── DRE ───────────────────────────────────────────────────────────────────────
def _explode_e_mapear_dre(df: pd.DataFrame, dim_cat: pd.DataFrame, valor_col: str = "total") -> pd.DataFrame:
    """Explode categorias JSON e junta com dim_categoria para mapeamento DRE."""
    if df.empty:
        return pd.DataFrame(columns=["valor", "dre_grupo", "entrada_dre"])
    cat_map = dim_cat.set_index("id")[["dre_grupo", "entrada_dre"]].to_dict("index")
    rows = []
    for _, row in df.iterrows():
        valor = pd.to_numeric(row.get(valor_col), errors="coerce")
        if pd.isna(valor):
            valor = 0.0
        cats = row.get("categorias") or []
        cats = [c for c in cats if isinstance(c, dict) and c.get("id")]
        if cats:
            v_por_cat = valor / len(cats)
            for c in cats:
                mapping = cat_map.get(c["id"], {})
                rows.append({
                    "valor": v_por_cat,
                    "dre_grupo": mapping.get("dre_grupo"),
                    "entrada_dre": mapping.get("entrada_dre"),
                })
        else:
            rows.append({"valor": valor, "dre_grupo": None, "entrada_dre": None})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["valor", "dre_grupo", "entrada_dre"])


def calc_dre(data: dict, ano: int, centros_sel: list[str], cats_sel: list[str]) -> dict:
    """Calcula o DRE anual (base competência) e as métricas da Regra dos 40."""
    cr = _filtrar_categoria(_filtrar_centro(data["cr"].copy(), centros_sel), cats_sel)
    cp = _filtrar_categoria(_filtrar_centro(data["cp"].copy(), centros_sel), cats_sel)
    dim_cat = data["categorias"]

    cr_ano = cr[cr["data_competencia"].dt.year == ano]
    cp_ano = cp[cp["data_competencia"].dt.year == ano]

    cr_dre = _explode_e_mapear_dre(cr_ano, dim_cat)
    cp_dre = _explode_e_mapear_dre(cp_ano, dim_cat)

    def _s(df: pd.DataFrame, grupo: str) -> float:
        if df.empty:
            return 0.0
        return float(df.loc[df["dre_grupo"] == grupo, "valor"].sum())

    receita_bruta         = _s(cr_dre, "Receitas Operacionais")
    deducoes              = _s(cp_dre, "Deduções da Receita Bruta")
    receita_liquida       = receita_bruta - deducoes
    custos_operacionais   = _s(cp_dre, "Custos Operacionais")
    lucro_bruto           = receita_liquida - custos_operacionais
    despesas_operacionais = _s(cp_dre, "Despesas Operacionais")
    ebitda                = lucro_bruto - despesas_operacionais
    resultado_financeiro  = (_s(cr_dre, "Receitas e Despesas Financeiras") -
                             _s(cp_dre, "Receitas e Despesas Financeiras"))
    outras_nao_oper       = (_s(cr_dre, "Outras Receitas e Despesas Não Operacionais") -
                             _s(cp_dre, "Outras Receitas e Despesas Não Operacionais"))
    lucro_liquido         = ebitda + resultado_financeiro + outras_nao_oper

    vnd = data["vendas"].copy()
    vnd["data"] = pd.to_datetime(vnd["data"], errors="coerce")
    mrr_atual    = vnd[vnd["data"].dt.year == ano]["total"].sum()
    mrr_anterior = vnd[vnd["data"].dt.year == (ano - 1)]["total"].sum()
    crescimento_mrr = ((mrr_atual / mrr_anterior) - 1) * 100 if mrr_anterior > 0 else None
    margem_ebitda   = (ebitda / receita_bruta * 100) if receita_bruta > 0 else 0.0
    score_r40       = (crescimento_mrr + margem_ebitda) if crescimento_mrr is not None else None

    return dict(
        receita_bruta=receita_bruta,
        deducoes=deducoes,
        receita_liquida=receita_liquida,
        custos_operacionais=custos_operacionais,
        lucro_bruto=lucro_bruto,
        despesas_operacionais=despesas_operacionais,
        ebitda=ebitda,
        resultado_financeiro=resultado_financeiro,
        outras_nao_oper=outras_nao_oper,
        lucro_liquido=lucro_liquido,
        crescimento_mrr=crescimento_mrr,
        margem_ebitda=margem_ebitda,
        score_r40=score_r40,
    )


def _dre_fmt_brl(v: float) -> str:
    return f"R$ {abs(v):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _dre_pct(v: float, base: float) -> str:
    return f"{v / base * 100:.1f}%" if base else "—"


def _dre_row(label: str, valor: float, base: float, kind: str = "item") -> str:
    brl = _dre_fmt_brl(valor)
    pct = _dre_pct(valor, base)
    if kind == "subtotal":
        cor = GREEN if valor >= 0 else RED
        return (
            f"<tr style='background:#f5f5f7;border-top:1px solid {BORDER};border-bottom:1px solid {BORDER};'>"
            f"<td style='font-weight:700;padding:10px 14px;color:{TEXT_PRIMARY};'>{label}</td>"
            f"<td style='font-weight:700;text-align:right;padding:10px 14px;color:{cor};white-space:nowrap;'>{brl}</td>"
            f"<td style='font-weight:700;text-align:right;padding:10px 14px;color:{TEXT_SECONDARY};'>{pct}</td>"
            f"</tr>"
        )
    else:
        return (
            f"<tr>"
            f"<td style='padding:8px 14px 8px 28px;color:{TEXT_SECONDARY};'>{label}</td>"
            f"<td style='text-align:right;padding:8px 14px;color:{TEXT_PRIMARY};white-space:nowrap;'>{brl}</td>"
            f"<td style='text-align:right;padding:8px 14px;color:{TEXT_SECONDARY};'>{pct}</td>"
            f"</tr>"
        )


def pagina_dre(data: dict, ano: int, mes: int, centros_sel: list[str], centro_label: str,
               cats_sel: list[str], cat_label: str) -> None:
    _page_header("DRE", f"Demonstrativo de Resultado do Exercicio — {ano}", ano, mes, centro_label, cat_label)

    d  = calc_dre(data, ano, centros_sel, cats_sel)
    rb = d["receita_bruta"] if d["receita_bruta"] else 1.0

    # ── Card Regra dos 40 ─────────────────────────────────────────────────────
    cresc_str = f"{d['crescimento_mrr']:+.1f}%" if d["crescimento_mrr"] is not None else "N/D"
    marg_str  = f"{d['margem_ebitda']:+.1f}%"
    if d["score_r40"] is not None:
        sv         = d["score_r40"]
        score_str  = f"{sv:.0f}%"
        score_cor  = GREEN if sv >= 40 else RED
        status_str = "&#10003; Acima de 40%" if sv >= 40 else "&#10005; Abaixo de 40%"
    else:
        score_str  = "N/D"
        score_cor  = TEXT_SECONDARY
        status_str = "Sem dados do ano anterior"

    despesa_nao_oper = d["resultado_financeiro"] + d["outras_nao_oper"]
    _cor_oper = "negativo" if d["despesas_operacionais"] > 0 else "positivo"
    _cor_nao  = "negativo" if despesa_nao_oper > 0 else "positivo"

    st.markdown(f"""
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;align-items:stretch;">
        <div class="kpi-card" style="padding:22px;">
            <div class="kpi-label" style="margin-bottom:16px;font-size:0.75rem;">
                Regra dos 40 &mdash; {ano} vs {ano - 1}
            </div>
            <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;">
                <div>
                    <div style="font-size:0.65rem;color:{TEXT_SECONDARY};text-transform:uppercase;
                                letter-spacing:0.07em;margin-bottom:4px;">Crescimento MRR</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{TEXT_PRIMARY};">{cresc_str}</div>
                </div>
                <div style="font-size:1.6rem;color:{BORDER};font-weight:300;">+</div>
                <div>
                    <div style="font-size:0.65rem;color:{TEXT_SECONDARY};text-transform:uppercase;
                                letter-spacing:0.07em;margin-bottom:4px;">Margem EBITDA</div>
                    <div style="font-size:1.4rem;font-weight:700;color:{TEXT_PRIMARY};">{marg_str}</div>
                </div>
                <div style="font-size:1.6rem;color:{BORDER};font-weight:300;">=</div>
                <div>
                    <div style="font-size:0.65rem;color:{TEXT_SECONDARY};text-transform:uppercase;
                                letter-spacing:0.07em;margin-bottom:4px;">Score</div>
                    <div style="font-size:2.2rem;font-weight:800;color:{score_cor};
                                line-height:1;">{score_str}</div>
                    <div style="font-size:0.65rem;color:{score_cor};margin-top:4px;">{status_str}</div>
                </div>
            </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:12px;">
            <div class="kpi-card" style="flex:1;margin:0;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div class="kpi-label" style="min-width:0;flex:1;">Despesas Operacionais</div>
                    <details class="kpi-info">
                        <summary>i</summary>
                        <div class="kpi-info-box">Despesas operacionais por competência no ano, mapeadas pelo DRE do Conta Azul. Inclui G&amp;A, marketing, tecnologia, pró-labore e similares. Não inclui Custos de Serviço (CSP).</div>
                    </details>
                </div>
                <div class="kpi-value {_cor_oper}">{_dre_fmt_brl(d["despesas_operacionais"])}</div>
                <div style="font-size:0.72rem;color:{TEXT_SECONDARY};margin-top:4px;">
                    {_dre_pct(d["despesas_operacionais"], rb)} da Receita Bruta
                </div>
            </div>
            <div class="kpi-card" style="flex:1;margin:0;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div class="kpi-label" style="min-width:0;flex:1;">Despesas Nao-Operacionais</div>
                    <details class="kpi-info">
                        <summary>i</summary>
                        <div class="kpi-info-box">Despesas fora do core operacional no ano: financeiras, investimentos, IRPJ/CSLL e categorias sem mapeamento DRE. Complemento das Despesas Operacionais.</div>
                    </details>
                </div>
                <div class="kpi-value {_cor_nao}">{_dre_fmt_brl(despesa_nao_oper)}</div>
                <div style="font-size:0.72rem;color:{TEXT_SECONDARY};margin-top:4px;">
                    {_dre_pct(despesa_nao_oper, rb)} da Receita Bruta
                </div>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tabela DRE ────────────────────────────────────────────────────────────
    st.markdown(
        f"<div style='font-size:0.68rem;color:{TEXT_SECONDARY};text-transform:uppercase;"
        f"letter-spacing:0.08em;font-weight:600;margin:18px 0 8px 0;'>"
        f"Demonstrativo de Resultado — {ano} (base competencia)</div>",
        unsafe_allow_html=True,
    )

    linhas = [
        _dre_row("RECEITA BRUTA", d["receita_bruta"], rb, "subtotal"),
        _dre_row("(&ndash;) Deduções da Receita Bruta", d["deducoes"], rb),
        _dre_row("= RECEITA LÍQUIDA", d["receita_liquida"], rb, "subtotal"),
        _dre_row("(&ndash;) Custos Operacionais (CSP)", d["custos_operacionais"], rb),
        _dre_row("= LUCRO BRUTO", d["lucro_bruto"], rb, "subtotal"),
        _dre_row("(&ndash;) Despesas Operacionais", d["despesas_operacionais"], rb),
        _dre_row("= EBITDA", d["ebitda"], rb, "subtotal"),
        _dre_row("(&plusmn;) Resultado Financeiro", d["resultado_financeiro"], rb),
        _dre_row("(&plusmn;) Outras Receitas / Despesas", d["outras_nao_oper"], rb),
        _dre_row("= LUCRO LÍQUIDO", d["lucro_liquido"], rb, "subtotal"),
    ]

    st.markdown(f"""
    <table style='width:100%;border-collapse:collapse;font-size:0.875rem;
                  border:1px solid {BORDER};border-radius:8px;overflow:hidden;'>
        <thead>
            <tr style='background:{BLUE};color:#fff;'>
                <th style='text-align:left;padding:10px 14px;font-weight:600;'>Linha</th>
                <th style='text-align:right;padding:10px 14px;font-weight:600;'>Valor</th>
                <th style='text-align:right;padding:10px 14px;font-weight:600;'>% Receita Bruta</th>
            </tr>
        </thead>
        <tbody>{"".join(linhas)}</tbody>
    </table>""", unsafe_allow_html=True)


# ─── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    _flush_cookie_write()
    _run_auth()
    _flush_cookie_write()
    data = load_data()

    # Guarda mínima: se as tabelas principais ainda não existem no banco, exibe aviso claro.
    if "cr" not in data or "cp" not in data:
        st.warning(
            "Os dados ainda não foram carregados no banco de dados. "
            "Execute o pipeline ETL manualmente no GitHub Actions para popular o Supabase.",
            icon="⚠️",
        )
        st.stop()

    with st.sidebar:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
            st.markdown("<br>", unsafe_allow_html=True)
        else:
            st.markdown(
                f"<div style='font-size:2.2rem;font-weight:800;color:{TEXT_PRIMARY};"
                "letter-spacing:-0.03em;padding:8px 0 0 0;'>Scua</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            f"<div class='sidebar-subtitle'>Finance Dashboard</div>",
            unsafe_allow_html=True,
        )

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section">Paginas</div>', unsafe_allow_html=True)

        paginas = {
            "Resumo de Caixa":       "resumo",
            "Cenarios de Caixa":     "cenarios",
            "Receita e Eficiencia":  "receita",
            "DRE":                   "dre",
        }
        pagina_label = st.radio("nav", list(paginas.keys()), label_visibility="collapsed")
        pagina = paginas[pagina_label]

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-section">Filtros</div>', unsafe_allow_html=True)

        anos_disp = sorted(
            data["cr"]["data_vencimento"].dt.year.dropna().unique().astype(int).tolist(),
            reverse=True,
        )
        ano_atual = pd.Timestamp.today().year
        idx_ano   = anos_disp.index(ano_atual) if ano_atual in anos_disp else 0
        ano = st.selectbox("Ano", anos_disp, index=idx_ano)
        mes_atual = pd.Timestamp.today().month
        mes = st.selectbox("Mes", list(MESES_PT.keys()), format_func=lambda m: MESES_PT[m], index=mes_atual - 1)

        centros_lista = sorted(data["centros"]["nome"].dropna().tolist())
        centro_raw    = st.selectbox("Centro de Custo", ["Todos os centros"] + centros_lista, index=0)
        centros_sel   = [] if centro_raw == "Todos os centros" else [centro_raw]
        centro_label  = centro_raw

        # ── Categorias (dropdown com checkboxes) ──────────────────────────────
        cats_lista = _extrair_nomes_categoria(data)
        n_total    = len(cats_lista)

        # Inicializa session_state na primeira execucao ou quando a lista muda
        if st.session_state.get("_cats_total") != n_total:
            for c in cats_lista:
                st.session_state[f"_cat_{c}"] = True
            st.session_state["_cat_all"]   = True
            st.session_state["_cats_total"] = n_total

        # Callbacks
        def _on_select_all():
            val = st.session_state["_cat_all"]
            for c in cats_lista:
                st.session_state[f"_cat_{c}"] = val

        def _on_cat_change():
            st.session_state["_cat_all"] = all(
                st.session_state.get(f"_cat_{c}", True) for c in cats_lista
            )

        n_sel       = sum(1 for c in cats_lista if st.session_state.get(f"_cat_{c}", True))
        exp_titulo  = "Todas as categorias" if n_sel == n_total else f"{n_sel} de {n_total} categorias"

        st.markdown(
            f"<div class='sidebar-section'>Categorias</div>",
            unsafe_allow_html=True,
        )
        with st.expander(exp_titulo, expanded=False):
            busca = st.text_input(
                "busca_cat", placeholder="Pesquisar...",
                label_visibility="collapsed", key="_cat_busca",
            )
            st.checkbox(
                "Selecionar tudo", key="_cat_all", on_change=_on_select_all,
            )
            st.markdown(
                f"<div style='height:1px;background:{BORDER};margin:4px 0 6px 0;'></div>",
                unsafe_allow_html=True,
            )
            termo = busca.lower() if busca else ""
            cats_visiveis = [c for c in cats_lista if termo in c.lower()] if termo else cats_lista
            scroll = st.container(height=260)
            with scroll:
                for c in cats_visiveis:
                    st.checkbox(c, key=f"_cat_{c}", on_change=_on_cat_change)

        # Monta lista efetiva para filtro
        cats_sel_raw = [c for c in cats_lista if st.session_state.get(f"_cat_{c}", True)]
        if len(cats_sel_raw) == n_total:
            cats_sel  = []
            cat_label = "Todas as categorias"
        elif not cats_sel_raw:
            cats_sel  = []
            cat_label = "Nenhuma categoria"
        else:
            cats_sel  = cats_sel_raw
            cat_label = f"{len(cats_sel)} de {n_total} categorias"

        st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
        ultima_data = data["realizado"]["data_pagamento"].max()
        st.markdown(
            f"<div style='font-size:0.70rem;color:{TEXT_SECONDARY};line-height:1.6;'>"
            f"Ultima atualizacao<br>"
            f"<strong style='color:{TEXT_PRIMARY};'>{ultima_data.strftime('%d/%m/%Y')}</strong></div>",
            unsafe_allow_html=True,
        )

        # ── Logout (exibido apenas quando auth está ativa) ────────────────────
        if st.session_state.get("_username"):
            st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
            nome_exib = st.session_state.get("_display_name", st.session_state["_username"])
            st.markdown(
                f"<div style='font-size:0.70rem;color:{TEXT_SECONDARY};margin-bottom:8px;line-height:1.5;'>"
                f"Conectado como<br>"
                f"<strong style='color:{TEXT_PRIMARY};'>{nome_exib}</strong></div>",
                unsafe_allow_html=True,
            )
            if st.button("Sair", use_container_width=True):
                _revoke_remember_token()
                _queue_cookie_write("clear")
                _clear_authenticated_session()
                st.rerun()

    kwargs = dict(data=data, ano=int(ano), mes=int(mes),
                  centros_sel=centros_sel, centro_label=centro_label,
                  cats_sel=cats_sel, cat_label=cat_label)

    if pagina == "resumo":
        pagina_resumo(**kwargs)
    elif pagina == "cenarios":
        pagina_cenarios(**kwargs)
    elif pagina == "receita":
        pagina_receita(**kwargs)
    elif pagina == "dre":
        pagina_dre(**kwargs)


if __name__ == "__main__":
    main()
