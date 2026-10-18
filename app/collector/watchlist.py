"""Lista vigiada: o operador cola o link da página de um produto e o coletor passa a acompanhá-lo.

Serve para o que não está entre os 20 mais vendidos da categoria (o caso do cebolinha de 50 mL).
Só página de produto de catálogo (`/p/MLB…`): é o único tipo com preço pela API do app. O link
curto chega já expandido pelo painel (`app.distribution.short_links`); o de afiliado (`meli.la`)
não diz o produto e é recusado lá. Nenhuma transação fica aberta durante a chamada ao ML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.marketplace import MARKETPLACE
from app.collector.service import _brand, _picture
from app.domain.catalog_offers import Tier
from app.integrations.mercadolivre import MercadoLivreClient, MercadoLivreError, NotFoundError
from app.models import CatalogProduct, Niche

CATALOG_URL = re.compile(r"/p/(MLB\d{5,12})(?:[/?#]|$)", re.IGNORECASE)
WATCHLIST = "watchlist"


class WatchError(ValueError):
    """Mensagem pronta para o operador."""


@dataclass(frozen=True, slots=True)
class WatchResult:
    product_id: int
    name: str
    created: bool


def catalog_id_from_url(url: str) -> str:
    text = url.strip()
    match = CATALOG_URL.search(text)
    if match is None:
        raise WatchError(
            "Cole o link da página do produto (com /p/MLB…). Anúncio sem página de produto não "
            "tem preço pela API, e o robô não consegue acompanhar."
        )
    return match.group(1).upper()


async def watch(
    sessions: async_sessionmaker[AsyncSession],
    client: MercadoLivreClient,
    *,
    url: str,
    niche_id: int,
    now: datetime,
) -> WatchResult:
    external_id = catalog_id_from_url(url)
    async with sessions() as db:
        niche = await db.get(Niche, niche_id)
        if niche is None or not niche.active:
            raise WatchError("Escolha um nicho ativo.")
    try:
        info = await client.product(external_id)  # fora de transação
    except NotFoundError:
        raise WatchError("O Mercado Livre não encontrou esse produto.") from None
    except MercadoLivreError:
        raise WatchError(
            "O Mercado Livre não respondeu agora; tente de novo em instantes."
        ) from None
    async with sessions() as db, db.begin():
        product = (
            await db.execute(
                select(CatalogProduct).where(
                    CatalogProduct.marketplace == MARKETPLACE,
                    CatalogProduct.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        created = product is None
        if product is None:
            product = CatalogProduct(
                marketplace=MARKETPLACE,
                external_id=external_id,
                first_seen_at=now,
                consecutive_failures=0,
            )
            db.add(product)
        product.niche_id = niche_id
        product.niche_locked = True  # o operador escolheu
        product.discovered_via = WATCHLIST
        product.name = str(info.get("name") or "")[:255] or product.name
        product.brand = _brand(info) or product.brand
        product.domain_id = str(info.get("domain_id") or "")[:80] or product.domain_id
        product.permalink = str(info.get("permalink") or "")[:512] or product.permalink
        product.image_url = _picture(info) or product.image_url
        product.active = True
        product.operator_blocked = False
        # Vigiado pelo operador: camada quente e leitura já no próximo ciclo.
        product.tier = Tier.HOT.value
        product.hot_since = now  # as 12 h de quente contam daqui, não de uma queda antiga
        product.next_check_at = now
        product.needs_evaluation = True
        await db.flush()
        return WatchResult(product.id, product.name or external_id, created)


async def unwatch(db: AsyncSession, product_id: int) -> bool:
    product = await db.get(CatalogProduct, product_id)
    if product is None or product.discovered_via != WATCHLIST:
        return False
    product.active = False
    return True


async def watched(db: AsyncSession) -> list[tuple[CatalogProduct, str]]:
    rows = await db.execute(
        select(CatalogProduct, Niche.name)
        .join(Niche, Niche.id == CatalogProduct.niche_id)
        .where(CatalogProduct.discovered_via == WATCHLIST, CatalogProduct.active.is_(True))
        .order_by(CatalogProduct.first_seen_at.desc())
        .limit(100)
    )
    return [(p, name) for p, name in rows.tuples()]
