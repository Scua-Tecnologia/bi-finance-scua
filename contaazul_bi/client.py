from __future__ import annotations

import logging
import math
import time
from typing import Any

import pandas as pd
import requests
from requests import Response, Session

from contaazul_bi.config import Settings
from contaazul_bi.oauth import ContaAzulOAuthManager


logger = logging.getLogger(__name__)


class ContaAzulAPIError(RuntimeError):
    pass


class ContaAzulClient:
    def __init__(self, settings: Settings, oauth_manager: ContaAzulOAuthManager):
        self.settings = settings
        self.oauth_manager = oauth_manager
        self.base_url = settings.api_base_url.rstrip("/")
        self.session = Session()
        self.max_retries = 5

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.oauth_manager.get_valid_access_token()}",
            "Accept": "application/json",
        }

    def _handle_response(self, response: Response, endpoint: str) -> Any:
        if response.status_code == 204:
            return None
        if not response.ok:
            raise ContaAzulAPIError(
                f"Erro na API Conta Azul [{response.status_code}] endpoint={endpoint} body={response.text[:1000]}"
            )
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def request(self, method: str, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(
                    method=method.upper(),
                    url=url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.settings.timeout_seconds,
                )

                if response.status_code == 401 and attempt < self.max_retries:
                    logger.warning("401 no endpoint %s. Renovando token e tentando novamente.", endpoint)
                    self.oauth_manager.force_refresh()
                    continue

                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = response.headers.get("Retry-After")
                    wait_seconds = float(retry_after) if retry_after else min(2 ** (attempt - 1), 30)
                    logger.warning(
                        "Resposta %s no endpoint %s. Nova tentativa em %.1f segundos.",
                        response.status_code,
                        endpoint,
                        wait_seconds,
                    )
                    time.sleep(wait_seconds)
                    continue

                return self._handle_response(response, endpoint)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                wait_seconds = min(2 ** (attempt - 1), 30)
                logger.warning("Falha na chamada %s %s. Tentativa %s/%s em %.1fs. Erro: %s", method, endpoint, attempt, self.max_retries, wait_seconds, exc)
                time.sleep(wait_seconds)

        raise ContaAzulAPIError(f"Falha definitiva ao chamar endpoint {endpoint}: {last_error}")

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", endpoint, params=params)

    @staticmethod
    def _extract_total_items(payload: dict[str, Any], item_key: str) -> int | None:
        total_candidates = [
            payload.get("itens_totais"),
            payload.get("total_itens"),
            (payload.get("paginacao") or {}).get("total_itens") if isinstance(payload.get("paginacao"), dict) else None,
        ]
        for candidate in total_candidates:
            if candidate is None:
                continue
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue

        items = payload.get(item_key, [])
        if isinstance(items, list):
            logger.warning(
                "Nenhuma chave de paginação encontrada no payload (itens_totais/total_itens). "
                "Assumindo página única com %s itens. Dados podem estar truncados.",
                len(items),
            )
            return len(items)
        return None

    def get_paginated_items(self, endpoint: str, *, params: dict[str, Any] | None = None, item_key: str = "itens") -> pd.DataFrame:
        params = dict(params or {})
        params.setdefault("pagina", 1)
        params.setdefault("tamanho_pagina", self.settings.page_size)
        page = int(params["pagina"])
        page_size = int(params["tamanho_pagina"])
        rows: list[dict[str, Any]] = []
        total_pages = 1

        while page <= total_pages:
            params["pagina"] = page
            payload = self.get(endpoint, params=params)
            total_items = self._extract_total_items(payload, item_key) or 0
            total_pages = max(1, math.ceil(total_items / page_size))
            items = payload.get(item_key, [])
            rows.extend(items)
            logger.info("Endpoint %s página %s/%s: %s registros.", endpoint, page, total_pages, len(items))
            page += 1
            time.sleep(0.12)

        return pd.json_normalize(rows, sep=".") if rows else pd.DataFrame()
