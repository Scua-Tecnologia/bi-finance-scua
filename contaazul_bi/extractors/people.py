from __future__ import annotations

import pandas as pd

from contaazul_bi.client import ContaAzulClient


class PeopleExtractor:
    def __init__(self, client: ContaAzulClient):
        self.client = client

    def people(self) -> pd.DataFrame:
        return self.client.get_paginated_items(
            "/v1/pessoas",
            params={
                "tipo_ordenacao": "NOME",
                "ordem_ordenacao": "ASC",
                "com_endereco": True,
            },
        )
