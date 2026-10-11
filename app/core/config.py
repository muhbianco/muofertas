from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Configuração lida do ambiente (e de `.env` em desenvolvimento).

    Segredos (senha do banco, client secret do ML) chegam pelo Env da stack ou pelo `.env` local
    e nunca são logados: são `SecretStr`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = "development"
    log_level: str = "INFO"

    # --- banco (usuário de runtime: só DML) ----------------------------------------------
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "megapromooz_app"
    db_password: SecretStr = SecretStr("")
    db_name: str = "megapromooz"
    db_pool_size: int = 3
    db_max_overflow: int = 2
    db_pool_recycle_seconds: int = 1800
    db_connect_timeout_seconds: int = 10
    # URL completa (testes usam sqlite+aiosqlite).
    database_url_override: str = ""

    # --- banco (usuário de migração: DDL só neste schema) -------------------------------
    migrate_db_user: str = "megapromooz_migrate"
    migrate_db_password: SecretStr = SecretStr("")

    # --- Mercado Livre (token do app por client_credentials) ----------------------------
    ml_api_base: str = "https://api.mercadolibre.com"
    ml_client_id: str = ""
    ml_client_secret: SecretStr = SecretStr("")
    ml_timeout_seconds: float = 15.0
    # Ritmo conservador: o ML não publica o limite. Sondagem de 10/10/2026: 10 chamadas seguidas
    # sem 429. Começamos em ~1/s com teto por hora e medimos.
    ml_requests_per_second: float = 1.0
    ml_hourly_call_cap: int = 2500

    # --- coletor ------------------------------------------------------------------------
    collector_enabled: bool = True
    discovery_interval_minutes: int = 360
    tier_hot_minutes: int = 20
    # 180 → 90 em 11/10/2026: 25% dos produtos mudaram de preço em 16 h e a oferta relâmpago dura
    # poucas horas. Com ~650 produtos dá ~450 chamadas/h, longe do teto abaixo.
    tier_warm_minutes: int = 90
    tier_cold_minutes: int = 1440
    heartbeat_hours: int = 24
    # Vendedor comum só entra no preço com reputação amarela/verde e este mínimo de vendas.
    min_seller_sales: int = 50
    refresh_batch_size: int = 40
    worker_idle_seconds: float = 5.0
    worker_lease_seconds: int = 60

    # --- alertas (o processo web vigia; e-mail pelo fluxo próprio do produto no n8n) ------
    alerts_enabled: bool = True
    alert_check_seconds: int = 300
    alert_email_webhook_url: str = ""
    alert_email_webhook_token: SecretStr = SecretStr("")
    alert_email_to: str = ""
    panel_url: str = "https://ofertas.muhbianco.com.br"
    # Convite de operador: fluxo próprio no n8n (texto fixo lá; só o link vem daqui). O token é
    # o mesmo do fluxo de alertas (credencial HOOK).
    invite_email_webhook_url: str = ""

    # --- WhatsApp (WuzAPI da muconnect, rede interna) ------------------------------------
    # O token admin só cria a sessão e lê o número dela. Os dois tokens vêm do Env da stack.
    wuzapi_base_url: str = ""
    wuzapi_admin_token: SecretStr = SecretStr("")
    wuzapi_session_token: SecretStr = SecretStr("")
    wuzapi_session_name: str = "megapromooz"
    wuzapi_timeout_seconds: float = 15.0
    # Envio automático para grupos com transporte "wuzapi" (decisão do dono, 10/10/2026).
    autosend_enabled: bool = True
    # Convite diário de compartilhamento (11/10/2026): 1 por grupo por dia, no horário de pico
    # (hora de Brasília). Textos e calendário de eventos em `config/convites.json`.
    invites_enabled: bool = True
    invite_hour: int = 19
    invites_file: str = "config/convites.json"
    # Código de afiliado do ML (`matt_tool`, público: vai em todo link). Com ele e a etiqueta
    # do grupo, o link nasce na aprovação; vazio, o link vem colado pela tela Links.
    ml_affiliate_tool_id: str = ""
    # Sessão do portal de afiliados (cookie + X-Csrf-Token) para o link curto oficial: arquivo do
    # host montado só leitura no worker (infra/scripts/set-ml-session-hel1.sh). Sem ele: link longo.
    ml_session_file: str = "/run/megapromooz/ml-session"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return _mariadb_url(
            self.db_user, self.db_password, self.db_host, self.db_port, self.db_name
        )

    @property
    def migrate_database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return _mariadb_url(
            self.migrate_db_user, self.migrate_db_password, self.db_host, self.db_port, self.db_name
        )


def _mariadb_url(user: str, password: SecretStr, host: str, port: int, name: str) -> str:
    secret = quote_plus(password.get_secret_value())
    return f"mysql+asyncmy://{quote_plus(user)}:{secret}@{host}:{port}/{name}?charset=utf8mb4"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
