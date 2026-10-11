"""Convite diário de compartilhamento e calendário de eventos comerciais (regras puras).

Um convite por grupo por dia, no horário de pico. O texto vem do evento comercial que vale para o
nicho naquele dia, pela fase:

- `periodo`: do início do evento até a antevéspera do auge;
- `vespera`: o dia antes do auge;
- `auge`: o dia do evento (Black Friday, Dia das Crianças...);
- `reta_final`: depois do auge até o fim (Cyber Monday, últimos dias de Natal).

Sem evento, um convite geral do nicho, alternando as variações pelo dia (o mesmo grupo não recebe o
mesmo texto dois dias seguidos). O calendário também diz quando o preço de referência da nota deve
parar de andar (`congela_referencia`): o esquenta da Black Friday sobe preço, e o desconto do dia
tem de ser medido contra o preço de antes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

PHASES = ("periodo", "vespera", "auge", "reta_final")
# Fase sem texto próprio no evento cai na mais parecida antes do padrão geral.
PHASE_FALLBACK = {"vespera": "periodo", "reta_final": "auge"}
ALL_NICHES = "*"
# O preço de referência para de andar este tempo antes do auge do evento.
FREEZE_BEFORE_PEAK = timedelta(days=21)


class InviteConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CommercialEvent:
    slug: str
    name: str
    starts_on: date
    peak_on: date
    ends_on: date
    niches: frozenset[str]
    texts: dict[str, str] = field(default_factory=dict)
    freezes_reference: bool = False

    def applies_to(self, niche_slug: str) -> bool:
        return ALL_NICHES in self.niches or niche_slug in self.niches

    def phase_on(self, day: date) -> str | None:
        if not self.starts_on <= day <= self.ends_on:
            return None
        if day == self.peak_on:
            return "auge"
        if day == self.peak_on - timedelta(days=1):
            return "vespera"
        return "periodo" if day < self.peak_on else "reta_final"


@dataclass(frozen=True, slots=True)
class InviteConfig:
    themes: dict[str, str]
    general: tuple[str, ...]
    default_phases: dict[str, str]
    events: tuple[CommercialEvent, ...]


@dataclass(frozen=True, slots=True)
class InvitePlan:
    kind: str  # periodo | vespera | auge | reta_final | geral
    event_slug: str | None
    event_name: str | None
    message: str


def _date(raw: Any, where: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise InviteConfigError(f"{where}: data inválida {raw!r}") from exc


def _check_text(text: Any, where: str) -> str:
    if not isinstance(text, str) or "{link}" not in text:
        raise InviteConfigError(f"{where}: texto sem {{link}}")
    try:
        text.format(evento="", tema="", link="")
    except (KeyError, IndexError, ValueError) as exc:
        raise InviteConfigError(f"{where}: chave desconhecida no texto ({exc})") from exc
    return text


def parse_config(data: dict[str, Any]) -> InviteConfig:
    """`config/convites.json` → regras. Erra alto: convite com texto quebrado não sai."""
    themes = {str(k): str(v) for k, v in (data.get("temas") or {}).items()}
    general = tuple(_check_text(t, "gerais") for t in data.get("gerais") or [])
    if not general:
        raise InviteConfigError("gerais: pelo menos um texto")
    defaults = {
        phase: _check_text(text, f"fases_padrao.{phase}")
        for phase, text in (data.get("fases_padrao") or {}).items()
    }
    missing = [p for p in PHASES if p not in defaults]
    if missing:
        raise InviteConfigError(f"fases_padrao: faltam {missing}")
    events = []
    for raw in data.get("eventos") or []:
        slug = str(raw.get("slug") or "")
        where = f"evento {slug or '?'}"
        event = CommercialEvent(
            slug=slug,
            name=str(raw.get("nome") or slug),
            starts_on=_date(raw.get("inicio"), where),
            peak_on=_date(raw.get("auge"), where),
            ends_on=_date(raw.get("fim"), where),
            niches=frozenset(str(n) for n in raw.get("nichos") or []),
            texts={
                phase: _check_text(text, f"{where}.{phase}")
                for phase, text in (raw.get("textos") or {}).items()
            },
            freezes_reference=bool(raw.get("congela_referencia")),
        )
        if not slug or not event.niches:
            raise InviteConfigError(f"{where}: slug e nichos são obrigatórios")
        if not event.starts_on <= event.peak_on <= event.ends_on:
            raise InviteConfigError(f"{where}: precisa de início <= auge <= fim")
        if set(event.texts) - set(PHASES):
            raise InviteConfigError(f"{where}: fase desconhecida em textos")
        events.append(event)
    return InviteConfig(themes, general, defaults, tuple(sorted(events, key=lambda e: e.peak_on)))


def event_for(day: date, niche_slug: str, config: InviteConfig) -> CommercialEvent | None:
    """O evento do nicho naquele dia; com dois ao mesmo tempo, o de auge mais próximo."""
    active = [e for e in config.events if e.applies_to(niche_slug) and e.phase_on(day)]
    if not active:
        return None
    return min(active, key=lambda e: abs((e.peak_on - day).days))


def _event_text(event: CommercialEvent, phase: str, config: InviteConfig) -> str:
    if phase in event.texts:
        return event.texts[phase]
    fallback = PHASE_FALLBACK.get(phase)
    if fallback and fallback in event.texts:
        return event.texts[fallback]
    return config.default_phases[phase]


def plan_invite(day: date, niche_slug: str, link: str, config: InviteConfig) -> InvitePlan:
    theme = config.themes.get(niche_slug, "ofertas")
    event = event_for(day, niche_slug, config)
    if event is not None:
        phase = event.phase_on(day)
        assert phase is not None
        template = _event_text(event, phase, config)
        message = template.format(evento=event.name, tema=theme, link=link)
        return InvitePlan(phase, event.slug, event.name, message)
    template = config.general[day.toordinal() % len(config.general)]
    return InvitePlan("geral", None, None, template.format(evento="", tema=theme, link=link))


def reference_frozen_until(day: date, niche_slug: str, config: InviteConfig) -> date | None:
    """Último dia que entra no preço de referência, quando um evento congela a referência.

    Do início ao fim do evento, a referência para 21 dias antes do auge: o preço que sobe no
    esquenta não vira base do desconto do dia.
    """
    for event in config.events:
        if event.freezes_reference and event.applies_to(niche_slug) and event.phase_on(day):
            return event.peak_on - FREEZE_BEFORE_PEAK
    return None
