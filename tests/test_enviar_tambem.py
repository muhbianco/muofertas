from __future__ import annotations

import re
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import CatalogProduct, Channel, Niche, Publication
from tests.test_mudar_grupo import _approved_in_the_wrong_group
from tests.test_panel import login, seed_admin


async def test_send_the_same_post_to_more_groups(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    pub_id, tech_id, tools_id = await _approved_in_the_wrong_group(sessions)
    now = utcnow()
    async with sessions() as s, s.begin():
        home = Niche(slug="casa-mimos", name="Casa", active=True, created_at=now)
        s.add(home)
        await s.flush()
        home_channel = Channel(
            niche_id=home.id, name="Grupo Casa", transport="wuzapi", created_at=now
        )
        s.add(home_channel)
        await s.flush()
        channels = {c.niche_id: c.id for c in (await s.execute(select(Channel))).scalars()}
        product = (await s.execute(select(CatalogProduct))).scalar_one()
        # O produto foi para o grupo Casa ontem: lá ele não entra de novo.
        s.add(
            Publication(
                channel_id=home_channel.id,
                product_id=product.id,
                idempotency_key="ontem",
                dedup_key=f"mercadolivre:{product.external_id}",
                state="sent",
                tier="regular",
                target_url="https://x",
                approved_price_cents=4875,
                created_at=now - timedelta(hours=10),
                sent_at=now - timedelta(hours=10),
            )
        )
    tools_ch, tech_ch, home_ch = channels[tools_id], channels[tech_id], home_channel.id
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/postar")).text
    assert (
        f'data-enviar-tambem data-pub="{pub_id}"' in page and '<dialog id="enviar-tambem"' in page
    )
    csrf = re.search(r'name="csrf" value="([^"]+)"', page)
    assert csrf is not None
    url = f"/postar/{pub_id}/copiar"

    r = await client.post(
        url, data={"csrf": csrf.group(1), "channel_id": [str(tools_ch), str(tech_ch), str(home_ch)]}
    )
    assert r.headers["location"] == f"/postar?aviso=copiado-agendado#grupo-{tools_ch}"
    async with sessions() as s:
        pubs = (await s.execute(select(Publication).order_by(Publication.id))).scalars().all()
        product = (await s.execute(select(CatalogProduct))).scalar_one()
    by_channel = {(p.channel_id, p.state) for p in pubs}
    assert (tools_ch, "ready") in by_channel  # o original fica onde estava
    assert (tech_ch, "awaiting_link") in by_channel  # cópia, com link próprio a gerar
    # Saiu lá há 10 h: entra, e espera completar 24 h do envio (era "de fora por 7 dias").
    home = next(p for p in pubs if p.channel_id == home_ch and p.state == "awaiting_link")
    assert home.not_before is not None
    copy = next(p for p in pubs if p.channel_id == tech_ch)
    assert copy.idempotency_key.endswith(f":cp{pub_id}") and copy.pix_price_cents == 4500
    assert (product.niche_id, product.niche_locked) == (tools_id, False)  # nicho não muda

    again = await client.post(url, data={"csrf": csrf.group(1), "channel_id": str(tech_ch)})
    assert again.headers["location"] == f"/postar?aviso=copiado-ja#grupo-{tools_ch}"
    nothing = await client.post(url, data={"csrf": csrf.group(1)})
    assert nothing.headers["location"] == f"/postar?aviso=copiar-erro#grupo-{tools_ch}"
    async with sessions() as s:
        assert len((await s.execute(select(Publication))).scalars().all()) == len(pubs)
