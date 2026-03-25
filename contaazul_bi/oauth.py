from __future__ import annotations

import base64
import hashlib
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
        self._engine = _create_engine(database_url, pool_pre_ping=True)

    def load(self) -> OAuthTokenBundle | None:
        from sqlalchemy import text
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT access_token, refresh_token, token_type, expires_in, obtained_at, atualizado_em "
                        "FROM bi_oauth_tokens WHERE id = 1"
                    )
                ).mappings().fetchone()
            if row and row["obtained_at"] and float(row["obtained_at"]) > 0:
                obtained_at = float(row["obtained_at"])
                atualizado_em = row["atualizado_em"]
                expires_in = int(row["expires_in"] or 3600)
                access_expires_at = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC", time.gmtime(obtained_at + expires_in)
                )
                logger.info(
                    "Tokens carregados do Supabase | "
                    "access_token expira em: %s | "
                    "refresh_token salvo em: %s | "
                    "refresh_token (últimos 8 chars): ...%s",
                    access_expires_at,
                    atualizado_em,
                    row["refresh_token"][-8:] if row["refresh_token"] else "N/A",
                )
                return OAuthTokenBundle(
                    access_token=row["access_token"],
                    refresh_token=row["refresh_token"],
                    token_type=row["token_type"] or "Bearer",
                    expires_in=expires_in,
                    obtained_at=obtained_at,
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
            logger.info(
                "Tokens salvos no Supabase | "
                "access_token obtido em: %s | "
                "refresh_token (últimos 8 chars): ...%s",
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(bundle.obtained_at)),
                bundle.refresh_token[-8:] if bundle.refresh_token else "N/A",
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
        logger.info(
            "OAuth config carregada | fingerprint=%s | redirect_uri=%s | token_store=%s",
            self.configuration_fingerprint(),
            self.settings.redirect_uri,
            self.token_store_label(),
        )

    @staticmethod
    def _short_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _oauth_error_details(response: requests.Response) -> tuple[str | None, str]:
        try:
            payload = response.json()
        except ValueError:
            return None, response.text

        if not isinstance(payload, dict):
            return None, response.text

        error = payload.get("error")
        description = (
            payload.get("error_description")
            or payload.get("message")
            or response.text
        )
        return str(error) if error else None, str(description)

    def configuration_fingerprint(self) -> str:
        raw = "|".join(
            [
                self.settings.client_id,
                self.settings.client_secret,
                self.settings.redirect_uri,
                self.settings.auth_base_url,
            ]
        )
        return self._short_hash(raw)

    def token_store_label(self) -> str:
        if isinstance(self.token_store, SupabaseTokenStore):
            return "supabase"
        return str(self.settings.token_store_path)

    def token_status(self) -> dict[str, str | bool | None]:
        stored = self._cached_bundle or self.token_store.load()
        status: dict[str, str | bool | None] = {
            "token_store": self.token_store_label(),
            "oauth_config_fingerprint": self.configuration_fingerprint(),
            "redirect_uri": self.settings.redirect_uri,
            "auth_base_url": self.settings.auth_base_url,
            "token_present": stored is not None,
            "refresh_token_present": bool(stored and stored.refresh_token),
            "access_token_expires_at_utc": None,
            "access_token_expired": None,
        }
        if stored is None:
            return status

        status["access_token_expires_at_utc"] = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(stored.expires_at),
        )
        status["access_token_expired"] = stored.is_expired(skew_seconds=0)
        return status

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
        if not response.ok:
            oauth_error, oauth_description = self._oauth_error_details(response)
            logger.error(
                "Falha ao renovar tokens (HTTP %s, oauth_error=%s, fingerprint=%s, redirect_uri=%s): %s",
                response.status_code,
                oauth_error or "N/A",
                self.configuration_fingerprint(),
                self.settings.redirect_uri,
                response.text,
            )
            if response.status_code == 400:
                if oauth_error == "invalid_client":
                    raise RuntimeError(
                        "OAuth `invalid_client`: o `client_id`/`client_secret` deste ambiente "
                        "nao foram aceitos pelo Conta Azul, ou o refresh token salvo foi emitido "
                        "para outro aplicativo OAuth. Confira `CONTA_AZUL_CLIENT_ID`, "
                        "`CONTA_AZUL_CLIENT_SECRET` e `CONTA_AZUL_REDIRECT_URI` no ambiente "
                        "atual. Se houve troca de aplicativo ou rotacao de secret, reautorize "
                        "com as credenciais vigentes usando `python -m contaazul_bi.main authorize`."
                    )
                if oauth_error == "invalid_grant":
                    raise RuntimeError(
                        "OAuth `invalid_grant`: o refresh token salvo no token store ficou "
                        "invalido, expirou ou foi revogado. Reexecute "
                        "`python -m contaazul_bi.main authorize` para reautorizar a integracao."
                    )
                raise RuntimeError(
                    f"Falha ao renovar tokens (HTTP 400, oauth_error={oauth_error or 'desconhecido'}). "
                    f"Resposta do servidor: {oauth_description}"
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
