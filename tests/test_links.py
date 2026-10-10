"""Link curto oficial pelo gerador do portal do ML, com recuo para o link longo."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from app.models import Channel, IntegrationError, Niche, Publication
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
