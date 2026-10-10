"""Hora sempre em UTC com fuso explícito.

A casa já perdeu tempo com "+3h na tela" (ISO sem offset lido como local). Regra: o código só
trabalha com datetime *aware* em UTC; o banco guarda UTC sem fuso (MariaDB DATETIME), e o tipo
`UTCDateTime` dos modelos converte na fronteira.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
