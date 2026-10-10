"""Cliente da WuzAPI (instância `muconnect`, rede interna `chatbot-net`).

Duas portas com credenciais diferentes, como na api-agents:

- **admin** (`Authorization: <token admin>`): esse token lista **todas** as sessões da instância,
  com número e token de cada uma, inclusive as dos lojistas do modo expor. Aqui ele só faz duas
  coisas: criar a sessão da MegaPromoOz e ler a sessão dela pelo id. Não existe método de listar.
- **sessão** (`token: <token da sessão>`): conectar, QR, código, estado, grupos.

Rotas conferidas no spec da imagem em uso (`asternic/wuzapi:1.0.9`, `/app/static/api/spec.yml`,
em 10/10/2026). A lista de participantes dos grupos traz telefones: este módulo devolve só
contagens, e nada dela vai para log ou banco (LGPD, §9.6 do plano).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class WuzapiError(Exception):
    """`maybe_sent`: a requisição pode ter chegado (timeout depois de enviar). Envio nessa
    situação nunca é repetido sozinho: duplicata no grupo custa mais que uma oferta perdida."""

    def __init__(
        self, message: str, *, status: int | None = None, maybe_sent: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.maybe_sent = maybe_sent


class WuzapiNotConfiguredError(WuzapiError):
    pass


@dataclass(frozen=True, slots=True)
class SessionState:
    connected: bool
    logged_in: bool
    jid: str | None = None


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """Grupo sem a lista de participantes: só o que o painel precisa."""

    jid: str
    name: str
    announce: bool
    members: int
    admins: int


def _detail(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return (response.text or "")[:160]
    if isinstance(body, dict):
        for key in ("error", "message", "details", "Details"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
        data = body.get("data")
        if isinstance(data, dict):
            for key in ("error", "Details", "details"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:160]
    return (response.text or "")[:160]


def _data(response: httpx.Response) -> Any:
    try:
        body: Any = response.json()
    except ValueError:
        return {}
    return body.get("data", body) if isinstance(body, dict) else body


def summarize_group(raw: dict[str, Any]) -> GroupSummary | None:
    jid = raw.get("JID")
    if not isinstance(jid, str) or not jid.endswith("@g.us"):
        return None
    participants = raw.get("Participants")
    people = (
        [p for p in participants if isinstance(p, dict)] if isinstance(participants, list) else []
    )
    return GroupSummary(
        jid=jid,
        name=str(raw.get("Name") or "")[:120],
        announce=bool(raw.get("IsAnnounce")),
        members=len(people),
        admins=sum(1 for p in people if p.get("IsAdmin") or p.get("IsSuperAdmin")),
    )


def digits(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "")


class WuzapiClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._admin_token = settings.wuzapi_admin_token.get_secret_value().strip()
        self._session_token = settings.wuzapi_session_token.get_secret_value().strip()
        self._configured = bool(
            settings.wuzapi_base_url.strip() and self._admin_token and self._session_token
        )
        self._http = httpx.AsyncClient(
            base_url=settings.wuzapi_base_url.rstrip("/") or "http://wuzapi.invalid",
            timeout=httpx.Timeout(settings.wuzapi_timeout_seconds, connect=5.0),
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self._configured

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        admin: bool = False,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if not self._configured:
            raise WuzapiNotConfiguredError("WhatsApp não configurado no servidor")
        token = self._admin_token if admin else self._session_token
        headers = {"Authorization": token} if admin else {"token": token, "Authorization": token}
        try:
            response = await self._http.request(
                method, path, headers=headers, params=params, json=json
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise WuzapiError("a WuzAPI não respondeu") from exc  # nem chegou a enviar
        except httpx.HTTPError as exc:
            raise WuzapiError("a WuzAPI não respondeu", maybe_sent=True) from exc
        if response.status_code >= 400:
            detail = _detail(response)
            logger.warning("WuzAPI recusou", extra={"path": path, "status": response.status_code})
            raise WuzapiError(detail or "a WuzAPI recusou", status=response.status_code)
        return _data(response)

    # ------------------------------------------------------------------ admin (só estas duas)

    async def create_session(self, name: str) -> str:
        """`POST /admin/users` sem webhook: a MegaPromoOz não recebe mensagem de ninguém."""
        data = await self._request(
            "POST",
            "/admin/users",
            admin=True,
            json={"name": name, "token": self._session_token, "webhook": "", "events": "Message"},
        )
        external_id = str((data or {}).get("id") or (data or {}).get("Id") or "")
        if not external_id:
            raise WuzapiError("a WuzAPI não devolveu o id da sessão")
        return external_id

    async def read_session(self, external_id: str) -> SessionState | None:
        """`GET /admin/users/{id}`: a única porta que traz o número conectado."""
        try:
            data = await self._request("GET", f"/admin/users/{external_id}", admin=True)
        except WuzapiError as exc:
            if exc.status == 404:
                return None
            raise
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            return None
        jid = str(row.get("jid") or "") or None
        return SessionState(
            connected=bool(row.get("connected")), logged_in=bool(row.get("loggedIn")), jid=jid
        )

    # ------------------------------------------------------------------ sessão

    async def connect(self) -> None:
        await self._request(
            "POST", "/session/connect", json={"Subscribe": ["Message"], "Immediate": True}
        )

    async def status(self) -> SessionState:
        data = await self._request("GET", "/session/status")
        data = data if isinstance(data, dict) else {}
        return SessionState(
            connected=bool(data.get("Connected") or data.get("connected")),
            logged_in=bool(data.get("LoggedIn") or data.get("loggedIn")),
        )

    async def qr(self) -> str:
        """Data URI do QR (vazio quando já pareado ou quando o aparelho pediu passkey)."""
        data = await self._request("GET", "/session/qr")
        data = data if isinstance(data, dict) else {}
        qr = str(data.get("QRCode") or data.get("qrcode") or "")
        return qr if qr.startswith("data:image/") else ""

    async def pair_code(self, phone: str) -> str:
        data = await self._request("POST", "/session/pairphone", json={"Phone": digits(phone)})
        data = data if isinstance(data, dict) else {}
        return str(data.get("LinkingCode") or data.get("linkingCode") or "")

    async def logout(self) -> None:
        await self._request("POST", "/session/logout")

    # ------------------------------------------------------------------ grupos

    async def groups(self) -> list[GroupSummary]:
        data = await self._request("GET", "/group/list")
        raw = data.get("Groups") if isinstance(data, dict) else None
        groups = [summarize_group(g) for g in raw or [] if isinstance(g, dict)]
        return sorted((g for g in groups if g), key=lambda g: g.name.lower())

    async def send_text(self, chat_jid: str, body: str) -> str:
        """`POST /chat/send/text` para o grupo (`Phone` = JID `…@g.us`; o parseJID da 1.0.9
        aceita JID completo). Sem prévia de link: a prévia faria o servidor abrir o link de
        afiliado, um clique de robô."""
        data = await self._request(
            "POST",
            "/chat/send/text",
            json={"Phone": chat_jid, "Body": body, "LinkPreview": False},
        )
        return str((data or {}).get("Id") or "") if isinstance(data, dict) else ""

    async def invite_link(self, group_jid: str) -> str:
        data = await self._request("GET", "/group/invitelink", params={"groupJID": group_jid})
        link = str((data or {}).get("InviteLink") or "") if isinstance(data, dict) else ""
        return link if link.startswith("https://chat.whatsapp.com/") else ""

    async def set_announce(self, group_jid: str, announce: bool = True) -> None:
        await self._request(
            "POST", "/group/announce", json={"GroupJID": group_jid, "Announce": announce}
        )
