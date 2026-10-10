"""Comandos de operação.

python -m app.cli seed-niches config/nichos.json   # cria/atualiza nichos e categorias
python -m app.cli run-once                          # uma descoberta + um lote de preços
python -m app.cli stats                             # contagens do coletor
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.collector.service import MARKETPLACE, Collector
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import SessionFactory, dispose_engine
from app.core.logging import configure_logging
from app.integrations.mercadolivre import MercadoLivreClient
from app.models import CatalogProduct, Niche, NicheCategory, PriceObservation


async def seed_niches(data: dict[str, Any]) -> None:
    now = utcnow()
    async with SessionFactory() as session, session.begin():
        for item in data["nichos"]:
            niche = (
                await session.execute(select(Niche).where(Niche.slug == item["slug"]))
            ).scalar_one_or_none()
            if niche is None:
                niche = Niche(
                    slug=item["slug"],
                    name=item["nome"],
                    active=item.get("ativo", True),
                    created_at=now,
                )
                session.add(niche)
                await session.flush()
            else:
                niche.name = item["nome"]
                niche.active = item.get("ativo", True)
            wanted = {c["id"]: c["nome"] for c in item["categorias"]}
            current = {
                c.external_category_id: c
                for c in (
                    await session.execute(
                        select(NicheCategory).where(
                            NicheCategory.niche_id == niche.id,
                            NicheCategory.marketplace == MARKETPLACE,
                        )
                    )
                ).scalars()
            }
            for ext_id, name in wanted.items():
                if ext_id in current:
                    current[ext_id].name = name
                    current[ext_id].active = True
                else:
                    session.add(
                        NicheCategory(
                            niche_id=niche.id,
                            marketplace=MARKETPLACE,
                            external_category_id=ext_id,
                            name=name,
                            active=True,
                        )
                    )
            for ext_id, category in current.items():
                if ext_id not in wanted:
                    category.active = False
            print(f"{item['slug']}: {len(wanted)} categorias")


async def run_once() -> None:
    client = MercadoLivreClient(settings)
    try:
        collector = Collector(SessionFactory, client, settings)
        new = await collector.discover_due()
        stats = await collector.refresh_due()
        print(
            f"descoberta: {new} produtos novos · lote: {stats.seen} vistos, "
            f"{stats.changed} mudaram, "
            f"{stats.errors} erros · {client.calls} chamadas"
        )
    finally:
        await client.aclose()


async def stats() -> None:
    async with SessionFactory() as session:
        products = (
            (
                await session.execute(
                    select(CatalogProduct.tier, func.count())
                    .where(CatalogProduct.active.is_(True))
                    .group_by(CatalogProduct.tier)
                )
            )
            .tuples()
            .all()
        )
        observations = (
            await session.execute(select(func.count()).select_from(PriceObservation))
        ).scalar_one()
    print("produtos ativos por camada:", dict(products))
    print("observações de preço:", observations)


def main() -> None:
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-niches")
    seed.add_argument("path", type=Path)
    sub.add_parser("run-once")
    sub.add_parser("stats")
    args = parser.parse_args()
    # Arquivo lido antes do loop assíncrono (sem I/O bloqueante dentro dele).
    seed_data = (
        json.loads(args.path.read_text(encoding="utf-8")) if args.command == "seed-niches" else {}
    )

    async def runner() -> None:
        try:
            if args.command == "seed-niches":
                await seed_niches(seed_data)
            elif args.command == "run-once":
                await run_once()
            else:
                await stats()
        finally:
            await dispose_engine()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
