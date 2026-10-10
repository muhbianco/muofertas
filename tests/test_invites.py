"""Convite de operador: o convidado cria a própria senha e o próprio autenticador."""

from __future__ import annotations

import json
import re
import time

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import AdminInvite, AdminUser
from app.panel import invites
from app.panel.security import TOTP_STEP_SECONDS, totp_at
from tests.test_panel import login, seed_admin

PASSWORD = "senha-do-primo-123"


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Fluxo do n8n falso: guarda o que seria enviado."""
    box: list[dict[str, str]] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        box.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def fake_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        settings, "invite_email_webhook_url", "https://n8n.test/webhook/megapromooz-convite"
    )
    monkeypatch.setattr("app.panel.operators_routes.httpx.AsyncClient", fake_client)
    return box


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def test_invite_flow_creates_operator_with_own_password_and_totp(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    mailbox: list[dict[str, str]],
) -> None:
    secret = await seed_admin(sessions)
    assert (await login(client, secret)).status_code == 303
    page = await client.get("/operadores")
    token = csrf_of(page.text)
    bad = await client.post("/operadores/convite", data={"csrf": token, "email": "não-é-email"})
    assert bad.status_code == 422
    sent = await client.post(
        "/operadores/convite", data={"csrf": token, "email": "Danilo@Example.com"}
    )
    assert "Convite enviado para danilo@example.com" in sent.text
    (mail,) = mailbox
    assert (mail["app"], mail["kind"], mail["to"]) == (
        "megapromooz",
        "invite",
        "danilo@example.com",
    )
    link = mail["link"]
    assert link.startswith(f"{settings.panel_url}/convite/")
    path = link.removeprefix(settings.panel_url)
    async with sessions() as session:
        row = (await session.execute(select(AdminInvite))).scalar_one()
    assert path.rsplit("/", 1)[1] not in row.token_hash  # só o hash fica no banco

    # o convidado não tem sessão: abre o link, vê o QR e cria o acesso
    client.cookies.clear()
    invite_page = await client.get(path)
    assert invite_page.status_code == 200 and 'src="data:image/svg+xml' in invite_page.text
    async with sessions() as session:
        totp_secret = (await session.execute(select(AdminInvite.totp_secret))).scalar_one()
    assert totp_secret
    wrong = await client.post(
        path,
        data={
            "username": "danilo",
            "password": PASSWORD,
            "password_again": PASSWORD,
            "code": "000000",
        },
    )
    assert wrong.status_code == 422 and "não confere" in wrong.text
    code = totp_at(totp_secret, int(time.time() // TOTP_STEP_SECONDS))
    done = await client.post(
        path,
        data={"username": "Danilo", "password": PASSWORD, "password_again": PASSWORD, "code": code},
    )
    assert done.status_code == 200 and "Pronto, danilo" in done.text
    async with sessions() as session:
        user = (
            await session.execute(select(AdminUser).where(AdminUser.username == "danilo"))
        ).scalar_one()
        row = (await session.execute(select(AdminInvite))).scalar_one()
    assert (
        user.active and user.totp_secret == totp_secret and row.used_at and row.totp_secret is None
    )
    # uso único
    assert (await client.get(path)).status_code == 404


async def test_invite_without_email_shows_link_once_and_attempts_are_capped(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "invite_email_webhook_url", "")
    secret = await seed_admin(sessions)
    assert (await login(client, secret)).status_code == 303
    token = csrf_of((await client.get("/operadores")).text)
    page = await client.post("/operadores/convite", data={"csrf": token, "email": "x@example.com"})
    assert "O e-mail não saiu" in page.text
    match = re.search(r"(/convite/[A-Za-z0-9_-]+)", page.text)
    assert match is not None
    path = match.group(1)
    client.cookies.clear()
    for _ in range(invites.MAX_ATTEMPTS):
        await client.post(
            path,
            data={
                "username": "x1",
                "password": PASSWORD,
                "password_again": "outra-senha-123",
                "code": "1",
            },
        )
    assert (await client.get(path)).status_code == 404  # tentativas demais invalidam
    assert (await client.get("/convite/nao-existe")).status_code == 404
