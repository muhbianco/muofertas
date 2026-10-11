from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_niches
from app.core.clock import utcnow
from app.models import CatalogProduct, Niche


def _niche(slug: str, categories: list[str]) -> dict[str, Any]:
    return {
        "slug": slug,
        "nome": slug.title(),
        "categorias": [{"id": c, "nome": c} for c in categories],
    }


async def _product(
    sessions: async_sessionmaker[AsyncSession], external_id: str, slug: str, category: str
) -> None:
    now = utcnow()
    async with sessions() as session, session.begin():
        niche = (await session.execute(select(Niche).where(Niche.slug == slug))).scalar_one()
        session.add(
            CatalogProduct(
                marketplace="mercadolivre",
                external_id=external_id,
                niche_id=niche.id,
                category_id=category,
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now,
                consecutive_failures=0,
                first_seen_at=now,
                needs_evaluation=False,
            )
        )


async def _niche_of(
    sessions: async_sessionmaker[AsyncSession], external_id: str
) -> tuple[str, bool]:
    async with sessions() as session:
        row = (
            await session.execute(
                select(Niche.slug, CatalogProduct.needs_evaluation)
                .join(Niche, Niche.id == CatalogProduct.niche_id)
                .where(CatalogProduct.external_id == external_id)
            )
        ).one()
    return row[0], row[1]


async def test_category_moving_to_a_new_niche_takes_its_products(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_niches(
        {"nichos": [_niche("observacao", ["MLB1051", "MLB455425"])]}, sessions=sessions
    )
    await _product(sessions, "MLBCEL", "observacao", "MLB1051")
    await _product(sessions, "MLBLEGO", "observacao", "MLB455425")

    await seed_niches(
        {
            "nichos": [
                _niche("tecnologia", ["MLB1051"]),
                _niche("observacao", ["MLB455425"]),
            ]
        },
        sessions=sessions,
    )

    # O celular vai junto com a categoria e volta para a avaliação (regras do nicho novo).
    assert await _niche_of(sessions, "MLBCEL") == ("tecnologia", True)
    # Produto de categoria que ficou onde estava não muda.
    assert await _niche_of(sessions, "MLBLEGO") == ("observacao", False)


async def test_category_active_in_two_niches_moves_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_niches({"nichos": [_niche("observacao", ["MLB1144"])]}, sessions=sessions)
    await _product(sessions, "MLBGAME", "observacao", "MLB1144")

    await seed_niches(
        {"nichos": [_niche("tecnologia", ["MLB1144"]), _niche("observacao", ["MLB1144"])]},
        sessions=sessions,
    )

    assert await _niche_of(sessions, "MLBGAME") == ("observacao", False)
