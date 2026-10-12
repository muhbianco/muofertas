from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_niches
from app.domain.scoring import required_kind
from app.evaluation.service import NicheRules
from app.models import Niche
from tests.test_cli_seed import _niche, _niche_of, _product


def _rules(**kw: object) -> NicheRules:
    base: dict[str, object] = {
        "commission_rate": 0.12,
        "require_official": True,
        "high_risk_brands": frozenset({"lego"}),
    }
    base.update(kw)
    return NicheRules(**base)  # type: ignore[arg-type]


def kind_of(rules: NicheRules, brand: str | None, domain: str | None) -> str:
    official, trusted = rules.seller_rule(brand, domain)
    return required_kind(require_official=official, high_risk_brand=trusted)


def test_official_niche_accepts_gold_platinum_except_listed_types() -> None:
    tech = _rules(accept_trusted=True, official_only_domains=frozenset({"MLB-CELLPHONES"}))
    assert kind_of(tech, "Redfin", "MLB-GAMEPADS_AND_JOYSTICKS") == "confiavel"
    assert kind_of(tech, "Samsung", "MLB-CELLPHONES") == "oficial"
    # Sem a opção, o nicho continua só loja oficial; nicho sem exigência só pede confiável na
    # marca de risco.
    assert kind_of(_rules(), "Redfin", "MLB-GAMEPADS_AND_JOYSTICKS") == "oficial"
    tools = _rules(require_official=False)
    assert kind_of(tools, "LEGO", None) == "confiavel"
    assert kind_of(tools, "Qualquer", None) == "elegivel"


async def test_seed_saves_the_rule_and_reevaluates_only_niches_that_changed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tech = _niche("tecnologia", ["MLB1"])
    tech["exigir_loja_oficial"] = True
    tools = _niche("ferramentas", ["MLB2"])
    await seed_niches({"nichos": [tech, tools]}, sessions=sessions)
    await _product(sessions, "MLBPAD", "tecnologia", "MLB1")
    await _product(sessions, "MLBFURA", "ferramentas", "MLB2")

    tech["aceitar_mercadolider"] = True
    tech["so_loja_oficial_tipos"] = ["MLB-CELLPHONES"]
    await seed_niches({"nichos": [tech, tools]}, sessions=sessions)

    async with sessions() as s:
        niche = (await s.execute(select(Niche).where(Niche.slug == "tecnologia"))).scalar_one()
    rules = NicheRules.from_model(niche)
    assert rules.accept_trusted and rules.official_only_domains == frozenset({"MLB-CELLPHONES"})
    assert await _niche_of(sessions, "MLBPAD") == ("tecnologia", True)  # reavaliar agora
    assert await _niche_of(sessions, "MLBFURA") == ("ferramentas", False)  # nada mudou lá
