"""Gravar antes de responder (15/10/2026): o FastAPI responde antes do commit da dependência, e um
commit que falhava chegava ao operador como "feito". Toda rota do painel que grava chama `save`
(ou `db.commit()`) antes de responder; a falha vira "nada foi salvo"."""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import utcnow
from app.models import AdminUser, Publication
from app.panel.security import hash_password, new_totp_secret
from tests.test_distribution_panel import csrf_of
from tests.test_fila_envio import seed as seed_queue
from tests.test_panel import PASSWORD, login, seed_admin

PANEL = Path(__file__).resolve().parents[1] / "app" / "panel"
JSON = {"Accept": "application/json"}


def _writes_without_saving() -> list[str]:
    missing = []
    for path in sorted(PANEL.glob("*routes*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            posts = any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "post"
                for d in node.decorator_list
            )
            uses_db = any(
                a.annotation is not None and ast.unparse(a.annotation) == "Db"
                for a in node.args.args
            )
            source = ast.unparse(node)
            if posts and uses_db and "save(db)" not in source and "db.commit()" not in source:
                missing.append(f"{path.name}:{node.name}")
    return missing


def test_every_panel_write_saves_before_answering() -> None:
    """Rota nova que grava pela sessão do request e esquece de salvar antes de responder."""
    assert _writes_without_saving() == []


@pytest.fixture
def break_next_commit(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """Arma a próxima gravação para falhar (queda do banco, ou conflito com `conflict`)."""
    original = AsyncSession.commit
    armed: dict[str, Exception | None] = {"error": None}

    async def commit(self: AsyncSession) -> None:
        error, armed["error"] = armed["error"], None
        if error is not None:
            raise error
        await original(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)

    def arm(conflict: bool = False) -> None:
        armed["error"] = (
            IntegrityError("INSERT", {}, Exception("Duplicate entry"))
            if conflict
            else OperationalError("COMMIT", {}, Exception("Lost connection to server"))
        )

    return arm


async def _state(sessions: async_sessionmaker[AsyncSession], pub_id: int) -> str:
    async with sessions() as s:
        return (
            await s.execute(select(Publication.state).where(Publication.id == pub_id))
        ).scalar_one()


async def test_discard_that_did_not_save_says_so_and_the_post_stays(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    break_next_commit: Callable[[bool], None],
) -> None:
    """O ✕ do Postar dizia "removido" e o post continuava na fila (e podia sair no grupo)."""
    secret = await seed_admin(sessions)
    fryer, pan, _towel = await seed_queue(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/postar")).text)

    break_next_commit(False)
    r = await client.post(f"/postar/{fryer}/descartar", data={"csrf": csrf})
    assert r.status_code == 503
    assert "Nada foi salvo" in r.text and "O banco não respondeu" in r.text
    assert 'href="/postar">Voltar' in r.text
    assert await _state(sessions, fryer) == "ready"

    break_next_commit(False)
    as_json = await client.post(f"/postar/{fryer}/descartar", data={"csrf": csrf}, headers=JSON)
    assert as_json.status_code == 503 and as_json.json()["ok"] is False

    break_next_commit(True)
    clash = await client.post(f"/postar/{pan}/descartar", data={"csrf": csrf})
    assert clash.status_code == 409 and "Outra pessoa mexeu nisso" in clash.text

    ok = await client.post(f"/postar/{fryer}/descartar", data={"csrf": csrf})
    assert ok.status_code == 303 and "aviso=removido" in ok.headers["location"]
    assert await _state(sessions, fryer) == "discarded"


async def test_deactivated_operator_that_did_not_save_is_reported(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    break_next_commit: Callable[[bool], None],
) -> None:
    """Desativar dizia "feito" e a pessoa continuava com acesso."""
    secret = await seed_admin(sessions)
    async with sessions() as s, s.begin():
        other = AdminUser(
            username="danilo.correa",
            password_hash=hash_password(PASSWORD),
            totp_secret=new_totp_secret(),
            totp_last_step=0,
            active=True,
            created_at=utcnow(),
        )
        s.add(other)
        await s.flush()
        other_id = other.id
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/operadores")).text)

    break_next_commit(False)
    r = await client.post(f"/operadores/{other_id}/desativar", data={"csrf": csrf})
    assert r.status_code == 503 and 'href="/operadores">Voltar' in r.text
    async with sessions() as s:
        assert (await s.get(AdminUser, other_id)).active is True  # type: ignore[union-attr]

    ok = await client.post(f"/operadores/{other_id}/desativar", data={"csrf": csrf})
    assert ok.status_code == 303
    async with sessions() as s:
        assert (await s.get(AdminUser, other_id)).active is False  # type: ignore[union-attr]


async def test_logout_that_did_not_save_keeps_saying_so(
    client: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
    break_next_commit: Callable[[bool], None],
) -> None:
    """Sair que não grava deixava a sessão valendo sem o operador saber."""
    secret = await seed_admin(sessions)
    assert (await login(client, secret)).status_code == 303
    csrf = csrf_of((await client.get("/")).text)

    break_next_commit(False)
    r = await client.post("/logout", data={"csrf": csrf})
    assert r.status_code == 503 and "Nada foi salvo" in r.text
    assert (await client.get("/")).status_code == 200  # a sessão segue valendo, e ele sabe

    ok = await client.post("/logout", data={"csrf": csrf})
    assert ok.status_code == 303 and ok.headers["location"] == "/login"
    assert (await client.get("/")).headers["location"] == "/login"
