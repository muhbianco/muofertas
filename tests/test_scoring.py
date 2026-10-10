from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.catalog_offers import CatalogSnapshot
from app.domain.price_history import PriceStats
from app.domain.scoring import ScoringInput, Tier, evaluate

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def snap(min_price: int, official: int | None = None) -> CatalogSnapshot:
    return CatalogSnapshot(
        min_price_cents=min_price,
        min_official_price_cents=official,
        offers_count=10,
        official_offers_count=1 if official else 0,
        deal_offers_count=0,
        free_shipping_offers_count=5,
        min_offer_item_id="MLB1",
        min_offer_seller_id=1,
        min_offer_original_price_cents=None,
        min_offer_free_shipping=True,
        # Nos testes, o menor preço é de vendedor elegível e a loja oficial é a oferta confiável.
        eligible_min_price_cents=min_price,
        eligible_item_id="MLB1",
        eligible_seller_id=1,
        eligible_offers_count=10,
        trusted_min_price_cents=official,
        trusted_item_id="MLB2" if official else None,
        trusted_seller_id=2 if official else None,
        eligible_official=official is not None and official <= min_price,
        trusted_official=official is not None,
        official_item_id="MLB2" if official else None,
        official_seller_id=2 if official else None,
    )


def stats(reference: int | None, min90: int | None = None, inflated: bool = False) -> PriceStats:
    return PriceStats(
        reference_cents=reference,
        coverage_days=20 if reference else 2,
        min_90d_cents=min90,
        median_60d_cents=reference,
        max_before_drop_cents=int(reference * 1.5) if (inflated and reference) else reference,
        first_seen=NOW - timedelta(days=40),
    )


def base(**kw: object) -> ScoringInput:
    inp = ScoringInput(
        title="Parafusadeira Makita TD0100",
        brand="Makita",
        commission_rate=0.12,
        require_official=False,
        high_risk_brand=False,
        snapshot=snap(30000, official=30000),
        stats=stats(45000, min90=30000),
        observed_at=NOW - timedelta(minutes=10),
        last_changed_at=NOW - timedelta(hours=1),
        highlight_position=1,
        niche_commission_p90_cents=3600,
        now=NOW,
    )
    return replace(inp, **kw)  # type: ignore[arg-type]


def test_real_drop_from_official_store_is_exceptional() -> None:
    ev = evaluate(base())
    assert ev.tier is Tier.EXCEPCIONAL
    assert ev.used_official
    assert ev.real_discount is not None and round(ev.real_discount, 2) == 0.33
    assert ev.components["F"] == 100.0  # ~33% + mínima de 90 dias


def test_cold_start_never_goes_above_boa() -> None:
    ev = evaluate(base(stats=stats(None)))
    assert ev.real_discount is None
    assert ev.components["F"] == 0.0
    assert ev.tier in (Tier.BOA, Tier.REGULAR)
    assert "sem histórico suficiente: desconto não comprovado" in ev.notes


def test_inflate_then_drop_is_suspicious() -> None:
    ev = evaluate(base(stats=stats(45000, min90=30000, inflated=True)))
    assert ev.tier is Tier.SUSPEITA
    assert "S1_subiu_para_descer" in ev.signals


def test_copy_title_is_blocked() -> None:
    ev = evaluate(base(title="Tênis Réplica Primeira Linha"))
    assert ev.tier is Tier.BLOQUEADA
    assert "G8_titulo_de_copia" in ev.gates_failed


def test_high_risk_brand_requires_trusted_offer() -> None:
    ev = evaluate(base(high_risk_brand=True, snapshot=snap(15000, official=None)))
    assert ev.tier is Tier.BLOQUEADA
    assert ev.gates_failed == ["G7_sem_oferta_confiavel"]


def test_much_cheaper_unofficial_offer_is_suspicious() -> None:
    # o caso real da serra Makita: R$ 199,54 de vendedor qualquer contra R$ 339 da loja oficial
    ev = evaluate(base(snapshot=snap(19954, official=33900), stats=stats(33900)))
    assert not ev.used_official
    assert "S4_muito_abaixo_da_loja_oficial" in ev.signals
    assert ev.tier is Tier.SUSPEITA


def test_stale_price_is_blocked() -> None:
    ev = evaluate(base(observed_at=NOW - timedelta(hours=7)))
    assert "G1_preco_velho" in ev.gates_failed


def test_snapshot_is_serializable_for_audit() -> None:
    data = evaluate(base()).as_snapshot()
    assert data["tier"] == "excepcional"
    assert data["ruleset"].startswith("v2")
    assert set(data["components"]) == {"F", "D", "Q", "U", "R"}


def galaxy() -> CatalogSnapshot:
    # 10/10/2026: R$ 1.170,40 sem reputação; R$ 1.299 verde/MercadoLíder; R$ 1.309 verde/Gold;
    # loja oficial R$ 1.499.
    return replace(
        snap(117040, official=149900),
        eligible_min_price_cents=129900,
        eligible_item_id="MLB7083641270",
        trusted_min_price_cents=130900,
        trusted_item_id="MLB6000000001",
        trusted_official=False,
    )


def test_galaxy_case_high_risk_brand_uses_trusted_and_notes_ignored_offer() -> None:
    # Samsung é marca de risco: vale a oferta confiável (Gold), e o R$ 1.170,40 vira só nota.
    ev = evaluate(base(high_risk_brand=True, snapshot=galaxy(), stats=stats(None)))
    assert ev.price_cents == 130900 and ev.offer_kind == "confiavel"
    assert ev.offer_item_id == "MLB6000000001"
    assert not ev.used_official
    assert "há oferta 11% mais barata de vendedor não elegível (ignorada)" in ev.notes


def test_galaxy_case_in_a_niche_that_requires_official_store() -> None:
    ev = evaluate(base(require_official=True, snapshot=galaxy(), stats=stats(None)))
    assert ev.price_cents == 149900 and ev.offer_kind == "oficial" and ev.used_official
    assert ev.offer_item_id == "MLB2"
    assert "vendedor elegível 13% mais barato fica de fora pela régua" in ev.notes


def test_niche_that_requires_official_store_blocks_without_one() -> None:
    s = replace(galaxy(), min_official_price_cents=None, official_item_id=None)
    ev = evaluate(base(require_official=True, snapshot=s))
    assert ev.gates_failed == ["G7_sem_loja_oficial"]


def test_no_eligible_seller_is_blocked() -> None:
    s = replace(snap(7000, official=None), eligible_min_price_cents=None)
    ev = evaluate(base(snapshot=s))
    assert ev.tier is Tier.BLOQUEADA and ev.gates_failed == ["G9_sem_vendedor_elegivel"]


def test_official_store_within_two_percent_is_preferred() -> None:
    s = replace(snap(10000, official=10150), eligible_official=False)
    ev = evaluate(base(snapshot=s, stats=stats(None)))
    assert ev.price_cents == 10150 and ev.offer_kind == "oficial" and ev.used_official
    assert ev.offer_item_id == "MLB2"
    s = replace(snap(10000, official=10300), eligible_official=False)
    ev = evaluate(base(snapshot=s, stats=stats(None)))
    assert ev.price_cents == 10000 and ev.offer_kind == "elegivel" and not ev.used_official
