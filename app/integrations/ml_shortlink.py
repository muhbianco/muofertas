"""Para onde aponta um link curto do ML (`meli.la/…`, `mercadolivre.com/sec/…`).

Uma requisição ao encurtador, sem seguir o redirecionamento e sem ler o corpo: a página do ML não
é aberta pelo servidor (termos do afiliado; e o ML manda servidor para a verificação de conta). O
endereço é montado a partir do código do link (`short_link`), nada do texto do operador vai para a
rede. O cliente se identifica pelo nome do sistema: com o agente padrão do httpx o encurtador
responde 403, com o nosso responde o 301 (medido em 18/10/2026). Nada de se passar por navegador.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.domain.distribution import ShortLink

USER_AGENT = "MegaPromoOz/1.0 (+https://ofertas.muhbianco.com.br)"
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
REDIRECTS = frozenset({301, 302, 303, 307, 308})


class ShortLinkError(Exception):
    """Mensagem pronta para o operador."""


def new_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, transport=transport
    )


async def where_to(client: httpx.AsyncClient, link: ShortLink) -> str:
    """O destino do primeiro salto (o `Location`), absoluto."""
    try:
        async with client.stream("GET", link.url, follow_redirects=False) as resp:
            status, location = resp.status_code, resp.headers.get("location")
    except httpx.HTTPError as exc:
        raise ShortLinkError("o encurtador do ML não respondeu: tente de novo") from exc
    if status not in REDIRECTS or not location:
        raise ShortLinkError(
            f"o link curto não abriu (o ML respondeu {status}): confira o link ou cole o da página"
        )
    return str(urljoin(link.url, location))
