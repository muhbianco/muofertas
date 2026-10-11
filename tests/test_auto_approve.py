from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.distribution.auto_approve import ROBOT_APPROVER, AutoApprover
from app.models import (
    CatalogProduct,
    Channel,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)


async def seed(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    ids: dict[str, int] = {}
    async with sessions() as session, session.begin():
        niche = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=NOW)
        session.add(niche)
        await session.flush()
        session.add(
            Channel(
                niche_id=niche.id,
                name="MegaPromoOz - Ferramentas",
                transport="wuzapi",
                ml_tag="mpoferramentas",
                wa_jid="120363000@g.us",
                created_at=NOW,
            )
        )
        for key, tier, score in (
            ("top", "excelente", 80.0),
            ("boa", "boa", 65.0),
            ("vetado", "excelente", 79.0),
        ):
            product = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLB{key}",
                niche_id=niche.id,
                name=f"Produto {key}",
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=NOW,
                consecutive_failures=0,
                first_seen_at=NOW - timedelta(days=20),
                last_changed_at=NOW - timedelta(hours=2),
                last_tier=tier,
                last_score=score,
            )
            session.add(product)
            await session.flush()
            session.add(
                OfferEvaluation(
                    product_id=product.id,
                    evaluated_at=NOW,
                    trigger="price_change",
                    ruleset="v2",
                    tier=tier,
                    score=score,
                    price_cents=39990,
                    used_official=True,
                    snapshot=json.dumps({"offer_item_id": f"MLBI{key}"}),
                )
            )
            ids[key] = product.id
        # O operador rejeitou depois da última mudança de preço: o robô não passa por cima.
        session.add(
            OperatorDecision(
                product_id=ids["vetado"],
                decision="rejected",
                actor="dono",
                decided_at=NOW - timedelta(hours=1),
            )
        )
    return ids


async def test_only_top_tiers_without_an_operator_decision_are_approved(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed(sessions)
    approver = AutoApprover(
        sessions, tiers=("excepcional", "excelente"), affiliate_tool_id="19964746"
    )
    assert await approver.run(NOW) == 1
    assert await approver.run(NOW) == 0  # já tem publicação desde a mudança de preço
    async with sessions() as session:
        pubs = (await session.execute(select(Publication))).scalars().all()
        decision = (
            await session.execute(
                select(OperatorDecision).where(OperatorDecision.product_id == ids["top"])
            )
        ).scalar_one()
    assert [p.product_id for p in pubs] == [ids["top"]]
    assert pubs[0].state == "awaiting_link"
    assert (decision.decision, decision.actor) == ("approved", ROBOT_APPROVER)


async def test_empty_tier_list_turns_it_off(sessions: async_sessionmaker[AsyncSession]) -> None:
    await seed(sessions)
    assert await AutoApprover(sessions, tiers=(), affiliate_tool_id="x").run(NOW) == 0
