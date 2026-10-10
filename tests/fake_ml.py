"""Mercado Livre falso para os testes (httpx.MockTransport), com script de respostas."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FakeML:
    highlights: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    offers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    token_failures: int = 0
    calls: list[str] = field(default_factory=list)
    override: Callable[[httpx.Request], httpx.Response | None] | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if self.override is not None:
            forced = self.override(request)
            if forced is not None:
                return forced
        if path == "/oauth/token":
            if self.token_failures > 0:
                self.token_failures -= 1
                return httpx.Response(400, json={"error": "error_role_idp"})
            return httpx.Response(200, json={"access_token": "APP_USR-test", "expires_in": 21600})
        if request.headers.get("Authorization") != "Bearer APP_USR-test":
            return httpx.Response(401, json={"error": "unauthorized"})
        parts = path.strip("/").split("/")
        if parts[:3] == ["highlights", "MLB", "category"]:
            content = self.highlights.get(parts[3])
            if content is None:
                return httpx.Response(404, json={"error": "not_found"})
            return httpx.Response(200, json={"content": content})
        if parts[0] == "products" and len(parts) == 2:
            product = self.products.get(parts[1])
            return (
                httpx.Response(200, json=product)
                if product
                else httpx.Response(404, json={"error": "not_found"})
            )
        if parts[0] == "products" and len(parts) == 3 and parts[2] == "items":
            offers = self.offers.get(parts[1])
            if offers is None:
                return httpx.Response(404, json={"error": "not_found"})
            offset = int(request.url.params.get("offset", "0"))
            limit = int(request.url.params.get("limit", "100"))
            page = offers[offset : offset + limit]
            return httpx.Response(
                200, content=json.dumps({"results": page, "paging": {"total": len(offers)}})
            )
        return httpx.Response(403, json={"error": "forbidden"})
