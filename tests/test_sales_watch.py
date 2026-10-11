from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.integrations.ml_affiliate import AffiliatePortal
from app.measurement.sales import SalesWatch
from app.models import SalesCheck

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
FAKE_SESSION = "cookie=sessao-de-teste\ncsrf=token-de-teste\n"  # gitleaks:allow


def settings_with(session_file: Path) -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        ml_session_file=str(session_file),
        alert_email_webhook_url="https://n8n.test/webhook/megapromooz-email",
        alert_email_to="dono@example.com",
    )


async def test_mail_on_first_and_new_sales_without_storing_values(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    session_file = tmp_path / "ml-session"
    session_file.write_text(FAKE_SESSION, encoding="utf-8")
    totals = iter([0, 1, 1, 3])
    calls: list[httpx.Request] = []

    def portal(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        total = next(totals)
        items = [{"order_id": 123, "tag": "mpofamilia", "buyer": "x"}] if total else []
        return httpx.Response(
            200,
            json={"item_list": items, "total_results": total, "page": 1, "items_per_page": 10},
        )

    mails: list[dict[str, str]] = []

    def n8n(request: httpx.Request) -> httpx.Response:
        mails.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    cfg = settings_with(session_file)
    client = AffiliatePortal(cfg, transport=httpx.MockTransport(portal))
    http = httpx.AsyncClient(transport=httpx.MockTransport(n8n))
    watch = SalesWatch(sessions, client, cfg, http)
    try:
        assert not await watch.run(NOW)  # 0 vendas
        assert not await watch.run(NOW + timedelta(hours=1))  # antes de 3 h nem consulta
        assert await watch.run(NOW + timedelta(hours=3))  # primeira venda
        assert not await watch.run(NOW + timedelta(hours=6))  # continua 1: sem e-mail repetido
        assert await watch.run(NOW + timedelta(hours=9))  # 3: venda nova
    finally:
        await client.aclose()
        await http.aclose()
    assert len(calls) == 4
    assert calls[0].headers["Cookie"] == "sessao-de-teste"
    assert "filter_time_range" in calls[0].url.params
    assert "primeira venda" in mails[0]["subject"] and "nova venda" in mails[1]["subject"]
    assert mails[0]["to"] == "dono@example.com"
    async with sessions() as session:
        rows = (await session.execute(select(SalesCheck).order_by(SalesCheck.id))).scalars().all()
    assert [r.total_results for r in rows] == [0, 1, 1, 3]
    # Só os nomes dos campos da venda, nenhum valor (nem o do comprador).
    assert json.loads(rows[1].item_keys or "[]") == ["buyer", "order_id", "tag"]
    assert "mpofamilia" not in (rows[1].item_keys or "")


async def test_blocked_portal_is_recorded_without_mail(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    session_file = tmp_path / "ml-session"
    session_file.write_text(FAKE_SESSION, encoding="utf-8")
    cfg = settings_with(session_file)
    page = httpx.MockTransport(
        lambda r: httpx.Response(
            200, text="<html>verificação</html>", headers={"content-type": "text/html"}
        )
    )
    client = AffiliatePortal(cfg, transport=page)
    mails: list[httpx.Request] = []
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: mails.append(r) or httpx.Response(200))
    )
    try:
        assert not await SalesWatch(sessions, client, cfg, http).run(NOW)
    finally:
        await client.aclose()
        await http.aclose()
    async with sessions() as session:
        row = (await session.execute(select(SalesCheck))).scalar_one()
    assert (row.status, row.total_results) == ("blocked", None) and not mails
