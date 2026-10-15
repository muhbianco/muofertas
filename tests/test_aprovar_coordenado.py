"""Dois operadores na mesma fila (pedido do dono em 15/10/2026): o segundo a aprovar fica sabendo
quem chegou antes, e as marcas ✓/✕ de um aparecem para o outro como "em aprovação por X"."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.main import app
from app.models import AdminUser, ApprovalClaim, OperatorDecision, Publication
from app.panel.security import TOTP_STEP_SECONDS, hash_password, new_totp_secret, totp_at
from tests.test_aprovar_grupos import _whey
from tests.test_distribution_panel import csrf_of
from tests.test_panel import PASSWORD, login, seed_admin

JSON = {"Accept": "application/json"}


@pytest.fixture
async def danilo(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[httpx.AsyncClient]:
    """O segundo operador, com o próprio navegador (cookie de sessão separado)."""
    secret = new_totp_secret()
    async with sessions() as s, s.begin():
        s.add(
            AdminUser(
                username="danilo.correa",
                password_hash=hash_password(PASSWORD),
                totp_secret=secret,
                totp_last_step=0,
                active=True,
                created_at=utcnow(),
            )
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
        code = totp_at(secret, int(time.time() // TOTP_STEP_SECONDS))
        r = await other.post(
            "/login", data={"username": "danilo.correa", "password": PASSWORD, "code": code}
        )
        assert r.status_code == 303
        yield other


async def _approve_form(
    c: httpx.AsyncClient, ids: dict[str, int], path: str = "/"
) -> dict[str, object]:
    page = (await c.get(path)).text
    seen = page.split('name="vista_em" value="', 1)[1].split('"', 1)[0]
    return {
        "csrf": csrf_of(page),
        "product_id": ids["product"],
        "evaluation_id": ids["ev"],
        "decision": "approved",
        "next": "/",
        "grupos": "1",
        "channel_id": str(ids["sport"]),
        "vista_em": seen,
    }


async def _decisions(sessions: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
    async with sessions() as s:
        rows = await s.execute(
            select(OperatorDecision.actor, OperatorDecision.decision).order_by(OperatorDecision.id)
        )
        return [tuple(r) for r in rows.all()]  # type: ignore[misc]


async def test_second_approval_says_who_came_first_and_records_nothing(
    client: httpx.AsyncClient,
    danilo: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Antes: o segundo recebia "Aprovada." sem nada acontecer e o Pix dele sumia."""
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    mine = await _approve_form(client, ids)
    his = await _approve_form(danilo, ids)  # as duas páginas abertas antes de qualquer decisão

    first = await danilo.post("/decisao", data=his, headers=JSON)
    assert first.status_code == 200 and first.json()["ok"] is True

    late = await client.post("/decisao", data=mine | {"pix": "119,90"}, headers=JSON)
    assert late.status_code == 409
    body = late.json()
    assert body["ok"] is False and body["decidido"] is True and body["aviso"] == "ja-decidido"
    assert body["mensagem"].startswith("danilo.correa já aprovou este produto (")
    assert "Enviar também em" in body["mensagem"]
    # Rejeitar o que já foi aprovado também chega tarde, com o caminho certo.
    rejected = await client.post("/decisao", data=mine | {"decision": "rejected"}, headers=JSON)
    assert rejected.status_code == 409 and "use ✕ no Postar" in rejected.json()["mensagem"]
    # O próprio danilo, de outra aba: "Você já aprovou".
    again = await danilo.post("/decisao", data=his, headers=JSON)
    assert again.json()["mensagem"].startswith("Você já aprovou este produto (")

    assert await _decisions(sessions) == [("danilo.correa", "approved")]
    async with sessions() as s:
        pubs = (await s.execute(select(Publication.pix_price_cents))).scalars().all()
    assert pubs == [None]  # um post só, sem o Pix de quem chegou tarde

    # Sem JavaScript: volta para a fila com o aviso, sem gravar.
    plain = await client.post("/decisao", data=mine)
    assert plain.headers["location"] == "/?aviso=ja-decidido"
    assert (
        "Outro operador já decidiu este produto" in (await client.get("/?aviso=ja-decidido")).text
    )


async def test_rejection_by_the_other_after_the_page_opened_wins(
    client: httpx.AsyncClient,
    danilo: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    mine = await _approve_form(client, ids)
    his = await _approve_form(danilo, ids)
    await danilo.post("/decisao", data=his | {"decision": "rejected"}, headers=JSON)

    late = await client.post("/decisao", data=mine, headers=JSON)
    assert late.status_code == 409
    assert late.json()["mensagem"].startswith("danilo.correa já rejeitou este produto (")
    # Rejeitado sai da fila; a página do produto aberta depois da rejeição aprova, como antes.
    fresh = await _approve_form(client, ids, f"/produto/{ids['product']}")
    ok = await client.post("/decisao", data=fresh, headers=JSON)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert await _decisions(sessions) == [
        ("danilo.correa", "rejected"),
        ("dono", "approved"),
    ]


async def test_discarded_post_can_be_approved_again(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Post tirado da fila (troca de nicho, ✕ no Postar) não conta como aprovado."""
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    form = await _approve_form(client, ids)
    assert (await client.post("/decisao", data=form, headers=JSON)).status_code == 200
    async with sessions() as s, s.begin():
        await s.execute(update(Publication).values(state="discarded"))
    form = await _approve_form(client, ids, f"/produto/{ids['product']}")
    again = await client.post(
        "/decisao", data=form | {"channel_id": str(ids["family"])}, headers=JSON
    )
    assert again.status_code == 200 and again.json()["ok"] is True


async def test_commit_failure_is_not_reported_as_approved(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O FastAPI responde antes do commit da dependência: a falha virava "Aprovada."."""
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    form = await _approve_form(client, ids)
    original = AsyncSession.commit
    calls = {"n": 0}

    async def fail_once(self: AsyncSession) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("Duplicate entry"))
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", fail_once)
    r = await client.post("/decisao", data=form, headers=JSON)
    monkeypatch.setattr(AsyncSession, "commit", original)
    assert r.status_code == 409
    assert r.json()["mensagem"] == (
        "Outro operador decidiu este produto agora mesmo; a fila foi atualizada."
    )
    async with sessions() as s:
        assert (await s.execute(select(func.count(OperatorDecision.id)))).scalar_one() == 0


async def test_marks_are_shared_and_lock_the_other_operator(
    client: httpx.AsyncClient,
    danilo: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    product = ids["product"]
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/")).text
    assert 'class="fila-main" data-agora="' in page and "data-trava" in page
    assert '<script src="/static/fila.js?v=8" defer></script>' in page
    mine = {"csrf": csrf_of(page)}
    his = {"csrf": csrf_of((await danilo.get("/")).text)}
    board = {"ids": f"{product},999999,abc", "desde": utcnow().isoformat()}

    assert (
        await client.post("/fila/marca", data=mine | {"product_id": product, "marca": "x"})
    ).status_code == 422
    assert (
        await client.post("/fila/marca", data={"product_id": product, "marca": "approved"})
    ).status_code == 403
    ok = await client.post("/fila/marca", data=mine | {"product_id": product, "marca": "approved"})
    assert ok.json() == {"ok": True}

    taken = await danilo.post(
        "/fila/marca", data=his | {"product_id": product, "marca": "rejected"}
    )
    assert taken.status_code == 409
    assert taken.json()["por"] == "dono"
    assert taken.json()["mensagem"].startswith("Em aprovação por dono (marcou há 0 min)")

    seen_by_him = (await danilo.post("/fila/estado", data=his | board)).json()
    assert seen_by_him["outros"] == {
        str(product): {"por": "dono", "marca": "approved", "ha_min": 0}
    }
    assert seen_by_him["meus"] == {} and seen_by_him["decididos"] == {}
    seen_by_me = (await client.post("/fila/estado", data=mine | board)).json()
    assert seen_by_me["meus"] == {str(product): "approved"} and seen_by_me["outros"] == {}

    # A aba do dono parou de dar sinal: depois de 5 min, o danilo pode assumir.
    async with sessions() as s, s.begin():
        await s.execute(update(ApprovalClaim).values(seen_at=utcnow() - timedelta(minutes=6)))
    taken = await danilo.post(
        "/fila/marca", data=his | {"product_id": product, "marca": "approved"}
    )
    assert taken.json() == {"ok": True}
    seen_by_me = (await client.post("/fila/estado", data=mine | board)).json()
    assert seen_by_me["outros"][str(product)]["por"] == "danilo.correa"
    assert seen_by_me["meus"] == {}

    # Desmarcar libera; desmarcar o que é do outro não mexe nele.
    await client.post("/fila/marca", data=mine | {"product_id": product, "marca": ""})
    seen_by_him = (await danilo.post("/fila/estado", data=his | board)).json()
    assert seen_by_him["meus"] == {str(product): "approved"}
    await danilo.post("/fila/marca", data=his | {"product_id": product, "marca": ""})
    async with sessions() as s:
        assert (await s.execute(select(func.count()).select_from(ApprovalClaim))).scalar_one() == 0


async def test_decision_clears_marks_and_shows_up_for_the_other(
    client: httpx.AsyncClient,
    danilo: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    product = ids["product"]
    assert (await login(client, secret)).status_code == 303
    page = (await client.get("/")).text
    opened = page.split('data-agora="', 1)[1].split('"', 1)[0]
    mine = {"csrf": csrf_of(page)}
    await client.post("/fila/marca", data=mine | {"product_id": product, "marca": "approved"})

    his = await _approve_form(danilo, ids)  # ele decide mesmo assim (a tela pede confirmação)
    assert (await danilo.post("/decisao", data=his, headers=JSON)).status_code == 200

    board = (
        await client.post("/fila/estado", data=mine | {"ids": str(product), "desde": opened})
    ).json()
    assert board["meus"] == {} and board["outros"] == {}
    assert board["decididos"][str(product)]["mensagem"].startswith("danilo.correa aprovou (")
    async with sessions() as s:
        assert (await s.execute(select(func.count()).select_from(ApprovalClaim))).scalar_one() == 0


async def test_stale_marks_are_cleaned(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    secret = await seed_admin(sessions)
    ids = await _whey(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/")).text)
    await client.post(
        "/fila/marca", data={"csrf": csrf, "product_id": ids["product"], "marca": "approved"}
    )
    async with sessions() as s, s.begin():
        await s.execute(update(ApprovalClaim).values(seen_at=utcnow() - timedelta(hours=7)))
    board = (await client.post("/fila/estado", data={"csrf": csrf, "ids": ""})).json()
    assert board == {"meus": {}, "outros": {}, "decididos": {}}
    async with sessions() as s:
        assert (await s.execute(select(func.count()).select_from(ApprovalClaim))).scalar_one() == 0
