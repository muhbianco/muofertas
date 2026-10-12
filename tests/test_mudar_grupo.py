from __future__ import annotations

import json
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.distribution.service import publications_for_approval
from app.models import (
    CatalogProduct,
    Channel,
    Niche,
    OfferEvaluation,
    OperatorDecision,
    Publication,
)
from tests.test_panel import login, seed_admin


async def _approved_in_the_wrong_group(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[int, int, int]:
    """Produto de tecnologia aprovado no grupo de Ferramentas, com Pix e últimas unidades."""
    now = utcnow()
    async with sessions() as s, s.begin():
        tools = Niche(slug="ferramentas", name="Ferramentas", active=True, created_at=now)
        tech = Niche(slug="tecnologia", name="Tecnologia", active=True, created_at=now)
        s.add_all([tools, tech])
        await s.flush()
        s.add_all(
            [
                Channel(
                    niche_id=tools.id,
                    name="Grupo Ferramentas",
                    transport="wuzapi",
                    ml_tag="mpoferramentas",
                    created_at=now,
                ),
                Channel(
                    niche_id=tech.id,
                    name="Grupo Tecnologia",
                    transport="wuzapi",
                    ml_tag="mpotecnologia",
                    created_at=now,
                ),
            ]
        )
        product = CatalogProduct(
            marketplace="mercadolivre",
            external_id="MLBPAD",
            niche_id=tools.id,
            name="Controle Redfin PS4",
            discovered_via="highlights",
            active=True,
            tier="warm",
            next_check_at=now,
            consecutive_failures=0,
            first_seen_at=now,
        )
        s.add(product)
        await s.flush()
        ev = OfferEvaluation(
            product_id=product.id,
            evaluated_at=now,
            trigger="daily",
            ruleset="v2",
            tier="regular",
            score=40.0,
            price_cents=4875,
            used_official=False,
            snapshot=json.dumps({"offer_item_id": "MLB1"}),
        )
        decision = OperatorDecision(
            product_id=product.id, decision="approved", actor="dono", decided_at=now
        )
        s.add_all([ev, decision])
        await s.flush()
        await publications_for_approval(
            s,
            product=product,
            evaluation=ev,
            decision=decision,
            now=now,
            pix_cents=4500,
            last_units=4,
            affiliate_tool_id="19964746",
        )
        await s.flush()
        pub = (await s.execute(select(Publication))).scalar_one()
        pub.state, pub.affiliate_url = "ready", "https://meli.la/ferramentas"  # link já gerado
        return pub.id, tech.id, tools.id


async def test_move_a_post_to_another_group(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    pub_id, tech_id, tools_id = await _approved_in_the_wrong_group(sessions)
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/postar")).text
    assert f'data-mudar-grupo data-pub="{pub_id}"' in page and '<dialog id="mudar-grupo"' in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None

    same = await client.post(
        f"/postar/{pub_id}/grupo", data={"csrf": csrf.group(1), "niche_id": tools_id}
    )
    assert same.headers["location"] == "/postar?aviso=mover-erro"  # já está nesse grupo

    r = await client.post(
        f"/postar/{pub_id}/grupo", data={"csrf": csrf.group(1), "niche_id": tech_id}
    )
    async with sessions() as s:
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
        product = (await s.execute(select(CatalogProduct))).scalar_one()
        tech_channel = (
            await s.execute(select(Channel).where(Channel.niche_id == tech_id))
        ).scalar_one()
    assert r.headers["location"] == f"/postar?aviso=movido#grupo-{tech_channel.id}"
    old, new = pubs
    assert old.state == "discarded" and "Tecnologia" in (old.note or "")
    assert (new.channel_id, new.state) == (tech_channel.id, "awaiting_link")
    assert new.idempotency_key.endswith(f":mv{old.id}")
    # Pix e últimas unidades vão junto, com a hora da aprovação (a validade não se renova).
    assert (new.pix_price_cents, new.last_units) == (4500, 4)
    assert new.pix_at == old.pix_at and new.last_units_at == old.last_units_at
    # Só este produto mudou de nicho, e fica lá (o seed não o move de volta).
    assert (product.niche_id, product.niche_locked) == (tech_id, True)

    again = await client.post(
        f"/postar/{pub_id}/grupo", data={"csrf": csrf.group(1), "niche_id": tools_id}
    )
    assert again.headers["location"] == "/postar?aviso=mover-erro"  # o antigo já saiu da fila

    detail = (await client.get(f"/postar/{new.id}")).text
    assert f'action="/postar/{new.id}/grupo"' in detail and "Grupo Ferramentas" in detail
