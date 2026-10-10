from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import check_once
from app.core.config import Settings
from app.domain.alerts import HealthFacts, firing, mail_for
from app.models import Alert, CatalogProduct, CollectionRun, WorkerLease

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)


def facts(**kw: object) -> HealthFacts:
    base: dict[str, object] = {
        "now": NOW,
        "lease_expires_at": NOW + timedelta(seconds=30),
        "last_refresh_ok_at": NOW - timedelta(minutes=5),
        "errors_last_hour": 0,
        "products_active": 300,
        "products_overdue": 0,
    }
    base.update(kw)
    return HealthFacts(**base)  # type: ignore[arg-type]


def test_healthy_collector_fires_nothing() -> None:
    assert firing(facts()) == {}


def test_each_rule() -> None:
    assert "worker_parado" in firing(facts(lease_expires_at=NOW - timedelta(minutes=11)))
    assert "worker_parado" not in firing(facts(lease_expires_at=NOW - timedelta(minutes=9)))
    stale = firing(facts(last_refresh_ok_at=NOW - timedelta(hours=7)))
    assert stale["coleta_parada"] == "Última atualização de preço concluída: 21/10 08:00."
    assert "coleta_parada" not in firing(facts(last_refresh_ok_at=None, products_active=0))
    assert "erros_ml" in firing(facts(errors_last_hour=20))
    assert "fila_atrasada" not in firing(facts(products_overdue=29))
    assert "fila_atrasada" in firing(facts(products_overdue=30))


def test_mail_texts() -> None:
    mail = mail_for(
        "coleta_parada", "msg", event="lembrete", opened_at=NOW, panel_url="https://x/saude"
    )
    assert mail.subject == "[MegaPromoOz] Ainda: Coleta de preços parada"
    assert "msg" in mail.body and "https://x/saude" in mail.body
    done = mail_for("coleta_parada", "msg", event="resolvido", opened_at=NOW, panel_url="u")
    assert done.subject.startswith("[MegaPromoOz] Resolvido:")


def settings_with_mail() -> Settings:
    return Settings(
        database_url_override="sqlite+aiosqlite://",
        alert_email_webhook_url="https://n8n.test/webhook/megapromooz-email",
        alert_email_webhook_token=SecretStr("hook-token"),
        alert_email_to="dono@example.com",
    )


async def seed_healthy(sessions: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with sessions() as session, session.begin():
        session.add(
            WorkerLease(name="collector", holder="w", expires_at=now + timedelta(seconds=60))
        )
        session.add(
            CollectionRun(
                source="ml_refresh",
                started_at=now - timedelta(minutes=6),
                finished_at=now - timedelta(minutes=5),
                status="ok",
            )
        )
        session.add(
            CatalogProduct(
                marketplace="mercadolivre",
                external_id="MLBP1",
                discovered_via="highlights",
                active=True,
                tier="warm",
                next_check_at=now + timedelta(hours=1),
                consecutive_failures=0,
                first_seen_at=now,
            )
        )


async def test_alert_opens_mails_once_reminds_and_resolves(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    cfg = settings_with_mail()
    now = NOW
    await seed_healthy(sessions, now)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await check_once(sessions, http, cfg, "web-1", now) == 0

        # o worker morre: abre e manda um e-mail; o ciclo seguinte não repete
        later = now + timedelta(minutes=15)
        assert await check_once(sessions, http, cfg, "web-1", later) == 1
        assert await check_once(sessions, http, cfg, "web-1", later + timedelta(minutes=5)) == 0
        body = json.loads(sent[0].content)
        assert body["app"] == "megapromooz" and body["to"] == "dono@example.com"
        assert body["subject"] == "[MegaPromoOz] Worker do coletor parado"
        assert sent[0].headers["authorization"] == "hook-token"

        # 6 h depois, ainda parado: lembrete
        assert await check_once(sessions, http, cfg, "web-1", later + timedelta(hours=6)) >= 1
        assert json.loads(sent[1].content)["subject"].startswith("[MegaPromoOz] Ainda:")

        # volta ao normal: fecha e avisa uma vez
        back = later + timedelta(hours=7)
        async with sessions() as session, session.begin():
            lease = await session.get(WorkerLease, "collector")
            assert lease is not None
            lease.expires_at = back + timedelta(seconds=60)
            session.add(
                CollectionRun(
                    source="ml_refresh",
                    started_at=back - timedelta(minutes=2),
                    finished_at=back - timedelta(minutes=1),
                    status="ok",
                )
            )
            product = (await session.execute(select(CatalogProduct))).scalar_one()
            product.next_check_at = back + timedelta(hours=1)
        await check_once(sessions, http, cfg, "web-1", back)
        await check_once(sessions, http, cfg, "web-1", back + timedelta(minutes=5))
    subjects = [json.loads(r.content)["subject"] for r in sent]
    assert subjects.count("[MegaPromoOz] Resolvido: Worker do coletor parado") == 1
    async with sessions() as session:
        states = {a.kind: a.state for a in (await session.execute(select(Alert))).scalars()}
    assert states["worker_parado"] == "resolved"


async def test_without_webhook_alert_stays_in_panel_and_mails_later(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    sent: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    ) as http:
        quiet = Settings(database_url_override="sqlite+aiosqlite://")
        assert await check_once(sessions, http, quiet, "web-1", NOW) == 0  # sem lease: parado
        async with sessions() as session:
            alert = (await session.execute(select(Alert))).scalar_one()
        assert alert.state == "open" and alert.notified_at is None and not sent
        # configurado depois: o alerta pendente sai no próximo ciclo
        assert await check_once(sessions, http, settings_with_mail(), "web-1", NOW) == 1


async def test_webhook_failure_retries_next_cycle(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] == 1 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as http:
        cfg = settings_with_mail()
        assert await check_once(sessions, http, cfg, "web-1", NOW) == 0
        assert await check_once(sessions, http, cfg, "web-1", NOW + timedelta(minutes=5)) == 1


async def test_panel_shows_open_alerts(
    client: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from tests.test_panel import login, seed_admin

    secret = await seed_admin(sessions)
    async with sessions() as session, session.begin():
        session.add(
            Alert(
                kind="coleta_parada",
                state="open",
                message="Última atualização de preço concluída: 21/10 08:00.",
                opened_at=NOW,
                last_seen_at=NOW,
            )
        )
    assert (await login(client, secret)).status_code == 303
    assert "1 alerta aberto" in (await client.get("/")).text
    assert "Última atualização de preço concluída" in (await client.get("/saude")).text
