from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

import pandas as pd

from contaazul_bi.client import ContaAzulClient


logger = logging.getLogger(__name__)


class FinanceExtractor:
    def __init__(self, client: ContaAzulClient):
        self.client = client

    def _due_date_window(self) -> dict[str, str]:
        return {
            "data_vencimento_de": self.client.settings.dynamic_date_from,
            "data_vencimento_ate": self.client.settings.dynamic_date_to,
        }

    @staticmethod
    def _normalize_payload_rows(payload: Any, *candidate_keys: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            for key in candidate_keys:
                items = payload.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
            return [payload] if payload else []

        return []

    def categories(self) -> pd.DataFrame:
        return self.client.get_paginated_items(
            "/v1/categorias",
            params={"permite_apenas_filhos": False},
        )

    def dre_categories(self) -> pd.DataFrame:
        payload = self.client.get("/v1/financeiro/categorias-dre")
        rows = self._normalize_payload_rows(payload, "itens", "categorias")
        return pd.json_normalize(rows, sep=".") if rows else pd.DataFrame()

    def cost_centers(self) -> pd.DataFrame:
        return self.client.get_paginated_items("/v1/centro-de-custo")

    def financial_accounts(self) -> pd.DataFrame:
        return self.client.get_paginated_items(
            "/v1/conta-financeira",
            params={"apenas_ativo": False},
        )

    def financial_account_balances(self, financial_accounts_df: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []

        if financial_accounts_df.empty or "id" not in financial_accounts_df.columns:
            return pd.DataFrame()

        account_ids = financial_accounts_df["id"].dropna().astype(str).unique().tolist()

        for idx, account_id in enumerate(account_ids, start=1):
            try:
                payload = self.client.get(f"/v1/conta-financeira/{account_id}/saldo-atual")
                if isinstance(payload, dict):
                    payload["id_conta_financeira"] = account_id
                    rows.append(payload)
            except Exception as exc:
                logger.warning(
                    "Falha ao extrair saldo da conta financeira %s (%s/%s): %s",
                    account_id,
                    idx,
                    len(account_ids),
                    exc,
                )

        return pd.json_normalize(rows, sep=".") if rows else pd.DataFrame()

    def accounts_receivable(self) -> pd.DataFrame:
        return self.client.get_paginated_items(
            "/v1/financeiro/eventos-financeiros/contas-a-receber/buscar",
            params=self._due_date_window(),
        )

    def accounts_payable(self) -> pd.DataFrame:
        return self.client.get_paginated_items(
            "/v1/financeiro/eventos-financeiros/contas-a-pagar/buscar",
            params=self._due_date_window(),
        )

    def transfers(self) -> pd.DataFrame:
        try:
            start = pd.to_datetime(self.client.settings.dynamic_date_from).date()
            end = pd.to_datetime(self.client.settings.dynamic_date_to).date()

            frames: list[pd.DataFrame] = []
            current_start = start

            while current_start <= end:
                try:
                    current_end = current_start.replace(year=current_start.year + 1)
                except ValueError:
                    current_end = current_start.replace(year=current_start.year + 1, month=2, day=28)

                current_end = min(current_end, end)
                params = {
                    "data_inicio": current_start.isoformat(),
                    "data_fim": current_end.isoformat(),
                }

                try:
                    df = self.client.get_paginated_items("/v1/financeiro/transferencias", params=params)
                    if not df.empty:
                        frames.append(df)
                except Exception as exc:
                    logger.warning(
                        "Falha ao extrair transferencias no intervalo %s ate %s: %s",
                        params["data_inicio"],
                        params["data_fim"],
                        exc,
                    )

                current_start = current_end + timedelta(days=1)

            if not frames:
                return pd.DataFrame()

            return pd.concat(frames, ignore_index=True).drop_duplicates()

        except Exception as exc:
            logger.warning("Nao foi possivel extrair transferencias: %s", exc)
            return pd.DataFrame()

    def acquittances_by_installment_ids(self, installment_ids: list[str]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        failed_ids: list[str] = []
        empty_ids: list[str] = []

        if not installment_ids:
            logger.warning("Nenhum ID de parcela foi informado para buscar baixas.")
            return pd.DataFrame()

        for idx, installment_id in enumerate(installment_ids, start=1):
            if idx % 50 == 0 or idx == 1:
                logger.info("Processando baixas: %s/%s parcelas", idx, len(installment_ids))

            try:
                payload = self.client.get(
                    f"/v1/financeiro/eventos-financeiros/parcelas/{installment_id}/baixa"
                )
                items = self._normalize_payload_rows(payload, "itens", "baixas", "items")

                if not items:
                    empty_ids.append(str(installment_id))

                for item in items:
                    item["id_parcela"] = installment_id
                    rows.append(item)

            except Exception as exc:
                failed_ids.append(str(installment_id))
                logger.warning(
                    "Falha ao extrair baixa da parcela %s (%s/%s): %s",
                    installment_id,
                    idx,
                    len(installment_ids),
                    exc,
                )
                continue

            time.sleep(0.05)

        logger.info(
            "Resumo da extracao de baixas: %s linhas, %s parcelas sem baixa e %s parcelas com falha.",
            len(rows),
            len(empty_ids),
            len(failed_ids),
        )
        if not rows and installment_ids:
            logger.warning(
                "Nenhuma baixa foi retornada para as parcelas informadas. Exemplos de IDs enviados: %s",
                ", ".join(installment_ids[:5]),
            )
        elif empty_ids:
            logger.info(
                "Algumas parcelas nao possuem baixa registrada no periodo consultado. Exemplos: %s",
                ", ".join(empty_ids[:5]),
            )
        if failed_ids:
            logger.warning(
                "Algumas parcelas falharam ao buscar baixas. Exemplos: %s",
                ", ".join(failed_ids[:5]),
            )

        return pd.json_normalize(rows, sep=".") if rows else pd.DataFrame()
