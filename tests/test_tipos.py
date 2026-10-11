from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_niches
from app.collector.domains import DomainRuleError
from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import Settings
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import MercadoLivreClient
from app.models import CatalogProduct, MarketplaceDomain, Niche
from tests.fake_ml import FakeML
from tests.test_cli_seed import _niche, _niche_of
from tests.test_panel import login, seed_admin

PUMP = "MLB-AIR_COMPRESSORS"
SHAMPOO = "MLB-HAIR_SHAMPOOS_AND_CONDITIONERS"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)


def _config(tools_domains: list[str], **extra: list[str]) -> dict[str, Any]:
    tools = _niche("ferramentas", ["MLB5"])
    tools["dominios"] = tools_domains
    sport = _niche("tenis-esporte", ["MLB1292"])
    family = _niche("pais-maes-filhos", ["MLB9"])
    beauty = _niche("beleza", ["MLB7"])
    for slug, domains in extra.items():
        {"beleza": beauty, "tenis-esporte": sport}[slug]["dominios"] = domains
    return {"nichos": [tools, sport, family, beauty]}


async def _product(
    sessions: async_sessionmaker[AsyncSession],
    external_id: str,
    slug: str,
    category: str,
    domain: str,
    *,
    locked: bool = False,
) -> int:
    now = utcnow()
    async with sessions() as s, s.begin():
        niche = (await s.execute(select(Niche).where(Niche.slug == slug))).scalar_one()
        p = CatalogProduct(
            marketplace="mercadolivre",
            external_id=external_id,
            niche_id=niche.id,
            category_id=category,
            domain_id=domain,
            name=external_id,
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now,
            needs_evaluation=False,
            niche_locked=locked,
            last_tier="boa",
            last_score=61.0,
        )
        s.add(p)
        await s.flush()
        return p.id


async def test_seed_moves_ruled_types_and_leaves_the_rest_with_the_category(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_niches(_config([]), sessions=sessions)
    await _product(sessions, "MLBCOMP", "tenis-esporte", "MLB1292", PUMP)
    await _product(sessions, "MLBFIXO", "tenis-esporte", "MLB1292", PUMP, locked=True)
    await _product(sessions, "MLBBEBE", "pais-maes-filhos", "MLB9", SHAMPOO)

    await seed_niches(_config([PUMP]), sessions=sessions)

    # O compressor que veio por Ciclismo vai para Ferramentas e volta para a avaliação; o que o
    # operador pôs à mão fica; o shampoo de bebê (tipo sem regra) segue a categoria.
    assert await _niche_of(sessions, "MLBCOMP") == ("ferramentas", True)
    assert (await _niche_of(sessions, "MLBFIXO"))[0] == "tenis-esporte"
    assert await _niche_of(sessions, "MLBBEBE") == ("pais-maes-filhos", False)

    # A categoria mudar de nicho não leva de volta o produto de tipo com regra.
    moved = _config([PUMP])
    moved["nichos"][1]["categorias"] = [{"id": "MLB6", "nome": "MLB6"}]
    moved["nichos"][0]["categorias"].append({"id": "MLB1292", "nome": "Ciclismo"})
    await seed_niches(moved, sessions=sessions)
    assert (await _niche_of(sessions, "MLBCOMP"))[0] == "ferramentas"


async def test_panel_rule_survives_the_seed_and_config_rule_leaving_is_cleared(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_niches(_config([PUMP, "MLB-THERMOSES"]), sessions=sessions)
    async with sessions() as s, s.begin():
        beauty = (await s.execute(select(Niche).where(Niche.slug == "beleza"))).scalar_one()
        row = (
            await s.execute(select(MarketplaceDomain).where(MarketplaceDomain.domain_id == PUMP))
        ).scalar_one()
        row.niche_id, row.rule_source, row.rule_actor = beauty.id, "painel", "dono"

    await seed_niches(_config([PUMP]), sessions=sessions)  # garrafa térmica saiu do JSON

    async with sessions() as s:
        rows = {
            r.domain_id: (r.niche_id, r.rule_source)
            for r in (await s.execute(select(MarketplaceDomain))).scalars()
        }
    assert rows[PUMP] == (beauty.id, "painel")
    assert rows["MLB-THERMOSES"] == (None, None)


async def test_seed_refuses_a_type_in_two_niches(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DomainRuleError):
        await seed_niches(_config([PUMP], beleza=[PUMP]), sessions=sessions)
    with pytest.raises(DomainRuleError):
        await seed_niches(_config(["../users/me"]), sessions=sessions)
    async with sessions() as s:
        assert (await s.execute(select(Niche))).first() is None  # nada gravado


async def test_discovery_puts_a_new_product_in_the_niche_of_its_type(
    sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    await seed_niches(_config([PUMP]), sessions=sessions)
    fake = FakeML(
        highlights={
            "MLB1292": [
                {"id": "MLBP1", "type": "PRODUCT", "position": 1},
                {"id": "MLBP2", "type": "PRODUCT", "position": 2},
            ]
        },
        products={
            "MLBP1": {"id": "MLBP1", "name": "Compressor de pneu", "domain_id": PUMP},
            "MLBP2": {"id": "MLBP2", "name": "Bicicleta aro 29", "domain_id": "MLB-BICYCLES"},
        },
        domains={PUMP: "Compressores de ar"},
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    collector = Collector(sessions, client, settings)
    try:
        await collector.discover_due()
        # Nome dos tipos para o painel: o que o ML conhece e o que não conhece (não volta).
        assert await collector.name_domains() == 2
        assert await collector.name_domains() == 0
    finally:
        await client.aclose()
    assert (await _niche_of(sessions, "MLBP1"))[0] == "ferramentas"
    assert (await _niche_of(sessions, "MLBP2"))[0] == "tenis-esporte"
    async with sessions() as s:
        names = {
            r.domain_id: r.name
            for r in (await s.execute(select(MarketplaceDomain))).scalars()
            if r.name_checked_at is not None
        }
    assert names == {PUMP: "Compressores de ar", "MLB-BICYCLES": None}
    assert fake.calls.count(f"/catalog_domains/{PUMP}") == 1


async def test_panel_moves_all_products_of_the_type(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    await seed_niches(_config([]), sessions=sessions)
    first = await _product(sessions, "MLBCOMP1", "tenis-esporte", "MLB1292", PUMP)
    await _product(sessions, "MLBCOMP2", "tenis-esporte", "MLB1292", PUMP)
    await _product(sessions, "MLBBIKE", "tenis-esporte", "MLB1292", "MLB-BICYCLES")
    async with sessions() as s, s.begin():
        s.add(
            MarketplaceDomain(
                marketplace="mercadolivre",
                domain_id=PUMP,
                name="Compressores de ar",
                name_checked_at=utcnow(),
            )
        )
        tools = (await s.execute(select(Niche).where(Niche.slug == "ferramentas"))).scalar_one()
    assert (await login(client, secret)).status_code == 303

    page = (await client.get("/")).text
    assert "todos os “Compressores de ar”" in page
    assert "todos os “bicycles”" in page  # sem nome ainda: o id legível
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None

    r = await client.post(
        f"/produto/{first}/nicho",
        data={"csrf": csrf.group(1), "niche_id": tools.id, "todos": "true"},
    )
    assert r.headers["location"] == "/?aviso=nicho-tipo"
    assert await _niche_of(sessions, "MLBCOMP1") == ("ferramentas", True)
    assert await _niche_of(sessions, "MLBCOMP2") == ("ferramentas", True)
    assert (await _niche_of(sessions, "MLBBIKE"))[0] == "tenis-esporte"
    async with sessions() as s:
        rule = (
            await s.execute(select(MarketplaceDomain).where(MarketplaceDomain.domain_id == PUMP))
        ).scalar_one()
    assert (rule.niche_id, rule.rule_source, rule.rule_actor) == (tools.id, "painel", "dono")

    nichos = (await client.get("/nichos")).text
    assert "Tipos de produto com nicho fixo" in nichos and "Compressores de ar" in nichos
