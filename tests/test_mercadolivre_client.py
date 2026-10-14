from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.integrations import mercadolivre as ml
from app.integrations.mercadolivre import (
    CallBudgetExceededError,
    ForbiddenError,
    MercadoLivreClient,
    RateLimitedError,
)
from tests.fake_ml import FakeML


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ml, "_backoff", lambda attempt: 0.0)


async def test_token_retries_transient_idp_error(settings: Settings) -> None:
    fake = FakeML(
        highlights={"MLB1": [{"id": "MLB10", "type": "PRODUCT", "position": 1}]}, token_failures=2
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        assert await client.highlights("MLB1") == [
            {"id": "MLB10", "type": "PRODUCT", "position": 1}
        ]
    finally:
        await client.aclose()
    assert fake.calls.count("/oauth/token") == 3


async def test_token_is_cached_between_calls(settings: Settings) -> None:
    fake = FakeML(highlights={"MLB1": []})
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        await client.highlights("MLB1")
        await client.highlights("MLB1")
    finally:
        await client.aclose()
    assert fake.calls.count("/oauth/token") == 1


async def test_429_stops_without_retry(settings: Settings) -> None:
    fake = FakeML(
        override=lambda r: httpx.Response(429) if r.url.path.startswith("/highlights") else None
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        with pytest.raises(RateLimitedError):
            await client.highlights("MLB1")
    finally:
        await client.aclose()
    assert sum(p.startswith("/highlights") for p in fake.calls) == 1


async def test_403_policy_is_forbidden(settings: Settings) -> None:
    fake = FakeML()
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        with pytest.raises(ForbiddenError):
            await client.get("/items/MLB1")
    finally:
        await client.aclose()


async def test_5xx_is_retried_then_succeeds(settings: Settings) -> None:
    state = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.startswith("/highlights") and state["n"] < 2:
            state["n"] += 1
            return httpx.Response(503)
        return None

    fake = FakeML(highlights={"MLB1": []}, override=flaky)
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        assert await client.highlights("MLB1") == []
    finally:
        await client.aclose()


async def test_partial_content_206_is_retried_then_succeeds(settings: Settings) -> None:
    # O ML às vezes responde 206 em /products/{id}/items; a mesma chamada volta 200 em seguida.
    state = {"n": 0}

    def partial(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.endswith("/items") and state["n"] < 1:
            state["n"] += 1
            return httpx.Response(206, json={"results": [], "paging": {"total": 0}})
        return None

    fake = FakeML(offers={"MLB9": [{"item_id": "MLB1", "price": 10.0}]}, override=partial)
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        assert [o["item_id"] for o in await client.product_items("MLB9")] == ["MLB1"]
    finally:
        await client.aclose()
    assert fake.calls.count("/products/MLB9/items") == 2


async def test_product_items_paginates_with_cap(settings: Settings) -> None:
    offers = [{"item_id": f"MLB{i}", "price": 10.0 + i} for i in range(250)]
    fake = FakeML(offers={"MLB9": offers})
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        got = await client.product_items("MLB9")
    finally:
        await client.aclose()
    assert len(got) == 250
    assert sum(p == "/products/MLB9/items" for p in fake.calls) == 3


async def test_hourly_cap_is_enforced(settings: Settings) -> None:
    capped = settings.model_copy(update={"ml_hourly_call_cap": 2})
    fake = FakeML(highlights={"MLB1": []})
    client = MercadoLivreClient(capped, transport=fake.transport())
    try:
        await client.highlights("MLB1")
        await client.highlights("MLB1")
        with pytest.raises(CallBudgetExceededError):
            await client.highlights("MLB1")
    finally:
        await client.aclose()


async def test_error_keeps_what_the_ml_answered(settings: Settings) -> None:
    """14/10/2026: o 400 ficou sem explicação porque o erro guardava só "http 400"."""
    import httpx

    fake = FakeML()
    fake.override = lambda r: (
        httpx.Response(400, text='{"message": "invalid limit"}')
        if r.url.path.endswith("/items")
        else None
    )
    client = MercadoLivreClient(settings, transport=fake.transport())
    try:
        with pytest.raises(ml.MercadoLivreError) as caught:
            await client.product_items("MLBP1")
    finally:
        await client.aclose()
    assert str(caught.value) == 'http 400: {"message": "invalid limit"}'
