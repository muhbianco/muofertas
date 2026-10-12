"""Comandos de operação.

python -m app.cli seed-niches config/nichos.json   # cria/atualiza nichos e categorias
python -m app.cli run-once                          # uma descoberta + um lote de preços
python -m app.cli stats                             # contagens do coletor
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, update

from app.collector.domains import (
    CONFIG_ACTOR,
    config_rules,
    domain_rules,
    follow_domain_rules,
    seed_rules,
)
from app.collector.marketplace import MARKETPLACE
from app.collector.service import Collector
from app.core.clock import utcnow
from app.core.config import settings
from app.core.database import SessionFactory, dispose_engine
from app.core.logging import configure_logging
from app.integrations.mercadolivre import MercadoLivreClient
from app.models import CatalogProduct, Niche, NicheCategory, PriceObservation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def seed_niches(
    data: dict[str, Any], sessions: async_sessionmaker[AsyncSession] = SessionFactory
) -> None:
    now = utcnow()
    domain_owners = config_rules(data)  # valida antes de gravar qualquer coisa
    niche_ids: dict[str, int] = {}
    changed_rules: list[int] = []
    async with sessions() as session, session.begin():
        for item in data["nichos"]:
            niche = (
                await session.execute(select(Niche).where(Niche.slug == item["slug"]))
            ).scalar_one_or_none()
            if niche is None:
                niche = Niche(
                    slug=item["slug"],
                    name=item["nome"],
                    active=item.get("ativo", True),
                    created_at=now,
                )
                session.add(niche)
                await session.flush()
            else:
                niche.name = item["nome"]
                niche.active = item.get("ativo", True)
            before = _rule_fields(niche)
            niche.commission_rate_bp = int(item.get("comissao_bp", 1200))
            niche.require_official = bool(item.get("exigir_loja_oficial", False))
            niche.high_risk_brands = json.dumps(
                item.get("marcas_alto_risco", []), ensure_ascii=False
            )
            niche.accept_trusted = bool(item.get("aceitar_mercadolider", False))
            niche.official_only_domains = json.dumps(
                sorted(item.get("so_loja_oficial_tipos", [])), ensure_ascii=False
            )
            if niche.id is not None and before != _rule_fields(niche):
                changed_rules.append(niche.id)
            wanted = {c["id"]: c["nome"] for c in item["categorias"]}
            current = {
                c.external_category_id: c
                for c in (
                    await session.execute(
                        select(NicheCategory).where(
                            NicheCategory.niche_id == niche.id,
                            NicheCategory.marketplace == MARKETPLACE,
                        )
                    )
                ).scalars()
            }
            for ext_id, name in wanted.items():
                if ext_id in current:
                    current[ext_id].name = name
                    current[ext_id].active = True
                else:
                    session.add(
                        NicheCategory(
                            niche_id=niche.id,
                            marketplace=MARKETPLACE,
                            external_category_id=ext_id,
                            name=name,
                            active=True,
                        )
                    )
            for ext_id, category in current.items():
                if ext_id not in wanted:
                    category.active = False
            niche_ids[niche.slug] = niche.id
            print(f"{item['slug']}: {len(wanted)} categorias")
        await session.flush()
        kept = await seed_rules(
            session, {d: niche_ids[slug] for d, slug in domain_owners.items()}, now=now
        )
        await session.flush()
        print(f"tipos de produto com nicho fixo no nichos.json: {len(domain_owners)}")
        for domain_id in kept:
            print(f"  {domain_id}: vale a regra do painel (mais recente), não a do JSON")
        ruled = set(await domain_rules(session))
        moved = await _follow_moved_categories(session, skip_domains=ruled)
        print(f"produtos que mudaram de nicho junto com a categoria: {moved}")
        by_type = await follow_domain_rules(session, actor=CONFIG_ACTOR)
        print(f"produtos que mudaram de nicho pelo tipo: {by_type}")
        if changed_rules:
            # Regra da nota mudou (comissão, régua de vendedor): reavalia o nicho agora, não
            # na reavaliação diária.
            in_changed = (
                CatalogProduct.niche_id.in_(changed_rules),
                CatalogProduct.active.is_(True),
            )
            to_review = (
                await session.execute(select(func.count()).where(*in_changed))
            ).scalar_one()
            await session.execute(
                update(CatalogProduct).where(*in_changed).values(needs_evaluation=True)
            )
            print(
                f"nichos com regra nova: {len(changed_rules)}; produtos para reavaliar: {to_review}"
            )


def _rule_fields(niche: Niche) -> tuple[object, ...]:
    return (
        niche.commission_rate_bp,
        niche.require_official,
        niche.high_risk_brands,
        niche.accept_trusted,
        niche.official_only_domains,
    )


async def _follow_moved_categories(
    session: AsyncSession, *, skip_domains: set[str] | None = None
) -> int:
    """Categoria que trocou de nicho leva os produtos dela, que voltam para a fila de avaliação.

    Só quando a categoria está ativa em exatamente um nicho (sem ambiguidade). O produto guarda a
    categoria pela qual foi descoberto, então a troca nunca pega produto de outra categoria. O
    produto cujo nicho o operador escolheu (`niche_locked`) fica onde ele pôs, e o de tipo com
    nicho fixo (`skip_domains`) segue o tipo.
    """
    owners: dict[str, set[int]] = defaultdict(set)
    rows = await session.execute(
        select(NicheCategory.external_category_id, NicheCategory.niche_id).where(
            NicheCategory.active.is_(True), NicheCategory.marketplace == MARKETPLACE
        )
    )
    for category_id, niche_id in rows.tuples():
        owners[category_id].add(niche_id)
    moved = 0
    for category_id, niche_ids in owners.items():
        if len(niche_ids) != 1:
            continue
        (target,) = niche_ids
        ids = list(
            (
                await session.execute(
                    select(CatalogProduct.id).where(
                        CatalogProduct.marketplace == MARKETPLACE,
                        CatalogProduct.category_id == category_id,
                        CatalogProduct.niche_locked.is_(False),
                        or_(CatalogProduct.niche_id.is_(None), CatalogProduct.niche_id != target),
                        or_(
                            CatalogProduct.domain_id.is_(None),
                            CatalogProduct.domain_id.not_in(list(skip_domains or ())),
                        ),
                    )
                )
            ).scalars()
        )
        if ids:
            await session.execute(
                update(CatalogProduct)
                .where(CatalogProduct.id.in_(ids))
                .values(niche_id=target, needs_evaluation=True)
            )
        moved += len(ids)
    return moved


async def run_once() -> None:
    client = MercadoLivreClient(settings)
    try:
        collector = Collector(SessionFactory, client, settings)
        new = await collector.discover_due()
        stats = await collector.refresh_due()
        print(
            f"descoberta: {new} produtos novos · lote: {stats.seen} vistos, "
            f"{stats.changed} mudaram, "
            f"{stats.errors} erros · {client.calls} chamadas"
        )
    finally:
        await client.aclose()


async def stats() -> None:
    async with SessionFactory() as session:
        products = (
            (
                await session.execute(
                    select(CatalogProduct.tier, func.count())
                    .where(CatalogProduct.active.is_(True))
                    .group_by(CatalogProduct.tier)
                )
            )
            .tuples()
            .all()
        )
        observations = (
            await session.execute(select(func.count()).select_from(PriceObservation))
        ).scalar_one()
    print("produtos ativos por camada:", dict(products))
    print("observações de preço:", observations)


def _prompt_admin() -> tuple[str, str, str]:
    """Pergunta tudo antes do loop assíncrono. Senha e segredo só aparecem neste terminal."""
    import getpass
    import sys

    from app.panel.security import new_totp_secret, otpauth_uri, verify_totp

    if not sys.stdin.isatty():
        raise SystemExit("rode num terminal interativo: senha e código são digitados")
    username = input("usuário do painel: ").strip()
    if not username or len(username) > 40:
        raise SystemExit("usuário inválido")
    password = getpass.getpass("senha (mínimo 12 caracteres): ")
    if len(password) < 12 or password != getpass.getpass("repita a senha: "):
        raise SystemExit("senha curta ou diferente; nada foi gravado")
    secret = new_totp_secret()
    grouped = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
    print()
    print("No Google Authenticator: + > Inserir chave de configuração")
    print(f"  conta: MegaPromoOz ({username})")
    print(f"  chave: {grouped}   (tipo: baseada em tempo)")
    print(f"  ou, num leitor de QR: {otpauth_uri(secret, username)}")
    print()
    code = input("código de 6 dígitos que o app mostra agora: ")
    if verify_totp(secret, code, last_step=0) is None:
        raise SystemExit("código não confere (relógio do celular certo?); nada foi gravado")
    return username, password, secret


async def save_admin(username: str, password: str, secret: str) -> None:
    from app.models import AdminSession, AdminUser
    from app.panel.security import hash_password

    async with SessionFactory() as session, session.begin():
        user = (
            await session.execute(select(AdminUser).where(AdminUser.username == username))
        ).scalar_one_or_none()
        if user is None:
            user = AdminUser(
                username=username,
                password_hash=hash_password(password),
                totp_secret=secret,
                totp_last_step=0,
                active=True,
                created_at=utcnow(),
            )
            session.add(user)
            action = "criado"
        else:
            user.password_hash = hash_password(password)
            user.totp_secret = secret
            user.totp_last_step = 0
            user.active = True
            await session.flush()
            await session.execute(delete(AdminSession).where(AdminSession.user_id == user.id))
            action = "atualizado (sessões antigas encerradas)"
    print(f"operador {username} {action}.")


def main() -> None:
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed-niches")
    seed.add_argument("path", type=Path)
    sub.add_parser("run-once")
    sub.add_parser("stats")
    sub.add_parser("create-admin", help="cria ou redefine o operador do painel (interativo)")
    args = parser.parse_args()
    # Arquivo e perguntas antes do loop assíncrono (sem I/O bloqueante dentro dele).
    seed_data = (
        json.loads(args.path.read_text(encoding="utf-8")) if args.command == "seed-niches" else {}
    )
    admin = _prompt_admin() if args.command == "create-admin" else None

    async def runner() -> None:
        try:
            if args.command == "seed-niches":
                await seed_niches(seed_data)
            elif args.command == "run-once":
                await run_once()
            elif admin is not None:
                await save_admin(*admin)
            else:
                await stats()
        finally:
            await dispose_engine()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
