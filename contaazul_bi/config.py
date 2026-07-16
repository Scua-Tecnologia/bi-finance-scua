from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


# ── Status considerados pagos/quitados ──────────────────────────────────────
# ATENÇÃO: os dois conjuntos divergem INTENCIONALMENTE (ou é divergência
# histórica sob revisão — confirmar com o negócio antes de unificar):
#   - parcelas (installments, em main.py) tratam ACQUITTED como pago;
#   - a análise de fluxo (analytics.py) NÃO inclui ACQUITTED no conjunto pago.
# Centralizados aqui como fonte única para evitar drift entre os módulos.
# NÃO unifique os dois sem validar o impacto nos resultados do ETL.
PAID_STATUSES_INSTALLMENTS: frozenset[str] = frozenset({"ACQUITTED", "PAGO", "RECEBIDO", "QUITADO"})
PAID_STATUSES_ANALYTICS: frozenset[str] = frozenset({"PAGO", "RECEBIDO", "QUITADO"})


@dataclass(slots=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str
    auth_base_url: str
    api_base_url: str
    token_store_path: Path
    output_dir: Path
    log_level: str
    timeout_seconds: int
    page_size: int
    lookback_years: int
    lookahead_years: int
    installment_lookback_months: int
    enable_sales: bool
    enable_contracts: bool
    enable_people: bool
    enable_invoices: bool
    enable_installment_enrichment: bool
    enable_acquittances: bool
    reference_date: date = field(default_factory=date.today)

    @classmethod
    def from_env(cls) -> "Settings":
        def _env(name: str, default: str | None = None, required: bool = False) -> str:
            value = os.getenv(name, default)
            if required and not value:
                raise ValueError(f"Variável de ambiente obrigatória não informada: {name}")
            return value or ""

        def _bool(name: str, default: str) -> bool:
            return _env(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}

        settings = cls(
            client_id=_env("CONTA_AZUL_CLIENT_ID", required=True),
            client_secret=_env("CONTA_AZUL_CLIENT_SECRET", required=True),
            redirect_uri=_env("CONTA_AZUL_REDIRECT_URI", "https://scua.com.br/", required=True),
            scope=_env("CONTA_AZUL_SCOPE", "openid profile aws.cognito.signin.user.admin"),
            auth_base_url=_env("CONTA_AZUL_AUTH_BASE_URL", "https://auth.contaazul.com"),
            api_base_url=_env("CONTA_AZUL_API_BASE_URL", "https://api-v2.contaazul.com"),
            token_store_path=Path(_env("CONTA_AZUL_TOKEN_STORE_PATH", ".secrets/conta_azul_tokens.json")),
            output_dir=Path(_env("CONTA_AZUL_OUTPUT_DIR", "output")),
            log_level=_env("CONTA_AZUL_LOG_LEVEL", "INFO"),
            timeout_seconds=int(_env("CONTA_AZUL_TIMEOUT_SECONDS", "60")),
            page_size=int(_env("CONTA_AZUL_PAGE_SIZE", "100")),
            lookback_years=int(_env("CONTA_AZUL_LOOKBACK_YEARS", "3")),
            lookahead_years=int(_env("CONTA_AZUL_LOOKAHEAD_YEARS", "3")),
            installment_lookback_months=int(_env("CONTA_AZUL_INSTALLMENT_LOOKBACK_MONTHS", "12")),
            enable_sales=_bool("CONTA_AZUL_ENABLE_SALES", "true"),
            enable_contracts=_bool("CONTA_AZUL_ENABLE_CONTRACTS", "true"),
            enable_people=_bool("CONTA_AZUL_ENABLE_PEOPLE", "true"),
            enable_invoices=_bool("CONTA_AZUL_ENABLE_INVOICES", "false"),
            enable_installment_enrichment=_bool("CONTA_AZUL_ENABLE_INSTALLMENT_ENRICHMENT", "false"),
            enable_acquittances=_bool("CONTA_AZUL_ENABLE_ACQUITTANCES", "false"),
        )
        settings.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        return settings

    @staticmethod
    def _safe_year_replace(base_date: date, year_delta: int) -> date:
        target_year = base_date.year + year_delta
        try:
            return base_date.replace(year=target_year)
        except ValueError:
            return base_date.replace(year=target_year, month=2, day=28)

    @property
    def dynamic_date_from(self) -> str:
        return self._safe_year_replace(self.reference_date, -self.lookback_years).isoformat()

    @property
    def dynamic_date_to(self) -> str:
        return self._safe_year_replace(self.reference_date, self.lookahead_years).isoformat()

    @property
    def installment_cutoff_date(self) -> str:
        cutoff = self.reference_date - timedelta(days=30 * self.installment_lookback_months)
        return cutoff.isoformat()

    @property
    def financial_window(self) -> dict[str, str]:
        return {
            "data_inicio": self.dynamic_date_from,
            "data_fim": self.dynamic_date_to,
        }

    @property
    def due_date_window(self) -> dict[str, str]:
        return {
            "data_vencimento_de": self.dynamic_date_from,
            "data_vencimento_ate": self.dynamic_date_to,
        }