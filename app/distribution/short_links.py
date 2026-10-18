"""Link colado pelo operador no avulso e no Vigiar: texto do "Compartilhar" do app do ML, link da
página ou link curto (pedido do dono em 18/10/2026: pelo celular o app só compartilha o curto).

- Link da página: segue para o `parse_manual_link`/`catalog_id_from_url`, que já descartam o
  rastreio e a etiqueta de quem copiou (o link de cada grupo o sistema gera com a do grupo).
- Link curto comum (`mercadolivre.com/sec/…`) que aponta para a página do produto: idem.
- Link curto de afiliado (`meli.la/…`): o primeiro salto vai para o perfil social do afiliado, com
  o produto cifrado no `ref`. Dá para saber de quem é (`matt_tool`), não qual é o produto. Do
  próprio afiliado do sistema, o avulso usa o link como está; de outro afiliado, é recusado (a
  comissão iria para ele).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.domain.distribution import (
    LinkError,
    ShortLink,
    affiliate_redirect,
    link_in_text,
    short_link,
)
from app.integrations.ml_shortlink import where_to


@dataclass(frozen=True, slots=True)
class Expanded:
    url: str  # link a interpretar (página do ML), ou o próprio link curto quando é nosso
    own_short: ShortLink | None = None  # link curto do afiliado do sistema: produto desconhecido


async def expand(raw: str, *, client: httpx.AsyncClient, our_tool_id: str) -> Expanded:
    """Levanta `LinkError` (link de outro afiliado, formato estranho) ou `ShortLinkError` (o
    encurtador não respondeu)."""
    url = link_in_text(raw)
    short = short_link(url)
    if short is None:
        return Expanded(url)
    location = await where_to(client, short)
    if short_link(location) is not None:
        raise LinkError("o link curto aponta para outro link curto: cole o link da página")
    redirect = affiliate_redirect(location)
    if redirect is None:
        return Expanded(location)
    if our_tool_id and redirect.tool_id == our_tool_id:
        return Expanded(short.url, own_short=short)
    raise LinkError(
        f"esse link curto é de outro afiliado ({redirect.affiliate}): a comissão iria para ele. "
        "Cole o link da página do produto, e o sistema gera o link de cada grupo"
    )
