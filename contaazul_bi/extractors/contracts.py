from __future__ import annotations

import pandas as pd

from contaazul_bi.client import ContaAzulClient


class ContractsExtractor:
    def __init__(self, client: ContaAzulClient):
        self.client = client

    def _window(self) -> dict[str, str]:
        settings = self.client.settings
        return {
            "data_inicio": settings.dynamic_date_from,
            "data_fim": settings.dynamic_date_to,
        }

    def contracts(self) -> pd.DataFrame:
        return self.client.get_paginated_items("/v1/contratos", params=self._window())
