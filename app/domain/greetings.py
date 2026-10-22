"""Boas-vindas a quem entra no grupo (pedido do dono em 22/10/2026): regras puras.

Quem entra junto (alguém compartilhou o link e quatro entraram em seguida) recebe uma saudação só:

- as entradas se juntam numa espera; a saudação sai depois de `quiet` sem ninguém novo, ou
  `max_wait` depois da primeira entrada, se continuar pingando gente;
- no máximo uma a cada `cooldown` por grupo: quem entra nesse meio espera a próxima;
- só dentro da janela do grupo: quem entra de madrugada é saudado na abertura;
- espera mais velha que `EXPIRE` (grupo pausado, WhatsApp fora) não sai mais: "bem-vindo" de
  ontem não é bem-vindo.

O texto diz quando as ofertas chegam, a partir da regra de envio do grupo (tela Grupos), para nunca
prometer horário que o envio não cumpre, e marca quem entrou (`{marcados}`, até `MAX_MENTIONS`).
"""

from __future__ import annotations

import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.distribution import (
    SP,
    ChannelPolicy,
    batch_open_for,
    batch_starts,
    current_batch_start,
    next_batch_start,
)

EXPIRE = timedelta(hours=24)
MAX_TEXT = 1500
BATCH_FIELDS = frozenset({"lote", "horarios", "proximo", "instagram", "marcados"})
STEADY_FIELDS = frozenset({"por_dia", "inicio", "fim", "instagram", "marcados"})
# Leva maior que isso (alguém postou o link numa comunidade) sai sem marcar ninguém: uma parede
# de @ no grupo é pior que a saudação sem nome.
MAX_MENTIONS = 15
# Mais da metade da lista "nova" de uma vez não é gente entrando: é o WhatsApp trocando o formato
# do identificador (telefone ↔ LID). A lista vira a referência e ninguém é saudado.
REBASE_SHARE = 0.5
REBASE_MIN = 20


class GreetingConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GreetingRules:
    poll_every: timedelta = timedelta(minutes=5)
    quiet: timedelta = timedelta(minutes=5)
    max_wait: timedelta = timedelta(minutes=30)
    cooldown: timedelta = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class GreetingConfig:
    batch_text: str
    steady_text: str
    instagram: str
    rules: GreetingRules


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def _check_text(raw: Any, where: str, allowed: frozenset[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        raise GreetingConfigError(f"{where}: texto vazio")
    try:
        unknown = _fields(text) - allowed
    except ValueError as exc:
        raise GreetingConfigError(f"{where}: chave mal fechada ({exc})") from None
    if unknown:
        raise GreetingConfigError(f"{where}: placeholder desconhecido {sorted(unknown)}")
    return text


def _minutes(raw: Any, where: str, default: timedelta, *, low: int, high: int) -> timedelta:
    if raw is None:
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or not low <= raw <= high:
        raise GreetingConfigError(f"{where}: minutos entre {low} e {high}")
    return timedelta(minutes=raw)


def parse_config(data: dict[str, Any]) -> GreetingConfig:
    """`config/boas_vindas.json` → regras. Erra alto: saudação com texto quebrado não sai."""
    instagram = str(data.get("instagram") or "").strip()
    if instagram and not instagram.startswith("https://"):
        raise GreetingConfigError("instagram: link https://")
    base = GreetingRules()
    rules = GreetingRules(
        poll_every=_minutes(
            data.get("conferir_a_cada_minutos"), "conferir", base.poll_every, low=2, high=60
        ),
        quiet=_minutes(data.get("silencio_minutos"), "silencio", base.quiet, low=1, high=60),
        max_wait=_minutes(
            data.get("espera_maxima_minutos"), "espera_maxima", base.max_wait, low=5, high=240
        ),
        cooldown=_minutes(
            data.get("intervalo_minimo_minutos"),
            "intervalo_minimo",
            base.cooldown,
            low=30,
            high=24 * 60,
        ),
    )
    config = GreetingConfig(
        batch_text=_check_text(data.get("texto_lote"), "texto_lote", BATCH_FIELDS),
        steady_text=_check_text(data.get("texto_ritmo"), "texto_ritmo", STEADY_FIELDS),
        instagram=instagram,
        rules=rules,
    )
    for name, text in (("texto_lote", config.batch_text), ("texto_ritmo", config.steady_text)):
        if len(text) > MAX_TEXT:
            raise GreetingConfigError(f"{name}: no máximo {MAX_TEXT} caracteres")
    return config


# --------------------------------------------------------------------------- quem entrou


def new_members(before: frozenset[str] | None, current: frozenset[str]) -> int | None:
    """Quantos entraram desde a última olhada; `None` quando não dá para comparar (primeira
    olhada do processo, lista vazia ou troca de formato) e a lista atual vira a referência."""
    if before is None or not current:
        return None
    fresh = len(current - before)
    if fresh > max(REBASE_MIN, len(current) * REBASE_SHARE):
        return None
    return fresh


# --------------------------------------------------------------------------- quando sai


def greeting_due(
    *,
    first_join_at: datetime,
    last_join_at: datetime,
    last_greeting_at: datetime | None,
    now: datetime,
    window_start_hour: int,
    window_end_hour: int,
    rules: GreetingRules,
) -> bool:
    if not window_start_hour <= now.astimezone(SP).hour < window_end_hour:
        return False
    if last_greeting_at is not None and now - last_greeting_at < rules.cooldown:
        return False
    return now - last_join_at >= rules.quiet or now - first_join_at >= rules.max_wait


def expired(first_join_at: datetime, now: datetime) -> bool:
    return now - first_join_at > EXPIRE


# --------------------------------------------------------------------------- texto


def _hour(moment: datetime) -> str:
    """ "7h", ou "7:30" (como na descrição do grupo; o painel usa "07h")."""
    local = moment.astimezone(SP)
    return f"{local.hour}h" if local.minute == 0 else f"{local.hour}:{local:%M}"


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} e {labels[-1]}"


def next_batch_sentence(now: datetime, policy: ChannelPolicy) -> str:
    """ "O lote das 10h está saindo agora." / "O próximo lote sai às 13h." / "... amanhã às 7h."."""
    current = current_batch_start(now, policy)
    if current is not None and now < current + batch_open_for(policy):
        return f"O lote das {_hour(current)} está saindo agora."
    upcoming = next_batch_start(now, policy)
    if upcoming is not None:
        return f"O próximo lote sai às {_hour(upcoming)}."
    assert policy.batch is not None
    tomorrow = batch_starts(
        now + timedelta(days=1),
        policy.window_start_hour,
        policy.window_end_hour,
        policy.batch.every,
    )
    if not tomorrow:
        return ""
    return f"O próximo lote sai amanhã às {_hour(tomorrow[0])}."


def handle(jid: str) -> str | None:
    """O `@` do texto: a parte do JID antes do `@`, sem o aparelho (`5511…:12@…`). Só dígitos
    (telefone ou LID); outra coisa não se marca."""
    user = jid.split("@", 1)[0].split(":", 1)[0]
    return user if user.isdigit() else None


def mentions_for(jids: Sequence[str]) -> list[tuple[str, str]]:
    """(JID, @) de quem será marcado: nenhum quando a leva passa de `MAX_MENTIONS`."""
    if len(jids) > MAX_MENTIONS:
        return []
    pairs = [(jid, handle(jid)) for jid in jids]
    return [(jid, h) for jid, h in pairs if h is not None]


def render_greeting(
    config: GreetingConfig, policy: ChannelPolicy, now: datetime, handles: Sequence[str] = ()
) -> str:
    """`handles`: os `@` de quem entrou (sem a arroba), no lugar de `{marcados}`."""
    marked = "".join(f" @{h}" for h in handles)
    if policy.batch is not None:
        starts = batch_starts(
            now, policy.window_start_hour, policy.window_end_hour, policy.batch.every
        )
        text = config.batch_text.format(
            lote=policy.batch.size,
            horarios=_join([_hour(s) for s in starts]),
            proximo=next_batch_sentence(now, policy),
            instagram=config.instagram,
            marcados=marked,
        )
    else:
        text = config.steady_text.format(
            por_dia=policy.max_per_day,
            inicio=f"{policy.window_start_hour}h",
            fim=f"{policy.window_end_hour}h",
            instagram=config.instagram,
            marcados=marked,
        )
    # Placeholder vazio (sem Instagram, sem lote amanhã) não deixa linha pendurada.
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()
