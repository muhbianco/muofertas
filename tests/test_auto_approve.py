from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
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


# --------------------------------------------------------------------------- desde 26/10/2026


async def seed_general(
    sessions: async_sessionmaker[AsyncSession],
    products: list[tuple[str, str, float, int | None, int | None]],
    *,
    niche_paused: bool = True,
    batch_size: int = 2,
) -> dict[str, int]:
    """Nicho Ferramentas com o grupo próprio (pausado) e o grupo geral da família, marcado
    "sempre", em lote. `products`: (chave, nível, nota, % OFF no anúncio, posição nos vendidos)."""
    ids: dict[str, int] = {}
    async with sessions() as session, session.begin():
        tools = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=NOW)
        family = Niche(slug="pais-maes-filhos", name="Pais", active=True, created_at=NOW)
        session.add_all([tools, family])
        await session.flush()
        session.add_all(
            [
                Channel(
                    niche_id=tools.id,
                    name="MegaPromoOz - Ferramentas",
                    transport="wuzapi",
                    ml_tag="mpoferramentas",
                    wa_jid="120363001@g.us",
                    paused=niche_paused,
                    created_at=NOW,
                ),
                Channel(
                    niche_id=family.id,
                    name=FAMILY,
                    transport="wuzapi",
                    ml_tag="mpofamilia",
                    wa_jid="120363006@g.us",
                    always_checked=True,
                    send_mode="lote",
                    batch_size=batch_size,
                    batch_every_minutes=180,
                    created_at=NOW,
                ),
            ]
        )
        for key, tier, score, off, position in products:
            product = CatalogProduct(
                marketplace="mercadolivre",
                external_id=f"MLB{key}",
                niche_id=tools.id,
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
                listing_discount_pct=off,
                best_highlight_position=position,
            )
            session.add(product)
            await session.flush()
            session.add(
                OfferEvaluation(
                    product_id=product.id,
                    evaluated_at=NOW,
                    trigger="daily",
                    ruleset="v3",
                    tier=tier,
                    score=score,
                    price_cents=39990,
                    used_official=True,
                    snapshot=json.dumps({"offer_item_id": f"MLBI{key}"}),
                )
            )
            ids[key] = product.id
    return ids


ALL_TIERS = ("excepcional", "excelente", "boa", "regular")
FAMILY = "MegaPromoOz - Pais, Mães e Filhos"


async def waiting_by_group(sessions: async_sessionmaker[AsyncSession]) -> dict[str, list[int]]:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Channel.name, Publication.product_id, Publication.state)
                .join(Channel, Channel.id == Publication.channel_id)
                .order_by(Publication.id)
            )
        ).all()
    out: dict[str, list[int]] = {}
    for name, product_id, state in rows:
        out.setdefault(f"{name}:{state}", []).append(product_id)
    return out


async def test_goes_to_the_general_group_when_the_niche_group_is_paused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_general(sessions, [("top", "excelente", 80.0, None, None)])
    approver = AutoApprover(sessions, tiers=ALL_TIERS, affiliate_tool_id="19964746")
    assert await approver.run(NOW) == 1
    # Antes: uma publicação "skipped" no grupo pausado e nada no geral.
    assert await waiting_by_group(sessions) == {f"{FAMILY}:awaiting_link": [ids["top"]]}


async def test_good_and_regular_only_fill_the_queue_in_the_approval_queue_order(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_general(
        sessions,
        [
            ("reg30", "regular", 40.0, 30, None),
            ("reg50", "regular", 35.0, 50, None),
            ("reg30top", "regular", 30.0, 30, 1),  # 30% no mais vendido pesa mais que 50%
            ("boa", "boa", 65.0, None, None),  # boa sem % OFF entra, depois dos com % OFF
            ("regsem", "regular", 55.0, None, None),  # regular sem % OFF nunca
            ("reg10", "regular", 50.0, 10, None),
        ],
        batch_size=2,  # teto: 2 lotes = 4 esperando
    )
    approver = AutoApprover(sessions, tiers=ALL_TIERS, affiliate_tool_id="19964746")
    assert await approver.run(NOW) == 4
    assert await approver.run(NOW) == 0  # fila cheia
    queued = (await waiting_by_group(sessions))[f"{FAMILY}:awaiting_link"]
    assert queued == [ids["reg30top"], ids["reg50"], ids["reg30"], ids["reg10"]]
    # Saiu um da fila (enviado): abre uma vaga e entra o próximo, a boa sem % OFF.
    async with sessions() as session, session.begin():
        pub = (
            await session.execute(
                select(Publication).where(Publication.product_id == ids["reg30top"])
            )
        ).scalar_one()
        pub.state = "sent"
    assert await approver.run(NOW) == 1
    after = await waiting_by_group(sessions)
    assert ids["boa"] in after[f"{FAMILY}:awaiting_link"]
    assert all(ids["regsem"] not in group for group in after.values())


async def test_top_tiers_ignore_the_cap(sessions: async_sessionmaker[AsyncSession]) -> None:
    ids = await seed_general(
        sessions,
        [
            ("r1", "regular", 40.0, 30, None),
            ("r2", "regular", 40.0, 20, None),
            ("r3", "regular", 40.0, 10, None),
            ("top", "boa", 70.0, None, None),  # vira excepcional depois da fila encher
        ],
        batch_size=1,  # teto: 2
    )
    approver = AutoApprover(sessions, tiers=ALL_TIERS, affiliate_tool_id="19964746")
    assert await approver.run(NOW) == 2  # r1 e r2: fila cheia
    async with sessions() as session, session.begin():
        product = await session.get(CatalogProduct, ids["top"])
        evaluation = (
            await session.execute(
                select(OfferEvaluation).where(OfferEvaluation.product_id == ids["top"])
            )
        ).scalar_one()
        assert product is not None
        product.last_tier = evaluation.tier = "excepcional"
        product.last_score = evaluation.score = 90.0
    assert await approver.run(NOW) == 1  # excepcional passa mesmo com a fila cheia
    queued = (await waiting_by_group(sessions))[f"{FAMILY}:awaiting_link"]
    assert queued == [ids["r1"], ids["r2"], ids["top"]]


async def test_without_an_active_group_nothing_is_recorded(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_general(sessions, [("top", "excelente", 80.0, None, None)])
    async with sessions() as session, session.begin():
        for channel in (await session.execute(select(Channel))).scalars():
            channel.paused = True
    approver = AutoApprover(sessions, tiers=ALL_TIERS, affiliate_tool_id="19964746")
    assert await approver.run(NOW) == 0
    async with sessions() as session:
        # Sem decisão: quando um grupo voltar, o produto ainda pode ser aprovado.
        assert (await session.execute(select(OperatorDecision))).first() is None
        assert (await session.execute(select(Publication))).first() is None


async def test_fill_search_rests_a_minute_after_a_round_with_nothing_to_approve(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed_general(sessions, [("sem", "regular", 50.0, None, None)])
    approver = AutoApprover(sessions, tiers=ALL_TIERS, affiliate_tool_id="19964746")
    assert await approver.run(NOW) == 0  # regular sem % OFF: nada para encher
    async with sessions() as session, session.begin():
        product = await session.get(CatalogProduct, ids["sem"])
        assert product is not None
        product.listing_discount_pct = 30  # agora tem % OFF
    assert await approver.run(NOW + timedelta(seconds=30)) == 0  # ainda descansando
    assert await approver.run(NOW + timedelta(seconds=61)) == 1


def test_default_tiers_include_good_and_regular() -> None:
    assert Settings().auto_approve_tier_list == ("excepcional", "excelente", "boa", "regular")
