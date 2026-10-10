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


def test_high_risk_brand_requires_official_store() -> None:
    ev = evaluate(base(high_risk_brand=True, snapshot=snap(15000, official=None)))
    assert ev.tier is Tier.BLOQUEADA
    assert ev.gates_failed == ["G7_sem_loja_oficial"]


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
    assert data["ruleset"].startswith("v1")
    assert set(data["components"]) == {"F", "D", "Q", "U", "R"}
