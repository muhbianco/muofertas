"""Modelos da MegaPromoOz.

Nichos e suas categorias, produtos de catálogo monitorados, observações de preço, execuções de
coleta, erros, avaliações, operador, grupos e publicações. Cupom entra numa próxima migration.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    false,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import ensure_utc


class UTCDateTime(TypeDecorator[datetime]):
    """Grava UTC sem fuso (DATETIME do MariaDB) e devolve sempre aware em UTC."""

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class Base(DeclarativeBase):
    pass


class Niche(Base):
    __tablename__ = "niche"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Regras da nota por nicho (0002). Comissão em pontos-base: 1600 = 16% (tabela do afiliado ML).
    commission_rate_bp: Mapped[int] = mapped_column(Integer, default=1200)
    require_official: Mapped[bool] = mapped_column(Boolean, default=False)
    high_risk_brands: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON: lista de marcas
    # 0022: no nicho que exige loja oficial, MercadoLíder Gold/Platinum (verde) também vale, menos
    # nos tipos de produto listados (JSON de domain_id), que seguem só loja oficial.
    accept_trusted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    official_only_domains: Mapped[str | None] = mapped_column(Text, nullable=True)


class NicheCategory(Base):
    """Categoria do marketplace que alimenta um nicho (descoberta pelos mais vendidos)."""

    __tablename__ = "niche_category"
    __table_args__ = (UniqueConstraint("niche_id", "marketplace", "external_category_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    niche_id: Mapped[int] = mapped_column(ForeignKey("niche.id", ondelete="CASCADE"))
    marketplace: Mapped[str] = mapped_column(String(20))
    external_category_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_discovery_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MarketplaceDomain(Base):
    """Tipo de produto do marketplace (no ML, o `domain_id` do catálogo: "Compressores de ar").

    0020. Guarda o nome em português para o painel e, quando há, o nicho fixo do tipo: vale mais
    que a categoria pela qual o produto foi descoberto e menos que o nicho escolhido para um
    produto (`niche_locked`). A regra vem do `nichos.json` (`config`) ou do painel (`painel`); o
    seed nunca mexe na do painel.
    """

    __tablename__ = "marketplace_domain"
    __table_args__ = (UniqueConstraint("marketplace", "domain_id", name="uq_marketplace_domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(20))
    domain_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    niche_id: Mapped[int | None] = mapped_column(
        ForeignKey("niche.id", ondelete="SET NULL"), nullable=True
    )
    rule_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rule_actor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CatalogProduct(Base):
    """Produto de catálogo monitorado (no ML, a página /p/MLB…)."""

    __tablename__ = "catalog_product"
    __table_args__ = (
        UniqueConstraint("marketplace", "external_id"),
        Index("ix_catalog_product_due", "active", "next_check_at"),
        Index("ix_catalog_product_needs_eval", "needs_evaluation", "active"),
        Index("ix_catalog_product_domain", "domain_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    marketplace: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(32))
    niche_id: Mapped[int | None] = mapped_column(
        ForeignKey("niche.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    domain_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    permalink: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    discovered_via: Mapped[str] = mapped_column(String(20))  # highlights | watchlist
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tier: Mapped[str] = mapped_column(String(8))
    next_check_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_observation_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_in_highlights_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    best_highlight_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nota (0002): o coletor marca para reavaliar quando o preço muda; o avaliador desmarca.
    needs_evaluation: Mapped[bool] = mapped_column(Boolean, default=False)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_tier: Mapped[str | None] = mapped_column(String(12), nullable=True)
    last_score: Mapped[float | None] = mapped_column(Numeric(5, 1, asdecimal=False), nullable=True)
    # Painel (0003): o operador tira o produto da fila de vez.
    operator_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # 0029: quando entrou (ou voltou) na camada quente, pela última queda relevante.
    hot_since: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0013: % OFF do próprio anúncio na última avaliação (travas do post); ordena a fila.
    listing_discount_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 0019: nicho escolhido pelo operador; o seed das categorias não move mais o produto.
    niche_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class PriceObservation(Base):
    """Série de preço do produto: uma linha por mudança, mais um batimento diário."""

    __tablename__ = "price_observation"
    __table_args__ = (Index("ix_price_observation_product_time", "product_id", "observed_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id", ondelete="CASCADE"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    kind: Mapped[str] = mapped_column(String(10))  # first | change | heartbeat (0018)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    min_price_cents: Mapped[int] = mapped_column(BigInteger)
    min_official_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    offers_count: Mapped[int] = mapped_column(Integer)
    official_offers_count: Mapped[int] = mapped_column(Integer)
    deal_offers_count: Mapped[int] = mapped_column(Integer)
    free_shipping_offers_count: Mapped[int] = mapped_column(Integer)
    min_offer_item_id: Mapped[str] = mapped_column(String(32))
    min_offer_seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_offer_original_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_offer_free_shipping: Mapped[bool] = mapped_column(Boolean, default=False)
    # 0004: o preço que vale é o de vendedor elegível (regra do afiliado) ou confiável (loja
    # oficial / MercadoLíder ouro-platina). O `min_*` acima é bruto e só serve de sinal.
    eligible_min_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eligible_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    eligible_seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eligible_offers_count: Mapped[int] = mapped_column(Integer, default=0)
    trusted_min_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trusted_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trusted_seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eligible_official: Mapped[bool] = mapped_column(Boolean, default=False)
    trusted_official: Mapped[bool] = mapped_column(Boolean, default=False)
    official_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    official_seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 0013: o "de" (preço riscado) da oferta de cada régua. Linha antiga: originals_known falso.
    eligible_original_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trusted_original_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    official_original_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    originals_known: Mapped[bool] = mapped_column(Boolean, default=True, server_default=false())
    # 0026: régua "reputado" (oficial, MercadoLíder Gold/Platinum ou verde com 500+ vendas).
    # Linha antiga: reputable_known falso, fora da série dessa régua.
    reputable_min_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reputable_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reputable_seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reputable_official: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    reputable_original_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reputable_known: Mapped[bool] = mapped_column(Boolean, default=True, server_default=false())


class MarketplaceSeller(Base):
    """Reputação do vendedor (cache de /users/bulk, renovado a cada 7 dias)."""

    __tablename__ = "marketplace_seller"
    __table_args__ = (UniqueConstraint("marketplace", "external_id"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    marketplace: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[int] = mapped_column(BigInteger)
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    level_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    power_seller_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transactions_total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CollectionRun(Base):
    __tablename__ = "collection_run"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(40))  # ml_discovery | ml_refresh
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(12))  # running | ok | partial | failed
    calls: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_changed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class IntegrationError(Base):
    __tablename__ = "integration_error"
    __table_args__ = (Index("ix_integration_error_time", "occurred_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(40))
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_run.id", ondelete="SET NULL"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class OfferEvaluation(Base):
    """Avaliação de um produto num instante: imutável (só insert), com tudo o que entrou na conta.

    Responde "por que esta oferta foi Excepcional?" com os dados daquele momento: `snapshot` traz
    portões, sinais, componentes, preço usado, referência e a versão das regras.
    """

    __tablename__ = "offer_evaluation"
    __table_args__ = (
        Index("ix_offer_evaluation_product_time", "product_id", "evaluated_at"),
        Index("ix_offer_evaluation_tier_time", "tier", "evaluated_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id", ondelete="CASCADE"))
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    trigger: Mapped[str] = mapped_column(String(16))  # price_change | daily
    ruleset: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(12))
    score: Mapped[float] = mapped_column(Numeric(5, 1, asdecimal=False))
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_official: Mapped[bool] = mapped_column(Boolean, default=False)
    real_discount_bp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    snapshot: Mapped[str] = mapped_column(Text)  # JSON da Evaluation


class AdminUser(Base):
    """Operador do painel. Senha em argon2; segundo fator TOTP (Google Authenticator)."""

    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str] = mapped_column(String(64))
    # Último passo TOTP aceito: o mesmo código não entra duas vezes (replay).
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AdminSession(Base):
    """Sessão do painel. O cookie leva o token; o banco guarda só o hash dele."""

    __tablename__ = "admin_session"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class OperatorDecision(Base):
    """Decisão do operador sobre uma avaliação (só insert: aprovar, rejeitar, bloquear)."""

    __tablename__ = "operator_decision"
    __table_args__ = (Index("ix_operator_decision_product_time", "product_id", "decided_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id", ondelete="CASCADE"))
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_evaluation.id", ondelete="SET NULL"), nullable=True
    )
    decision: Mapped[str] = mapped_column(String(12))  # approved | rejected | blocked
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ApprovalClaim(Base):
    """Quem marcou ✓/✕ o produto na fila de aprovação (0030): uma linha por produto.

    Trava leve entre operadores: os outros veem "em aprovação por X" enquanto alguma aba de X
    está aberta (`seen_at` recente) e não conseguem marcar o mesmo produto. A marca dura algumas
    horas e acompanha o operador em outro aparelho. Não impede decidir: quem aprova mesmo assim
    confirma na tela; a garantia de não aprovar duas vezes é do /decisao."""

    __tablename__ = "approval_claim"

    product_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("catalog_product.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    actor: Mapped[str] = mapped_column(String(40))
    mark: Mapped[str] = mapped_column(String(12))  # approved | rejected
    claimed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WorkerLease(Base):
    """Garante um agendador só, mesmo durante deploy (se curar sozinho: lição do Mu Tower)."""

    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    holder: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Channel(Base):
    """Grupo de WhatsApp de um nicho, com a etiqueta do ML e a política de envio (0005).

    `transport` diz quem envia: `manual` (o operador posta pelo celular a partir do painel) ou,
    na Fase 2b, `wuzapi`. Horários da janela em hora de Brasília.
    """

    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    niche_id: Mapped[int] = mapped_column(ForeignKey("niche.id"))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    transport: Mapped[str] = mapped_column(String(12), default="manual")
    ml_tag: Mapped[str | None] = mapped_column(String(30), nullable=True)
    invite_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_per_day: Mapped[int] = mapped_column(Integer, default=30)
    # Desde 11/10/2026 o intervalo é calculado (janela ÷ envios do dia, `spacing_minutes`) e a
    # coluna só guarda o último valor calculado ao salvar; o envio não a lê.
    min_interval_minutes: Mapped[int] = mapped_column(Integer, default=45)
    window_start_hour: Mapped[int] = mapped_column(Integer, default=7)
    window_end_hour: Mapped[int] = mapped_column(Integer, default=22)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # 0028: vem marcado no "Grupos ▾" da aprovação e no link avulso de todo produto, não só
    # dos do nicho dele (o grupo Família, que mais rende, desde 13/10/2026).
    always_checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # 0031: "ritmo" (max_per_day espalhados na janela) ou "lote" (batch_size anúncios a cada
    # batch_every_minutes, alinhados ao começo da janela), pedido do dono em 16/10/2026.
    send_mode: Mapped[str] = mapped_column(String(8), default="ritmo", server_default="ritmo")
    batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batch_every_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # 0008: grupo de verdade no WhatsApp (JID `…@g.us`), ligado pela tela Grupos.
    wa_jid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class Publication(Base):
    """Uma oferta aprovada a caminho de um grupo (0005). Nunca é apagada: é a prova do envio.

    Estados: `awaiting_link` (falta o link de afiliado do ML) → `ready` → `sent`; ou `skipped`
    (repetida, nicho pausado) e `discarded` (o operador desistiu ou o preço subiu).
    `idempotency_key` impede duas publicações da mesma avaliação no mesmo grupo.
    """

    __tablename__ = "publication"
    __table_args__ = (
        Index("ix_publication_channel_dedup", "channel_id", "dedup_key", "created_at"),
        Index("ix_publication_state", "state", "created_at"),
        Index("ix_publication_channel_sent", "channel_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id"))
    evaluation_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_evaluation.id", ondelete="SET NULL"), nullable=True
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("operator_decision.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    dedup_key: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    tier: Mapped[str] = mapped_column(String(12))
    item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_url: Mapped[str] = mapped_column(String(512))
    affiliate_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approved_price_cents: Mapped[int] = mapped_column(BigInteger)
    checked_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0006: preço do Pix que o operador viu na página (a API não traz).
    pix_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 0009: quando o Pix foi visto, e o envio automático (no máximo uma vez).
    pix_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sending_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 0011: o "de" do próprio anúncio na última revalidação (preço riscado da página).
    original_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 0023: menor preço de loja oficial do produto na mesma revalidação (post de vendedor comum).
    official_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 0012: preço com o cupom da loja, visto pelo operador na aprovação (a API não traz cupom).
    coupon_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    coupon_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0024: "últimas N unidades" visto na página pelo operador ao aprovar (a API não traz estoque).
    last_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_units_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0025: fim da oferta relâmpago informado no link avulso (o post não sai depois dele).
    flash_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0015: posição na fila de envio do grupo, quando o operador reordena (vazio = ordem de
    # aprovação, depois das posicionadas).
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 0029: o 1º 404 na conferência adia o envio até aqui (pode ser instabilidade do ML).
    retry_after: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # 0032: o produto saiu para o grupo há menos de 24 h; este post só sai a partir daqui, e na
    # frente da fila (pedido do dono em 20/10/2026: antes ficava de fora por 7 dias).
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    link_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Alert(Base):
    """Alerta crítico da operação (0007): abre uma vez, é lembrado e fecha sozinho."""

    __tablename__ = "alert"
    __table_args__ = (Index("ix_alert_state_kind", "state", "kind"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(10))  # open | resolved
    message: Mapped[str] = mapped_column(String(255))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    resolution_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class WaSession(Base):
    """A sessão da MegaPromoOz na WuzAPI (0008). Uma linha só; o token fica no Env, não aqui."""

    __tablename__ = "wa_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    external_id: Mapped[str] = mapped_column(String(64))
    jid: Mapped[str | None] = mapped_column(String(80), nullable=True)
    logged_in: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ChannelStat(Base):
    """Tamanho do grupo por dia (0008): só contagens, nunca quem são os membros (LGPD)."""

    __tablename__ = "channel_stat"
    __table_args__ = (UniqueConstraint("channel_id", "day"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    members: Mapped[int] = mapped_column(Integer)
    admins: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AdminInvite(Base):
    """Convite de operador (0010): link de uso único, 48 h. Do token só fica o hash.

    O convidado escolhe usuário e senha e cadastra o autenticador ele mesmo; o segredo TOTP nasce
    na primeira visita ao link e só vira operador depois do primeiro código certo.
    """

    __tablename__ = "admin_invite"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    username: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ChannelInvite(Base):
    """Convite de compartilhamento do grupo (0014): uma linha por grupo, dia e horário (0016).

    O índice único é a trava do "no máximo um por horário". Estados: `sending` (gravado antes de
    chamar a WuzAPI), `sent`, `unknown` (a WuzAPI não confirmou: não reenvia), `failed` (recusado)
    e `skipped` (o operador pulou o dia).
    """

    __tablename__ = "channel_invite"
    __table_args__ = (
        UniqueConstraint("channel_id", "local_date", "slot", name="uq_channel_invite_slot"),
        Index("ix_channel_invite_state", "state", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    local_date: Mapped[date] = mapped_column(Date)
    # 0016: manha (primeiro horário de envio do grupo) | noite (pico, INVITE_HOUR).
    slot: Mapped[str] = mapped_column(String(10), default="noite", server_default="noite")
    kind: Mapped[str] = mapped_column(String(12))
    event_slug: Mapped[str | None] = mapped_column(String(60), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(80), nullable=True)


class SalesCheck(Base):
    """Checagem da lista de vendas do painel de afiliado (0017), a cada 3 h.

    `status`: ok | session | blocked | transient | rejected | sem_sessao. `item_keys`: só os nomes
    dos campos de uma venda (JSON), nunca os valores.
    """

    __tablename__ = "sales_check"
    __table_args__ = (Index("ix_sales_check_checked_at", "checked_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(12))
    total_results: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_keys: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
