"""Alertas críticos da operação. Puro, sem I/O.

O histórico de preço é o ativo do projeto e só cresce com o coletor rodando: um dia parado é um
buraco que não volta. Por isso o que importa avisar é a coleta parada, não cada erro isolado.

- `worker_parado`: ninguém renova o lease do coletor há mais de 10 minutos.
- `coleta_parada`: nenhuma atualização de preço concluída em 6 horas.
- `erros_ml`: 20 ou mais erros de integração na última hora.
- `fila_atrasada`: produtos vencidos há mais de 1 hora (o coletor não dá conta).
- `whatsapp_desconectado`: o número já pareado saiu da sessão (logout, banimento, 14 dias sem o
  celular). A WuzAPI desiste de reconectar sozinha; sem aviso, os grupos ficam mudos.

Um alerta abre uma vez, é lembrado a cada 6 horas enquanto continuar valendo e fecha sozinho.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SP = ZoneInfo("America/Sao_Paulo")
LEASE_GRACE = timedelta(minutes=10)
REFRESH_STALE = timedelta(hours=6)
ERRORS_PER_HOUR = 20
OVERDUE_AFTER = timedelta(hours=1)
REMIND_EVERY = timedelta(hours=6)

TITLES = {
    "worker_parado": "Worker do coletor parado",
    "coleta_parada": "Coleta de preços parada",
    "erros_ml": "Muitos erros na API do Mercado Livre",
    "fila_atrasada": "Coletor atrasado",
    "whatsapp_desconectado": "WhatsApp desconectado",
}


@dataclass(frozen=True, slots=True)
class HealthFacts:
    now: datetime
    lease_expires_at: datetime | None
    last_refresh_ok_at: datetime | None
    errors_last_hour: int
    products_active: int
    products_overdue: int
    whatsapp_down: bool = False


def _hhmm(at: datetime) -> str:
    return f"{at.astimezone(SP):%d/%m %H:%M}"


def firing(facts: HealthFacts) -> dict[str, str]:
    """Alertas que valem agora: tipo → mensagem."""
    out: dict[str, str] = {}
    if facts.lease_expires_at is None or facts.now - facts.lease_expires_at > LEASE_GRACE:
        since = _hhmm(facts.lease_expires_at) if facts.lease_expires_at else "o início"
        out["worker_parado"] = f"O worker não renova o lease desde {since}."
    if facts.products_active and (
        facts.last_refresh_ok_at is None or facts.now - facts.last_refresh_ok_at > REFRESH_STALE
    ):
        since = _hhmm(facts.last_refresh_ok_at) if facts.last_refresh_ok_at else "nunca"
        out["coleta_parada"] = f"Última atualização de preço concluída: {since}."
    if facts.errors_last_hour >= ERRORS_PER_HOUR:
        out["erros_ml"] = f"{facts.errors_last_hour} erros de integração na última hora."
    overdue_floor = max(20, facts.products_active // 10)
    if facts.products_overdue >= overdue_floor:
        out["fila_atrasada"] = (
            f"{facts.products_overdue} de {facts.products_active} produtos com leitura atrasada "
            "há mais de 1 hora."
        )
    if facts.whatsapp_down:
        out["whatsapp_desconectado"] = (
            "O número da MegaPromoOz saiu da sessão do WhatsApp. Reconecte em Número."
        )
    return out


def should_remind(notified_at: datetime | None, now: datetime) -> bool:
    return notified_at is None or now - notified_at >= REMIND_EVERY


@dataclass(frozen=True, slots=True)
class Mail:
    subject: str
    body: str


def mail_for(kind: str, message: str, *, event: str, opened_at: datetime, panel_url: str) -> Mail:
    """E-mail curto: o que houve, desde quando, onde olhar. `event`: aberto/lembrete/resolvido."""
    title = TITLES.get(kind, kind)
    if event == "resolvido":
        return Mail(
            f"[MegaPromoOz] Resolvido: {title}",
            f"{title}: voltou ao normal.\nAberto em {_hhmm(opened_at)}.\n\n{panel_url}",
        )
    prefix = "Ainda: " if event == "lembrete" else ""
    return Mail(
        f"[MegaPromoOz] {prefix}{title}",
        f"{message}\nAberto em {_hhmm(opened_at)}.\n\nDetalhes no painel: {panel_url}",
    )
