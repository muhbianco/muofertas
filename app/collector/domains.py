"""Tipo de produto (domínio do ML) com nicho fixo.

A categoria pela qual o robô descobre um produto nem sempre diz o que ele é: Ciclismo traz
compressor de pneu de carro. Para os tipos com regra, o tipo decide o nicho. A regra vem do
`nichos.json` (`dominios` de cada nicho) ou do painel ("todos deste tipo" no Trocar nicho). Tipo
sem regra continua com a categoria, e é de propósito: shampoo de bebê fica em Pais, mães e filhos
e o de adulto em Beleza, os dois do mesmo tipo.

Precedência: nicho escolhido para o produto (`niche_locked`) > regra do tipo > categoria.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.marketplace import DOMAIN_ID, MARKETPLACE
from app.collector.recategorize import discard_pending
from app.models import CatalogProduct, MarketplaceDomain, Niche

SOURCE_CONFIG = "config"
SOURCE_PANEL = "painel"
CONFIG_ACTOR = "nichos.json"
RULE_LIMIT = 5000
MOVE_LIMIT = 1000


class DomainRuleError(ValueError):
    """Tipo de produto inválido ou em dois nichos."""


def label(domain_id: str, name: str | None) -> str:
    """Nome para o painel: o do ML ou, enquanto o worker não buscou, o id legível."""
    return name or domain_id.split("-", 1)[-1].replace("_", " ").lower()


async def domain_rules(
    session: AsyncSession, domain_ids: Collection[str] | None = None
) -> dict[str, int]:
    """Tipo → nicho, só das regras cujo nicho está ativo (nicho desligado devolve à categoria)."""
    q = (
        select(MarketplaceDomain.domain_id, Niche.id)
        .join(Niche, Niche.id == MarketplaceDomain.niche_id)
        .where(MarketplaceDomain.marketplace == MARKETPLACE, Niche.active.is_(True))
    )
    if domain_ids is not None:
        if not domain_ids:
            return {}
        q = q.where(MarketplaceDomain.domain_id.in_(list(domain_ids)))
    return dict((await session.execute(q.limit(RULE_LIMIT))).tuples().all())


async def follow_domain_rules(
    session: AsyncSession, *, actor: str, domain_ids: Collection[str] | None = None
) -> int:
    """Leva para o nicho da regra os produtos do tipo que estão em outro (menos os travados).

    Voltam para a avaliação (regras do nicho novo) e saem da fila de envio do grupo antigo.
    """
    rules = await domain_rules(session, domain_ids)
    if not rules:
        return 0
    niches = {
        n.id: n
        for n in (await session.execute(select(Niche).where(Niche.id.in_(set(rules.values())))))
        .scalars()
        .all()
    }
    moved = 0
    for domain_id, niche_id in rules.items():
        ids = list(
            (
                await session.execute(
                    select(CatalogProduct.id)
                    .where(
                        CatalogProduct.marketplace == MARKETPLACE,
                        CatalogProduct.domain_id == domain_id,
                        CatalogProduct.niche_locked.is_(False),
                        or_(CatalogProduct.niche_id.is_(None), CatalogProduct.niche_id != niche_id),
                    )
                    .limit(MOVE_LIMIT)
                )
            ).scalars()
        )
        if not ids:
            continue
        await session.execute(
            update(CatalogProduct)
            .where(CatalogProduct.id.in_(ids))
            .values(niche_id=niche_id, needs_evaluation=True)
        )
        await discard_pending(session, ids, niches[niche_id], actor=actor)
        moved += len(ids)
    return moved


def _set_rule(
    row: MarketplaceDomain, niche_id: int, source: str, actor: str, now: datetime
) -> None:
    row.niche_id = niche_id
    row.rule_source = source
    row.rule_actor = actor[:40]
    row.rule_at = now


async def apply_panel_rule(
    session: AsyncSession, *, domain_id: str, niche_id: int, actor: str, now: datetime
) -> int:
    """ "Todos deste tipo": grava a regra do painel e leva junto os produtos do tipo.

    Devolve quantos produtos mudaram de nicho. O nicho já foi validado (ativo) por quem chama.
    """
    if not DOMAIN_ID.match(domain_id):
        raise DomainRuleError(domain_id)
    row = (
        await session.execute(
            select(MarketplaceDomain)
            .where(
                MarketplaceDomain.marketplace == MARKETPLACE,
                MarketplaceDomain.domain_id == domain_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = MarketplaceDomain(marketplace=MARKETPLACE, domain_id=domain_id)
        session.add(row)
    _set_rule(row, niche_id, SOURCE_PANEL, actor, now)
    await session.flush()
    return await follow_domain_rules(session, actor=actor, domain_ids=[domain_id])


def config_rules(data: dict[str, object]) -> dict[str, str]:
    """Tipo → slug do nicho, a partir do `nichos.json`. Recusa tipo inválido ou em dois nichos."""
    owners: dict[str, str] = {}
    niches = data.get("nichos")
    for item in niches if isinstance(niches, list) else []:
        for domain_id in item.get("dominios", []):
            if not isinstance(domain_id, str) or not DOMAIN_ID.match(domain_id):
                raise DomainRuleError(f"tipo de produto inválido em {item['slug']}: {domain_id!r}")
            if domain_id in owners:
                raise DomainRuleError(
                    f"{domain_id} está em {owners[domain_id]} e em {item['slug']}"
                )
            owners[domain_id] = item["slug"]
    return owners


async def seed_rules(session: AsyncSession, wanted: dict[str, int], *, now: datetime) -> list[str]:
    """Grava as regras do `nichos.json`. Regra do painel fica (o operador decidiu depois); regra do
    JSON que saiu do arquivo some. Devolve os tipos em que a regra do painel prevaleceu."""
    rows = {
        r.domain_id: r
        for r in (
            await session.execute(
                select(MarketplaceDomain)
                .where(MarketplaceDomain.marketplace == MARKETPLACE)
                .limit(RULE_LIMIT)
            )
        ).scalars()
    }
    kept_panel = []
    for domain_id, niche_id in wanted.items():
        row = rows.get(domain_id)
        if row is not None and row.rule_source == SOURCE_PANEL:
            if row.niche_id != niche_id:
                kept_panel.append(domain_id)
            continue
        if row is None:
            row = MarketplaceDomain(marketplace=MARKETPLACE, domain_id=domain_id)
            session.add(row)
        if row.niche_id != niche_id or row.rule_source != SOURCE_CONFIG:
            _set_rule(row, niche_id, SOURCE_CONFIG, CONFIG_ACTOR, now)
    for domain_id, row in rows.items():
        if row.rule_source == SOURCE_CONFIG and domain_id not in wanted:
            row.niche_id = None
            row.rule_source = None
            row.rule_actor = None
            row.rule_at = None
    return kept_panel
