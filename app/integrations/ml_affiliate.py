"""Gerador de links do portal de afiliados do ML (o mesmo endpoint da página `linkbuilder`).

Decisão do dono (10/10/2026), ciente da cláusula 3.1.2: o link curto oficial (`meli.la`) sai pelo
endpoint interno do gerador, com a sessão logada dele. Não existe API pública de afiliado.

- A sessão (cookie + `X-Csrf-Token`) mora num arquivo do host montado só leitura no worker; é
  relida a cada uso (renovar não pede deploy) e nunca vai para log, banco ou Env do Portainer.
- Uma URL por chamada, com pausa entre chamadas.
- 401/403, ou resposta que não é JSON (captcha, verificação): para e avisa. Nada de contornar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)
CREATE_LINK_URL = "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
LINKBUILDER = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.0.0 Safari/537.36"
)
SHORT_PREFIXES = ("https://meli.la/", "https://mercadolivre.com/sec/")


@dataclass(frozen=True, slots=True)
class PortalSession:
    cookie: str
    csrf: str


def read_session(path: str) -> PortalSession | None:
    """Arquivo `chave=valor` (cookie=..., csrf=...). Ausente ou incompleto: None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    cookie, csrf = values.get("cookie", ""), values.get("csrf", "")
    if not cookie or not csrf:
        return None
    return PortalSession(cookie=cookie, csrf=csrf)


class PortalError(Exception):
    """`kind`: session (expirou), blocked (captcha/verificação), transient, rejected (URL)."""

    def __init__(self, kind: str, *, status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.status = status


class AffiliatePortal:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0), transport=transport, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create_link(self, session: PortalSession, url: str, tag: str) -> str:
        headers = {
            "Cookie": session.cookie,
            "X-Csrf-Token": session.csrf,
            "Origin": "https://www.mercadolivre.com.br",
            "Referer": LINKBUILDER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            response = await self._http.post(
                CREATE_LINK_URL, json={"urls": [url], "tag": tag}, headers=headers
            )
        except httpx.HTTPError as exc:
            raise PortalError("transient") from exc
        is_json = "application/json" in response.headers.get("content-type", "")
        if response.status_code in (401, 403):
            raise PortalError("session" if is_json else "blocked", status=response.status_code)
        if 300 <= response.status_code < 400 or not is_json:
            # redirecionou para login/verificação ou devolveu página: sessão inútil agora
            raise PortalError("blocked", status=response.status_code)
        if response.status_code == 429 or response.status_code >= 500:
            raise PortalError("transient", status=response.status_code)
        if response.status_code >= 400:
            raise PortalError("rejected", status=response.status_code)
        return _short_url(response.json())


def _short_url(body: Any) -> str:
    urls = body.get("urls") if isinstance(body, dict) else None
    first = urls[0] if isinstance(urls, list) and urls and isinstance(urls[0], dict) else {}
    short = str(first.get("short_url") or "")
    if not short.startswith(SHORT_PREFIXES) or len(short) > 100:
        raise PortalError("rejected")
    return short
