from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.watchlist import WatchError, catalog_id_from_url
from app.core.clock import utcnow
from app.core.config import Settings
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import MercadoLivreClient
from app.main import app
from app.models import CatalogProduct, Niche
from app.panel.distribution_routes import get_ml_client
from tests.fake_ml import FakeML
from tests.test_panel import login, seed_admin

PAGE = "https://www.mercadolivre.com.br/cebolinha-50ml/p/MLB23951248?pdp_filters=item_id:MLB1"


def test_only_catalog_product_pages_are_accepted() -> None:
    assert catalog_id_from_url(PAGE) == "MLB23951248"
    assert catalog_id_from_url("https://www.mercadolivre.com.br/p/mlb123456") == "MLB123456"
    with pytest.raises(WatchError, match="/p/MLB"):
        catalog_id_from_url("https://produto.mercadolivre.com.br/MLB-4093716883-gel-_JM")


@pytest.fixture
async def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeML]:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)
    fake = FakeML(
        products={
            "MLB23951248": {
                "id": "MLB23951248",
                "name": "Cebolinha 50 mL",
                "permalink": "https://www.mercadolivre.com.br/p/MLB23951248",
                "attributes": [{"id": "BRAND", "value_name": "Marca"}],
                "pictures": [{"url": "https://http2.mlstatic.com/c.jpg"}],
            }
        }
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    app.dependency_overrides[get_ml_client] = lambda: client
    yield fake
    await client.aclose()


async def test_watch_creates_a_hot_product_and_stop_deactivates_it(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], fake: FakeML
) -> None:
    secret = await seed_admin(sessions)
    async with sessions() as session, session.begin():
        niche = Niche(slug="beleza", name="Beleza", active=True, created_at=utcnow())
        session.add(niche)
        await session.flush()
        niche_id = niche.id
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/vigiar")).text
    csrf = page.split('name="csrf" value="', 1)[1].split('"', 1)[0]

    bad = await client.post(
        "/vigiar", data={"csrf": csrf, "url": "https://meli.la/x", "niche_id": niche_id}
    )
    # Código curto demais: recusado sem ir à rede (o link curto válido está em test_link_curto).
    assert bad.status_code == 422 and "link curto do ML num formato" in bad.text

    r = await client.post("/vigiar", data={"csrf": csrf, "url": PAGE, "niche_id": niche_id})
    assert r.status_code == 303 and r.headers["location"].endswith("aviso=novo")
    async with sessions() as session:
        product = (await session.execute(select(CatalogProduct))).scalar_one()
    assert (product.external_id, product.discovered_via, product.tier) == (
        "MLB23951248",
        "watchlist",
        "hot",
    )
    assert product.name == "Cebolinha 50 mL" and product.niche_id == niche_id
    assert product.hot_since is not None  # as 12 h de quente contam do vigiar
    assert "Cebolinha 50 mL" in (await client.get("/vigiar")).text

    again = await client.post("/vigiar", data={"csrf": csrf, "url": PAGE, "niche_id": niche_id})
    assert again.headers["location"].endswith("aviso=atualizado")

    stop = await client.post(f"/vigiar/{product.id}/parar", data={"csrf": csrf})
    assert stop.status_code == 303
    async with sessions() as session:
        product = (await session.execute(select(CatalogProduct))).scalar_one()
    assert product.active is False
