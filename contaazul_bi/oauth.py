from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from contaazul_bi.config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OAuthTokenBundle:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    obtained_at: float

    @property
    def expires_at(self) -> float:
        return self.obtained_at + self.expires_in

    def is_expired(self, skew_seconds: int = 120) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)


class TokenStore:
    """Armazena tokens OAuth em arquivo JSON local (desenvolvimento)."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> OAuthTokenBundle | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return OAuthTokenBundle(**payload)

    def save(self, bundle: OAuthTokenBundle) -> None:
        self.path.write_text(json.dumps(asdict(bundle), ensure_ascii=False, indent=2), encoding="utf-8")


class SupabaseTokenStore:
    """
    Armazena tokens OAuth na tabela bi_oauth_tokens do Supabase (produção).

    Requer que a tabela bi_oauth_tokens exista (ver migrations/001_init_supabase.sql).
    A tabela tem sempre exatamente uma linha (id=1) — o bundle é atualizado via UPSERT.
    """

    def __init__(self, database_url: str):
        from sqlalchemy import create_engine as _create_engine
        self._engine = _create_engine(database_url)

    def load(self) -> OAuthTokenBundle | None:
        from sqlalchemy import text
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT access_token, refresh_token, token_type, expires_in, obtained_at "
                        "FROM bi_oauth_tokens WHERE id = 1"
                    )
                ).mappings().fetchone()
            if row and row["obtained_at"] and float(row["obtained_at"]) > 0:
                return OAuthTokenBundle(
                    access_token=row["access_token"],
                    refresh_token=row["refresh_token"],
                    token_type=row["token_type"] or "Bearer",
                    expires_in=int(row["expires_in"] or 3600),
                    obtained_at=float(row["obtained_at"]),
                )
        except Exception as exc:
            logger.warning("Falha ao carregar tokens do Supabase: %s", exc)
        return None

    def save(self, bundle: OAuthTokenBundle) -> None:
        from sqlalchemy import text
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO bi_oauth_tokens
                            (id, access_token, refresh_token, token_type, expires_in, obtained_at, atualizado_em)
                        VALUES (1, :a, :r, :t, :ei, :oa, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            access_token  = EXCLUDED.access_token,
                            refresh_token = EXCLUDED.refresh_token,
                            token_type    = EXCLUDED.token_type,
                            expires_in    = EXCLUDED.expires_in,
                            obtained_at   = EXCLUDED.obtained_at,
                            atualizado_em = NOW()
                    """),
                    {
                        "a":  bundle.access_token,
                        "r":  bundle.refresh_token,
                        "t":  bundle.token_type,
                        "ei": bundle.expires_in,
                        "oa": bundle.obtained_at,
                    },
                )
        except Exception as exc:
            logger.error("Falha ao salvar tokens no Supabase: %s", exc)
            raise


class ContaAzulOAuthManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            self.token_store: TokenStore | SupabaseTokenStore = SupabaseTokenStore(database_url)
            logger.info("OAuth: tokens serão persistidos no Supabase.")
        else:
            self.token_store = TokenStore(settings.token_store_path)
            logger.info("OAuth: tokens serão persistidos em arquivo local (%s).", settings.token_store_path)
        self.token_url = f"{settings.auth_base_url}/oauth2/token"
        self.authorization_url = f"{settings.auth_base_url}/login"
        self.session = requests.Session()
        self._cached_bundle: OAuthTokenBundle | None = None

    def _basic_auth_header(self) -> str:
        raw = f"{self.settings.client_id}:{self.settings.client_secret}".encode("utf-8")
        encoded = base64.b64encode(raw).decode("utf-8")
        return f"Basic {encoded}"

    def build_authorization_url(self, state: str | None = None) -> tuple[str, str]:
        state = state or secrets.token_urlsafe(16)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "state": state,
                "scope": self.settings.scope,
            }
        )
        return f"{self.authorization_url}?{query}", state

    def exchange_code_for_tokens(self, code: str) -> OAuthTokenBundle:
        headers = {
            "Authorization": self._basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
        }
        response = self.session.post(
            self.token_url,
            headers=headers,
            data=data,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        bundle = OAuthTokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            obtained_at=time.time(),
        )
        self.token_store.save(bundle)
        self._cached_bundle = bundle
        logger.info("Tokens iniciais gravados com sucesso.")
        return bundle

    def refresh_tokens(self, refresh_token: str | None = None) -> OAuthTokenBundle:
        stored = self._cached_bundle or self.token_store.load()
        effective_refresh_token = refresh_token or (stored.refresh_token if stored else None)
        if not effective_refresh_token:
            raise RuntimeError(
                "Nenhum refresh token disponível. Execute primeiro o comando de autorização inicial."
            )

        headers = {
            "Authorization": self._basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": effective_refresh_token,
        }
        response = self.session.post(
            self.token_url,
            headers=headers,
            data=data,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        bundle = OAuthTokenBundle(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            obtained_at=time.time(),
        )
        self.token_store.save(bundle)
        self._cached_bundle = bundle
        logger.info("Access token renovado com sucesso.")
        return bundle

    def get_valid_access_token(self) -> str:
        stored = self._cached_bundle or self.token_store.load()
        if stored is None:
            raise RuntimeError(
                "Token store vazio. Execute `python -m contaazul_bi.main authorize` para autorizar a integração."
            )
        if stored.is_expired():
            stored = self.refresh_tokens(stored.refresh_token)
        else:
            self._cached_bundle = stored
        return stored.access_token

    def force_refresh(self) -> str:
        return self.refresh_tokens().access_token
