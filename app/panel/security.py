"""Segurança do painel: senha (argon2), segundo fator TOTP, tokens de sessão e freio de login.

TOTP pela RFC 6238 (HMAC-SHA1, 6 dígitos, passo de 30 s, como o Google Authenticator), com a
biblioteca padrão. Aceita o passo atual e um de cada lado (relógio do celular adiantado ou
atrasado) e recusa passo já usado (replay).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections import deque
from urllib.parse import quote

from pwdlib import PasswordHash

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES_PER_IP = 5
LOGIN_MAX_FAILURES_GLOBAL = 30

_hasher = PasswordHash.recommended()
# Hash de uma senha qualquer: login com usuário inexistente gasta o mesmo tempo que um real.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    return _hasher.verify(password, password_hash or _DUMMY_HASH) and password_hash is not None


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret_b32: str, step: int, digits: int = TOTP_DIGITS) -> str:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(padded)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return f"{code:0{digits}d}"


def verify_totp(secret_b32: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Devolve o passo aceito (para gravar como último) ou None. Comparação em tempo constante."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return None
    current = int((time.time() if now is None else now) // TOTP_STEP_SECONDS)
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if step > last_step and hmac.compare_digest(totp_at(secret_b32, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, username: str, issuer: str = "MegaPromoOz") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&digits=6&period=30"


def new_session_token() -> tuple[str, str]:
    """(token para o cookie, hash para o banco)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """Freio de tentativas em memória. Vale porque o web roda com uma réplica só (stack).

    Por IP: 5 falhas em 15 min bloqueiam aquele IP. Global: 30 falhas em 15 min fecham o login
    para todos até a janela andar (defesa contra tentativa distribuída).
    """

    def __init__(self) -> None:
        self._by_ip: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > LOGIN_WINDOW_SECONDS:
            q.popleft()

    def allowed(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._trim(self._global, now)
        q = self._by_ip.get(ip)
        if q is not None:
            self._trim(q, now)
            if not q:
                del self._by_ip[ip]
                q = None
        return len(self._global) < LOGIN_MAX_FAILURES_GLOBAL and (
            q is None or len(q) < LOGIN_MAX_FAILURES_PER_IP
        )

    def failure(self, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._global.append(now)
        self._by_ip.setdefault(ip, deque()).append(now)

    def success(self, ip: str) -> None:
        self._by_ip.pop(ip, None)
