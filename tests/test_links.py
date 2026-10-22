"""Link curto oficial pelo gerador do portal do ML, com recuo para o link longo."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import gather_facts
from app.core.config import Settings
from app.distribution.links import LONG_LINK_NOTE, resolve_links
from app.domain.alerts import firing
from app.integrations.ml_affiliate import (
    AffiliatePortal,
    PortalError,
    PortalSession,
    read_session,
)
from app.models import Channel, IntegrationError, Niche, PortalStatus, Publication
from tests.test_sender import approve

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
SESSION = PortalSession(cookie="ssid=abc; _csrf=def", csrf="tok-123")  # gitleaks:allow


def portal(handler: object) -> AffiliatePortal:
    return AffiliatePortal(Settings(), transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def ok_link(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["tag"] == "megapromooz" and len(body["urls"]) == 1
    return httpx.Response(
        200,
        json={
            "status": 200,
            "urls": [
                {
                    "id": "1aQGY1E",
                    "tag": body["tag"],
                    "short_url": "https://meli.la/1aQGY1E",
                    "long_url": "https://www.mercadolivre.com.br/social/x?matt_word=megapromooz",
                }
            ],
        },
    )


def test_read_session(tmp_path: Path) -> None:
    f = tmp_path / "ml-session"
    f.write_text('cookie=ssid=abc; x={"a":1}\ncsrf=tok-123\n', encoding="utf-8")  # gitleaks:allow
    s = read_session(str(f))
    assert s is not None and s.cookie == 'ssid=abc; x={"a":1}' and s.csrf == "tok-123"
    f.write_text("cookie=so-cookie\n", encoding="utf-8")  # gitleaks:allow
    assert read_session(str(f)) is None
    assert read_session(str(tmp_path / "nao-existe")) is None


async def test_portal_sends_session_headers_and_reads_short_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_link(request)

    client = portal(handler)
    try:
        link = await client.create_link(
            SESSION, "https://www.mercadolivre.com.br/p/MLB1", "megapromooz"
        )
    finally:
        await client.aclose()
    assert link == "https://meli.la/1aQGY1E"
    (request,) = seen
    assert request.url.path == "/affiliate-program/api/v2/affiliates/createLink"
    assert request.headers["cookie"] == SESSION.cookie
    assert request.headers["x-csrf-token"] == SESSION.csrf
    assert request.headers["referer"].endswith("/afiliados/linkbuilder")


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(401, json={"error": "unauthorized"}), "session"),
        (httpx.Response(403, text="<html>captcha</html>"), "blocked"),
        (
            httpx.Response(302, headers={"location": "https://www.mercadolivre.com.br/login"}),
            "blocked",
        ),
        (httpx.Response(200, text="<html>verifique</html>"), "blocked"),
        (httpx.Response(503, json={"error": "x"}), "transient"),
        (httpx.Response(200, json={"urls": [{"short_url": "https://evil.example/x"}]}), "rejected"),
    ],
)
async def test_portal_failures(response: httpx.Response, kind: str) -> None:
    client = portal(lambda r: response)
    try:
        with pytest.raises(PortalError) as err:
            await client.create_link(SESSION, "https://x", "megapromooz")
    finally:
        await client.aclose()
    assert err.value.kind == kind


def settings_with(session_file: str) -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        ml_affiliate_tool_id="19964746",
        ml_session_file=session_file,
    )


async def pending_publication(sessions: async_sessionmaker[AsyncSession]) -> int:
    pub_id = await approve(sessions)
    async with sessions() as session, session.begin():
        pub = await session.get(Publication, pub_id)
        assert pub is not None
        pub.state, pub.affiliate_url, pub.link_at = "awaiting_link", None, None
    return pub_id


async def test_short_link_when_session_works(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    pub_id = await pending_publication(sessions)
    f = tmp_path / "ml-session"
    f.write_text("cookie=ssid=abc\ncsrf=tok-123\n", encoding="utf-8")  # gitleaks:allow
    client = portal(ok_link)
    try:
        assert await resolve_links(sessions, client, settings_with(str(f)), NOW, pause=0) == 1
    finally:
        await client.aclose()
    async with sessions() as session:
        pub = await session.get(Publication, pub_id)
    assert pub is not None and (pub.state, pub.affiliate_url) == (
        "ready",
        "https://meli.la/1aQGY1E",
    )


async def test_expired_session_falls_back_to_long_link_and_alerts(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    pub_id = await pending_publication(sessions)
    f = tmp_path / "ml-session"
    f.write_text("cookie=ssid=velho\ncsrf=tok-velho\n", encoding="utf-8")  # gitleaks:allow
    client = portal(lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    try:
        assert await resolve_links(sessions, client, settings_with(str(f)), NOW, pause=0) == 1
    finally:
        await client.aclose()
    async with sessions() as session:
        pub = await session.get(Publication, pub_id)
        error = (await session.execute(select(IntegrationError))).scalar_one()
        facts = await gather_facts(session, NOW)
    assert pub is not None and pub.state == "ready"
    assert pub.affiliate_url and "matt_tool=19964746" in pub.affiliate_url
    assert pub.note == LONG_LINK_NOTE
    assert (error.source, error.error_code) == ("ml_portal", "session")
    assert "sessao_ml" in firing(facts)


async def test_group_without_tag_waits_for_manual_link(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    pub_id = await pending_publication(sessions)
    async with sessions() as session, session.begin():
        channel = (await session.execute(select(Channel))).scalar_one()
        channel.ml_tag = None
        assert (await session.execute(select(Niche))).scalar_one()
    client = portal(ok_link)
    try:
        assert (
            await resolve_links(sessions, client, settings_with("/nao/existe"), NOW, pause=0) == 0
        )
    finally:
        await client.aclose()
    async with sessions() as session:
        pub = await session.get(Publication, pub_id)
    assert pub is not None and pub.state == "awaiting_link"


# --------------------------------------------------------------------------- alerta da sessão


async def pending_publication_again(sessions: async_sessionmaker[AsyncSession]) -> int:
    """A mesma publicação de volta a esperar link (nova rodada do gerador)."""
    async with sessions() as session, session.begin():
        pub = (
            await session.execute(select(Publication).order_by(Publication.id).limit(1))
        ).scalar_one()
        pub.state, pub.affiliate_url, pub.link_at, pub.note = "awaiting_link", None, None, None
        return pub.id


async def second_pending(sessions: async_sessionmaker[AsyncSession], pub_id: int) -> int:
    """Outra publicação esperando link, cópia da primeira (mesma rodada do gerador)."""
    async with sessions() as session, session.begin():
        first = await session.get(Publication, pub_id)
        assert first is not None
        values = {
            c.key: getattr(first, c.key) for c in Publication.__table__.columns if c.key != "id"
        }
        values["idempotency_key"] = f"{first.idempotency_key}-2"
        copy = Publication(**values)
        session.add(copy)
        await session.flush()
        return copy.id


async def run_links(
    sessions: async_sessionmaker[AsyncSession], handler: object, f: Path, now: datetime
) -> int:
    client = portal(handler)
    try:
        return await resolve_links(sessions, client, settings_with(str(f)), now, pause=0)
    finally:
        await client.aclose()


def session_file(tmp_path: Path) -> Path:
    f = tmp_path / "ml-session"
    f.write_text("cookie=ssid=abc\ncsrf=tok-123\n", encoding="utf-8")  # gitleaks:allow
    return f


async def alerts_at(sessions: async_sessionmaker[AsyncSession], now: datetime) -> dict[str, str]:
    async with sessions() as session:
        return firing(await gather_facts(session, now))


async def test_short_link_after_renewal_closes_the_session_alert(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    f = session_file(tmp_path)
    await pending_publication(sessions)
    await run_links(sessions, lambda r: httpx.Response(401, json={"error": "x"}), f, NOW)
    assert "sessao_ml" in await alerts_at(sessions, NOW + timedelta(minutes=5))
    # Sessão renovada: o próximo link sai curto e o alerta fecha sem esperar as 3 h.
    pub_id = await pending_publication_again(sessions)
    assert await run_links(sessions, ok_link, f, NOW + timedelta(minutes=30)) == 1
    assert "sessao_ml" not in await alerts_at(sessions, NOW + timedelta(minutes=31))
    async with sessions() as session:
        status = await session.get(PortalStatus, "ml_portal")
        pub = await session.get(Publication, pub_id)
    assert status is not None and status.last_ok_at == NOW + timedelta(minutes=30)
    assert pub is not None and pub.affiliate_url == "https://meli.la/1aQGY1E"
    # Recusa nova depois do acerto reabre.
    await pending_publication_again(sessions)
    await run_links(
        sessions, lambda r: httpx.Response(401, json={"error": "x"}), f, NOW + timedelta(hours=1)
    )
    assert "sessao_ml" in await alerts_at(sessions, NOW + timedelta(hours=1, minutes=1))


async def test_refusal_after_a_short_link_in_the_same_round_keeps_the_alert(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    f = session_file(tmp_path)
    first = await pending_publication(sessions)
    await second_pending(sessions, first)
    calls: list[int] = []

    def ok_then_401(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return ok_link(request) if len(calls) == 1 else httpx.Response(401, json={"e": "x"})

    assert await run_links(sessions, ok_then_401, f, NOW) == 2
    assert "sessao_ml" in await alerts_at(sessions, NOW + timedelta(minutes=1))
    async with sessions() as session:
        assert await session.get(PortalStatus, "ml_portal") is None


async def test_product_refused_by_the_generator_says_nothing_about_the_session(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    f = session_file(tmp_path)
    first = await pending_publication(sessions)
    await second_pending(sessions, first)
    calls: list[int] = []

    def ok_then_1008(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return ok_link(request)
        item = {"message": "The condition is unknown", "error_code": 1008, "status": 200}
        return httpx.Response(200, json={"status": 200, "urls": [item]})

    assert await run_links(sessions, ok_then_1008, f, NOW) == 2
    async with sessions() as session:
        status = await session.get(PortalStatus, "ml_portal")
        errors = (await session.execute(select(IntegrationError.error_code))).scalars().all()
    assert status is not None and status.last_ok_at == NOW
    assert errors == ["rejected"]
    assert "sessao_ml" not in await alerts_at(sessions, NOW + timedelta(minutes=1))
