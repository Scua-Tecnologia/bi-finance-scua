from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pandas as pd

from contaazul_bi.client import ContaAzulClient
from contaazul_bi.config import Settings, PAID_STATUSES_INSTALLMENTS as PAID_STATUSES
from contaazul_bi.extractors.contracts import ContractsExtractor
from contaazul_bi.extractors.finance import FinanceExtractor
from contaazul_bi.extractors.invoices import InvoiceExtractor
from contaazul_bi.extractors.people import PeopleExtractor
from contaazul_bi.extractors.sales import SalesExtractor
from contaazul_bi.logging_utils import setup_logging
from contaazul_bi.oauth import ContaAzulOAuthManager
from contaazul_bi.transformers.analytics import build_analytics
from contaazul_bi.utils import dump_json, write_dataframe


logger = logging.getLogger(__name__)


class ContaAzulETLPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.oauth_manager = ContaAzulOAuthManager(settings)
        self.client = ContaAzulClient(settings, self.oauth_manager)
        self.finance = FinanceExtractor(self.client)
        self.people = PeopleExtractor(self.client)
        self.sales = SalesExtractor(self.client)
        self.contracts = ContractsExtractor(self.client)
        self.invoices = InvoiceExtractor(self.client)

    @staticmethod
    def _parse_redirected_url(redirected_url: str) -> tuple[str | None, str | None, str | None, str | None]:
        parsed = urlparse(redirected_url)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        returned_state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]
        error_description = query.get("error_description", [None])[0]
        return code, returned_state, error, error_description

    def authorize(
        self,
        open_browser: bool = True,
        *,
        redirected_url: str | None = None,
        code: str | None = None,
    ) -> None:
        if code:
            self.oauth_manager.exchange_code_for_tokens(code)
            logger.info("Autorização concluída com sucesso a partir de código informado diretamente.")
            print("\nAutorização concluída com sucesso.\n")
            return

        expected_state: str | None = None
        if redirected_url is None:
            auth_url, expected_state = self.oauth_manager.build_authorization_url()
            logger.info("URL de autorização gerada.")

            print("\nAbra a URL abaixo no navegador e autorize o aplicativo:\n")
            print(auth_url)
            print()
            print(
                "Depois da autorização, o Conta Azul vai redirecionar para a sua URL cadastrada.\n"
                "Copie a URL FINAL completa do navegador e cole no terminal."
            )
            print()

            if open_browser:
                webbrowser.open(auth_url)

        if redirected_url is None:
            redirected_url = input("Cole aqui a URL final completa após a autorização:\n").strip()

        if not redirected_url:
            raise RuntimeError("Nenhuma URL foi informada.")

        code, returned_state, error, error_description = self._parse_redirected_url(redirected_url)

        if error:
            raise RuntimeError(
                f"Autorização recusada ou inválida. error={error}, description={error_description}"
            )

        if not code:
            raise RuntimeError(
                "Nenhum código de autorização foi encontrado na URL informada. "
                "Verifique se você colou a URL final completa do navegador."
            )

        if expected_state and returned_state != expected_state:
            raise RuntimeError(
                "State do OAuth divergente. A autorização foi abortada por segurança."
            )
        if expected_state is None and returned_state:
            logger.warning(
                "URL de redirecionamento informada via argumento recebida sem validacao local do state. "
                "Isso e esperado quando o fluxo ja foi concluido no navegador e o code esta sendo importado depois."
            )

        self.oauth_manager.exchange_code_for_tokens(code)
        logger.info("Autorização inicial concluída com sucesso.")
        print("\nAutorização concluída com sucesso.\n")

    def oauth_status(self, force_refresh: bool = False) -> dict[str, str | bool | None]:
        status = self.oauth_manager.token_status()
        logger.info(
            "OAuth status | token_store=%s | fingerprint=%s | redirect_uri=%s | token_present=%s | "
            "refresh_token_present=%s | access_token_expires_at=%s | access_token_expired=%s",
            status["token_store"],
            status["oauth_config_fingerprint"],
            status["redirect_uri"],
            status["token_present"],
            status["refresh_token_present"],
            status["access_token_expires_at_utc"],
            status["access_token_expired"],
        )
        if force_refresh:
            logger.info("Executando refresh explicito para validar as credenciais OAuth deste ambiente...")
            self.oauth_manager.force_refresh()
            status = self.oauth_manager.token_status()
            logger.info(
                "Refresh OAuth concluido com sucesso | access_token_expires_at=%s",
                status["access_token_expires_at_utc"],
            )
        return status

    @staticmethod
    def _candidate_installment_id_columns(frame: pd.DataFrame) -> list[str]:
        candidates = [
            "id_parcela",
            "parcela.id",
            "id",
        ]
        return [column for column in candidates if column in frame.columns]

    @staticmethod
    def _normalize_installment_frame(frame: pd.DataFrame, frame_name: str) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()

        candidate = frame.copy()

        if "data_pagamento" in candidate.columns:
            candidate["data_pagamento"] = pd.to_datetime(candidate["data_pagamento"], errors="coerce")

        if "data_vencimento" in candidate.columns:
            candidate["data_vencimento"] = pd.to_datetime(candidate["data_vencimento"], errors="coerce")

        if "data_competencia" in candidate.columns:
            candidate["data_competencia"] = pd.to_datetime(candidate["data_competencia"], errors="coerce")

        candidate["tipo_evento_financeiro"] = "RECEITA" if frame_name == "contas_receber" else "DESPESA"

        if "id" in candidate.columns and "id_parcela" not in candidate.columns:
            candidate["id_parcela"] = candidate["id"]

        return candidate

    def _recent_installments(self, raw_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        cutoff = pd.to_datetime(self.settings.installment_cutoff_date)
        frames: list[pd.DataFrame] = []

        for frame_name in ["contas_receber", "contas_pagar"]:
            frame = raw_frames.get(frame_name, pd.DataFrame())
            if frame.empty:
                continue

            candidate = self._normalize_installment_frame(frame, frame_name)

            if "data_pagamento" in candidate.columns:
                candidate = candidate[
                    candidate["data_pagamento"].notna()
                    | (
                        candidate.get("data_vencimento", pd.Series(index=candidate.index, dtype="datetime64[ns]")).notna()
                        & (candidate["data_vencimento"] >= cutoff)
                    )
                ]
            elif "data_vencimento" in candidate.columns:
                candidate = candidate[candidate["data_vencimento"].notna() & (candidate["data_vencimento"] >= cutoff)]

            installment_id_columns = self._candidate_installment_id_columns(candidate)
            if not installment_id_columns:
                continue

            chosen_column = installment_id_columns[0]
            candidate = candidate[candidate[chosen_column].notna()].copy()
            if chosen_column != "id_parcela":
                candidate["id_parcela"] = candidate[chosen_column].astype(str)
            else:
                candidate["id_parcela"] = candidate["id_parcela"].astype(str)

            logger.info(
                "Usando a coluna %s como ID de parcela em %s. %s registros elegiveis apos filtros.",
                chosen_column,
                frame_name,
                len(candidate),
            )
            frames.append(candidate)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["id_parcela"])

    @staticmethod
    def _acquittance_candidate_ids(installments: pd.DataFrame) -> list[str]:
        if installments.empty or "id_parcela" not in installments.columns:
            return []

        paid_amount = pd.to_numeric(installments.get("pago"), errors="coerce").fillna(0)
        if "status" in installments.columns:
            status_mask = installments["status"].astype(str).str.upper().isin(PAID_STATUSES)
        else:
            status_mask = pd.Series(False, index=installments.index)

        eligible = installments[status_mask | paid_amount.gt(0)]
        return eligible["id_parcela"].dropna().astype(str).unique().tolist()

    def _recent_installment_ids(self, raw_frames: dict[str, pd.DataFrame]) -> list[str]:
        installments = self._recent_installments(raw_frames)
        if installments.empty or "id_parcela" not in installments.columns:
            return []
        return sorted(set(installments["id_parcela"].dropna().astype(str).unique().tolist()))

    @staticmethod
    def _backfill_missing_contracts(
        contracts: pd.DataFrame,
        sales: pd.DataFrame,
        reference_date: pd.Timestamp,
    ) -> pd.DataFrame:
        if sales.empty or "id_contrato" not in sales.columns:
            return contracts

        sales_with_contract = sales[sales["id_contrato"].notna()].copy()
        if sales_with_contract.empty:
            return contracts

        sales_with_contract["id_contrato"] = sales_with_contract["id_contrato"].astype(str)
        sales_with_contract["data"] = pd.to_datetime(sales_with_contract.get("data"), errors="coerce")

        known_contract_ids: set[str] = set()
        if not contracts.empty and "id" in contracts.columns:
            known_contract_ids = set(contracts["id"].dropna().astype(str))

        missing_contract_sales = sales_with_contract[
            ~sales_with_contract["id_contrato"].isin(known_contract_ids)
        ].copy()
        if missing_contract_sales.empty:
            return contracts

        fallback_rows: list[dict[str, object]] = []
        for contract_id, group in missing_contract_sales.groupby("id_contrato", dropna=True):
            group = group.sort_values("data")
            latest_row = group.iloc[-1]
            future_rows = group[group["data"].notna() & (group["data"] >= reference_date)]
            data_inicio = group["data"].dropna().min()
            proximo_vencimento = future_rows["data"].dropna().min() if not future_rows.empty else pd.NaT
            fallback_rows.append(
                {
                    "id": contract_id,
                    "status": "ATIVO" if not future_rows.empty else "INATIVO",
                    "data_inicio": data_inicio.date().isoformat() if pd.notna(data_inicio) else None,
                    "numero": 0,
                    "cliente.id": latest_row.get("cliente.id"),
                    "cliente.nome": latest_row.get("cliente.nome"),
                    "proximo_vencimento": (
                        proximo_vencimento.date().isoformat() if pd.notna(proximo_vencimento) else None
                    ),
                }
            )

        fallback_contracts = pd.DataFrame(fallback_rows)
        logger.warning(
            "Foram identificados %s contratos referenciados em vendas que nao vieram do endpoint de contratos. "
            "Eles serao incorporados ao dataset a partir das vendas para evitar perda analitica.",
            len(fallback_contracts),
        )

        if contracts.empty:
            return fallback_contracts

        return pd.concat([contracts, fallback_contracts], ignore_index=True, sort=False)

    def run(self) -> dict[str, object]:
        started_at = datetime.now()
        raw_dir = self.settings.output_dir / "raw"
        analytics_dir = self.settings.output_dir / "analytics"
        meta_dir = self.settings.output_dir / "meta"

        raw_frames: dict[str, pd.DataFrame] = {}
        outputs: dict[str, object] = {}

        def extract_step(name: str, func) -> None:
            logger.info("Extraindo %s...", name)
            df = func()
            raw_frames[name] = df
            outputs[name] = {
                "rows": len(df),
                **write_dataframe(df, raw_dir, name),
            }

        extract_step("categorias", self.finance.categories)
        extract_step("categorias_dre", self.finance.dre_categories)
        extract_step("centros_custo", self.finance.cost_centers)
        extract_step("contas_financeiras", self.finance.financial_accounts)

        raw_frames["saldos_contas_financeiras"] = self.finance.financial_account_balances(
            raw_frames["contas_financeiras"]
        )
        outputs["saldos_contas_financeiras"] = {
            "rows": len(raw_frames["saldos_contas_financeiras"]),
            **write_dataframe(raw_frames["saldos_contas_financeiras"], raw_dir, "saldos_contas_financeiras"),
        }

        extract_step("contas_receber", self.finance.accounts_receivable)
        extract_step("contas_pagar", self.finance.accounts_payable)
        extract_step("transferencias", self.finance.transfers)

        if self.settings.enable_people:
            extract_step("pessoas", self.people.people)

        if self.settings.enable_sales:
            extract_step("vendas", self.sales.sales)

        if self.settings.enable_contracts:
            extract_step("contratos", self.contracts.contracts)

        if self.settings.enable_contracts and self.settings.enable_sales:
            raw_frames["contratos"] = self._backfill_missing_contracts(
                raw_frames.get("contratos", pd.DataFrame()),
                raw_frames.get("vendas", pd.DataFrame()),
                pd.Timestamp(self.settings.reference_date),
            )
            outputs["contratos"] = {
                "rows": len(raw_frames["contratos"]),
                **write_dataframe(raw_frames["contratos"], raw_dir, "contratos"),
            }

        if self.settings.enable_invoices:
            try:
                extract_step("notas_fiscais", self.invoices.invoices)
            except Exception as exc:
                logger.warning("Extração de notas fiscais ignorada: %s", exc)

        if self.settings.enable_installment_enrichment:
            installments = self._recent_installments(raw_frames)
            logger.info("Parcelas selecionadas para detalhamento/baixas: %s", len(installments))
            raw_frames["parcelas"] = installments
            outputs["parcelas"] = {
                "rows": len(installments),
                **write_dataframe(installments, raw_dir, "parcelas"),
            }

            if self.settings.enable_acquittances:
                acquittance_ids = self._acquittance_candidate_ids(installments)
                logger.info("Parcelas selecionadas para baixas: %s", len(acquittance_ids))

                acquittances = self.finance.acquittances_by_installment_ids(acquittance_ids)
                raw_frames["baixas"] = acquittances
                outputs["baixas"] = {
                    "rows": len(acquittances),
                    **write_dataframe(acquittances, raw_dir, "baixas"),
                }

        analytics = build_analytics(raw_frames)
        if not self.settings.enable_people:
            analytics.tables.pop("dim_pessoa", None)
        analytics_output: dict[str, object] = {}

        for table_name, table_df in analytics.tables.items():
            analytics_output[table_name] = {
                "rows": len(table_df),
                **write_dataframe(table_df, analytics_dir, table_name),
            }

        database_url = os.getenv("DATABASE_URL")
        if database_url:
            logger.info("Escrevendo tabelas analytics no Supabase...")
            from contaazul_bi.supabase_writer import write_analytics_to_supabase
            write_analytics_to_supabase(analytics.tables, database_url)
        else:
            logger.info("DATABASE_URL não configurada — escrita no Supabase ignorada.")

        zero_row_warnings: list[str] = []
        for table_name, table_info in {**outputs, **analytics_output}.items():
            if isinstance(table_info, dict) and table_info.get("rows", -1) == 0:
                zero_row_warnings.append(table_name)
                logger.warning("Tabela '%s' retornou 0 linhas. Verifique a extração.", table_name)

        finished_at = datetime.now()
        run_summary = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            "zero_row_warnings": zero_row_warnings,
            "raw_outputs": outputs,
            "analytics_outputs": analytics_output,
        }

        dump_json(run_summary, meta_dir / "run_summary.json")
        logger.info("Pipeline concluído com sucesso.")
        return run_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETL Conta Azul para BI financeiro")
    subparsers = parser.add_subparsers(dest="command", required=True)

    authorize_parser = subparsers.add_parser("authorize", help="Executa a autorização OAuth inicial")
    authorize_parser.add_argument("--no-browser", action="store_true", help="Não abre o navegador automaticamente")
    authorize_parser.add_argument(
        "--redirected-url",
        help="URL final de redirecionamento retornada pelo navegador após a autorização",
    )
    authorize_parser.add_argument(
        "--code",
        help="Código OAuth já extraído da URL de redirecionamento",
    )

    oauth_status_parser = subparsers.add_parser("oauth-status", help="Exibe o status do OAuth e pode testar o refresh")
    oauth_status_parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Executa um refresh explícito para validar client_id/client_secret/redirect_uri e o refresh token salvo",
    )
    subparsers.add_parser("run", help="Executa o pipeline de extração e transformação")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    parser = build_parser()
    args = parser.parse_args(argv)

    pipeline = ContaAzulETLPipeline(settings)

    if args.command == "authorize":
        pipeline.authorize(
            open_browser=not args.no_browser,
            redirected_url=args.redirected_url,
            code=args.code,
        )
        return 0

    if args.command == "oauth-status":
        status = pipeline.oauth_status(force_refresh=args.force_refresh)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run":
        pipeline.run()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
