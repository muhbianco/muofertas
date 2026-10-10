"""Foto do produto para o post do grupo: só do CDN de imagens do ML, só JPEG/PNG, até 3 MB.

A URL vem da API oficial (`pictures` do produto de catálogo), mas a lista de hosts é fechada de
qualquer forma (SSRF): nada fora de `http2.mlstatic.com` é baixado.
"""

from __future__ import annotations

import base64
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)
IMAGE_HOSTS = frozenset({"http2.mlstatic.com"})
IMAGE_TYPES = frozenset({"image/jpeg", "image/png"})
MAX_IMAGE_BYTES = 3 * 1024 * 1024


async def fetch_product_image(http: httpx.AsyncClient, url: str | None) -> str | None:
    """Data URI da foto, ou None (o post sai só em texto)."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in IMAGE_HOSTS:
        return None
    try:
        response = await http.get(url, follow_redirects=False)
    except httpx.HTTPError:
        logger.warning("foto do produto não baixou", extra={"host": parts.hostname})
        return None
    kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body = response.content
    if response.status_code != 200 or kind not in IMAGE_TYPES or not body:
        return None
    if len(body) > MAX_IMAGE_BYTES:
        return None
    return f"data:{kind};base64,{base64.b64encode(body).decode()}"
