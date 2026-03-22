from __future__ import annotations

import pandas as pd

from contaazul_bi.client import ContaAzulClient


class InvoiceExtractor:
    def __init__(self, client: ContaAzulClient):
        self.client = client

    def _window(self) -> dict[str, str]:
        settings = self.client.settings
        return {
            "data_inicial": settings.dynamic_date_from,
            "data_final": settings.dynamic_date_to,
        }

    def invoices(self) -> pd.DataFrame:
        return self.client.get_paginated_items("/v1/notas-fiscais", params=self._window())
