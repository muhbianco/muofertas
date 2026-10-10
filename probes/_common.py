"""Utilidades das sondagens da Fase 0.

Regras da casa: o segredo é lido de arquivo, nunca impresso, nunca gravado na saída. O que vai
para `docs/fase0/` é resposta saneada de API pública (produto, preço, link), sem credencial.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SECRETS_DIR = Path(os.environ.get("MPO_SECRETS_DIR", Path.home() / ".secrets" / "megapromooz"))
TIMEOUT_S = 15.0


class MissingSecretError(RuntimeError):
    """O arquivo de segredo não existe ou falta chave. A mensagem cita só nomes, nunca valores."""


def load_env_file(name: str, required: tuple[str, ...]) -> dict[str, str]:
    path = SECRETS_DIR / name
    if not path.is_file():
        raise MissingSecretError(
            f"crie {path} com as chaves: {', '.join(required)} (formato CHAVE=valor, uma por linha)"
        )
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in required if not values.get(k)]
    if missing:
        raise MissingSecretError(f"{path} sem as chaves: {', '.join(missing)}")
    return values


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    elapsed_ms: int
    headers: dict[str, str]
    body: Any  # JSON decodificado, ou texto curto quando não é JSON


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> HttpResult:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
            status = resp.status
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    try:
        parsed: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = raw[:500].decode("utf-8", errors="replace")
    return HttpResult(status=status, elapsed_ms=elapsed_ms, headers=resp_headers, body=parsed)


SAFE_HEADER_PREFIXES = ("x-ratelimit", "ratelimit", "retry-after", "x-request-id", "date", "content-type")


def safe_headers(headers: dict[str, str]) -> dict[str, str]:
    """Só cabeçalhos úteis para medir limite e rastrear chamada; nada de cookie ou autorização."""
    return {k: v for k, v in headers.items() if k.lower().startswith(SAFE_HEADER_PREFIXES)}


def run_dir(provider: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "docs" / "fase0" / provider / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json(directory: Path, name: str, data: Any) -> None:
    (directory / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
