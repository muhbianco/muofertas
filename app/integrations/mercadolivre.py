"""Cliente da API do Mercado Livre para o coletor (token do app por client_credentials).

Rotas usadas, todas sondadas em 10/10/2026 com as permissões "Publicação e sincronização" e
"Métricas do negócio" em leitura (docs/fase0/README.md, P4b):
  /highlights/MLB/category/{id} · /products/{id} · /products/{id}/items
Fechadas para afiliado (403): /items/{id}, /items/{id}/sale_price, /user-products e a busca
por palavra.

Regras de I/O da casa: timeout em toda chamada; nova tentativa só em GET (idempotente) e no pedido
de token, com espera crescente e aleatória e no máximo 3 tentativas; 429 para o ciclo inteiro.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
TOKEN_SAFETY_SECONDS = 300
PRODUCT_ITEMS_PAGE = 100
PRODUCT_ITEMS_MAX = 300
USERS_BULK_PAGE = 20


class MercadoLivreError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        path: str | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.path = path
        self.code = code


class RateLimitedError(MercadoLivreError):
    """429: o ciclo para e só volta depois de uma espera longa."""


class CallBudgetExceededError(MercadoLivreError):
    """Teto de chamadas por hora atingido (limite nosso, conservador)."""


class ForbiddenError(MercadoLivreError):
    """403 de política: permissão do app ou rota fechada para afiliado."""


class NotFoundError(MercadoLivreError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float


class _Pacer:
    """Ritmo mínimo entre chamadas e teto por hora (janela por hora cheia)."""

    def __init__(self, per_second: float, hourly_cap: int) -> None:
        self._min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._hourly_cap = hourly_cap
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._hour = int(time.time() // 3600)
        self._count = 0

    async def wait(self) -> None:
        async with self._lock:
            hour = int(time.time() // 3600)
            if hour != self._hour:
                self._hour, self._count = hour, 0
            if self._count >= self._hourly_cap:
                raise CallBudgetExceededError(f"teto de {self._hourly_cap} chamadas/hora atingido")
            delay = self._last + self._min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()
            self._count += 1

    @property
    def calls_this_hour(self) -> int:
        return self._count


class MercadoLivreClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client_id = settings.ml_client_id
        self._client_secret = settings.ml_client_secret
        self._http = httpx.AsyncClient(
            base_url=settings.ml_api_base,
            timeout=httpx.Timeout(settings.ml_timeout_seconds, connect=8.0),
            headers={"accept": "application/json"},
            transport=transport,
        )
        self._pacer = _Pacer(settings.ml_requests_per_second, settings.ml_hourly_call_cap)
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()
        self.calls = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ token

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if (
                not force
                and self._token
                and self._token.expires_at - TOKEN_SAFETY_SECONDS > time.time()
            ):
                return self._token.value
            if not self._client_id or not self._client_secret.get_secret_value():
                raise MercadoLivreError("ML_CLIENT_ID/ML_CLIENT_SECRET não configurados")
            form = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret.get_secret_value(),
            }
            last_error = "sem resposta"
            for attempt in range(MAX_ATTEMPTS):
                try:
                    resp = await self._http.post("/oauth/token", data=form)
                except httpx.HTTPError as exc:
                    last_error = type(exc).__name__
                else:
                    body = _json(resp)
                    if (
                        resp.status_code == 200
                        and isinstance(body, dict)
                        and body.get("access_token")
                    ):
                        self._token = _Token(
                            body["access_token"], time.time() + int(body.get("expires_in", 21600))
                        )
                        return self._token.value
                    # Visto em 10/10/2026: 400 error_role_idp transitório, que passa na seguinte.
                    last_error = f"http {resp.status_code} {_error_code(body)}"
                await asyncio.sleep(_backoff(attempt))
            raise MercadoLivreError(f"token do app falhou: {last_error}", path="/oauth/token")

    # ------------------------------------------------------------------ GET

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            await self._pacer.wait()
            token = await self._access_token()
            self.calls += 1
            try:
                resp = await self._http.get(
                    path, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TimeoutException:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise MercadoLivreError("timeout", path=path) from None
                await asyncio.sleep(_backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise MercadoLivreError(type(exc).__name__, path=path) from None
                await asyncio.sleep(_backoff(attempt))
                continue

            body = _json(resp)
            status = resp.status_code
            if status == 200:
                return body
            if status == 401 and not refreshed:
                refreshed = True
                await self._access_token(force=True)
                continue
            if status == 429:
                raise RateLimitedError("429 do ML", status=status, path=path)
            if status == 403:
                raise ForbiddenError(
                    "403 de política", status=status, path=path, code=_error_code(body)
                )
            if status == 404:
                raise NotFoundError("404", status=status, path=path)
            # 206 (resposta parcial) é passageiro: a mesma rota volta 200 completa logo depois.
            if (status >= 500 or status == 206) and attempt + 1 < MAX_ATTEMPTS:
                await asyncio.sleep(_backoff(attempt))
                continue
            raise MercadoLivreError(
                f"http {status}", status=status, path=path, code=_error_code(body)
            )
        raise MercadoLivreError("tentativas esgotadas", path=path)

    # ------------------------------------------------------------------ rotas

    async def highlights(self, category_id: str) -> list[dict[str, Any]]:
        body = await self.get(f"/highlights/MLB/category/{category_id}")
        content = body.get("content") if isinstance(body, dict) else None
        return [c for c in content or [] if isinstance(c, dict)]

    async def product(self, product_id: str) -> dict[str, Any]:
        body = await self.get(f"/products/{product_id}")
        return body if isinstance(body, dict) else {}

    async def product_items(self, product_id: str) -> list[dict[str, Any]]:
        """Todas as ofertas do produto de catálogo, paginadas e com teto (lista sempre limitada)."""
        offers: list[dict[str, Any]] = []
        offset = 0
        while offset < PRODUCT_ITEMS_MAX:
            body = await self.get(
                f"/products/{product_id}/items",
                params={"limit": PRODUCT_ITEMS_PAGE, "offset": offset},
            )
            results = body.get("results") if isinstance(body, dict) else None
            page = [r for r in results or [] if isinstance(r, dict)]
            offers.extend(page)
            total = (
                ((body or {}).get("paging") or {}).get("total", 0) if isinstance(body, dict) else 0
            )
            offset += PRODUCT_ITEMS_PAGE
            if not page or offset >= int(total or 0):
                break
        return offers

    async def users_bulk(self, user_ids: list[int]) -> list[dict[str, Any]]:
        """Perfil público (com `seller_reputation`) de vários vendedores, 20 por chamada.

        `/users/bulk` substitui `/users?ids=` (doc do ML: migrar até 25/10/2026). A resposta pode
        vir como lista de usuários ou de {code, body}; os dois formatos são aceitos.
        """
        users: list[dict[str, Any]] = []
        for i in range(0, len(user_ids), USERS_BULK_PAGE):
            chunk = user_ids[i : i + USERS_BULK_PAGE]
            body = await self.get("/users/bulk", params={"ids": ",".join(str(u) for u in chunk)})
            for entry in body if isinstance(body, list) else []:
                if not isinstance(entry, dict):
                    continue
                user = entry.get("body", entry)
                if isinstance(user, dict) and isinstance(user.get("id"), int):
                    users.append(user)
        return users

    @property
    def calls_this_hour(self) -> int:
        return self._pacer.calls_this_hour


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _error_code(body: Any) -> str | None:
    if isinstance(body, dict):
        code = body.get("error") or body.get("code")
        return str(code)[:80] if code else None
    return None


def _backoff(attempt: int) -> float:
    delay: float = min(30.0, 1.5 * float(2**attempt))
    return delay + random.uniform(0, 1)  # noqa: S311 - jitter, não é segurança
