"""Sondagem da API de desenvolvedor do Mercado Livre (Fase 0).

Pergunta que responde: um app OAuth de quem NÃO é vendedor consegue ler mais vendidos, tendências,
item e preço de venda? E a busca por palavra-chave continua fechada?

Rotas conferidas na doc oficial em 10/10/2026:
  /highlights/MLB/category/{id} · /trends/MLB[/{id}] · /items/{id}/sale_price?context=channel_marketplace
  /items/bulk?ids= (substitui /items?ids= até 25/10/2026) · /sites/MLB/domain_discovery/search
OAuth: authorization_code (+ PKCE, obrigatório se ligado no app). O formulário do DevCenter também
oferece client_credentials (visto em 10/10/2026, a página de autenticação não cita): o `run` testa
os dois tokens, lado a lado. Token de 6 h; o refresh devolve um refresh token novo, que é gravado.

Segredos (nunca impressos):
  ~/.secrets/megapromooz/ml-app.env   ML_CLIENT_ID, ML_CLIENT_SECRET, ML_REDIRECT_URI
  ~/.secrets/megapromooz/ml-tokens.json  (escrito por `auth`)

Uso:
  python probes/ml_probe.py auth   # só o dono, no terminal dele: abre o login e cola o code
  python probes/ml_probe.py run    # sondagens; saída em docs/fase0/ml/<carimbo UTC>/
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import re
import secrets
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    ROOT,
    SECRETS_DIR,
    MissingSecretError,
    http_request,
    load_env_file,
    run_dir,
    safe_headers,
    save_json,
)

API = "https://api.mercadolibre.com"
AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKENS = SECRETS_DIR / "ml-tokens.json"
PKCE_FILE = SECRETS_DIR / "ml-pkce.tmp"
NICHE_QUERIES = {"ferramentas": "parafusadeira", "maquiagem": "base maquiagem"}
DOC_CATEGORY = "MLB432825"  # categoria usada no exemplo oficial de /highlights


def app_config() -> dict[str, str]:
    return load_env_file("ml-app.env", ("ML_CLIENT_ID", "ML_CLIENT_SECRET", "ML_REDIRECT_URI"))


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: herda a ACL da pasta do usuário


# ---------------------------------------------------------------- OAuth (só o dono roda)

def cmd_auth() -> int:
    cfg = app_config()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    write_private(PKCE_FILE, verifier)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cfg["ML_CLIENT_ID"],
        "redirect_uri": cfg["ML_REDIRECT_URI"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print("1) Abra no navegador, entre com a conta de afiliado e autorize:\n")
    print(url)
    print("\n2) O navegador vai para o redirect_uri com ?code=TG-... na barra de endereço.")
    code = getpass.getpass("3) Cole aqui só o valor do code (não aparece na tela): ").strip()
    if not code:
        print("sem code; nada feito")
        return 1
    res = token_request({
        "grant_type": "authorization_code",
        "client_id": cfg["ML_CLIENT_ID"],
        "client_secret": cfg["ML_CLIENT_SECRET"],
        "code": code,
        "redirect_uri": cfg["ML_REDIRECT_URI"],
        "code_verifier": verifier,
    })
    PKCE_FILE.unlink(missing_ok=True)
    if res is None:
        return 1
    print(f"tokens gravados em {TOKENS} (escopo: {res.get('scope')})")
    return 0


def token_request(form: dict[str, str]) -> dict[str, Any] | None:
    r = http_request(
        "POST",
        f"{API}/oauth/token",
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode(form).encode(),
    )
    if r.status != 200 or not isinstance(r.body, dict) or "access_token" not in r.body:
        detail = r.body.get("message") or r.body.get("error") if isinstance(r.body, dict) else str(r.body)[:120]
        print(f"[oauth] falhou: http={r.status} {detail}")
        return None
    body = r.body
    stored = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": int(time.time()) + int(body.get("expires_in", 21600)) - 120,
        "scope": body.get("scope"),
    }
    write_private(TOKENS, json.dumps(stored))
    return {"scope": stored["scope"]}


def access_token() -> str:
    if not TOKENS.is_file():
        raise MissingSecretError(f"rode `python probes/ml_probe.py auth` antes (falta {TOKENS})")
    tok = json.loads(TOKENS.read_text(encoding="utf-8"))
    if tok["expires_at"] > time.time():
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise MissingSecretError("token expirado e sem refresh_token; rode `auth` de novo")
    cfg = app_config()
    if token_request({
        "grant_type": "refresh_token",
        "client_id": cfg["ML_CLIENT_ID"],
        "client_secret": cfg["ML_CLIENT_SECRET"],
        "refresh_token": tok["refresh_token"],
    }) is None:
        raise MissingSecretError("refresh falhou; rode `auth` de novo")
    return json.loads(TOKENS.read_text(encoding="utf-8"))["access_token"]


# ---------------------------------------------------------------- sondagens

def app_token() -> str | None:
    """Token do próprio app (client_credentials, que o DevCenter oferece). Não é gravado.

    Se ele ler mais vendidos, item e preço de venda, o worker não depende de token de usuário
    (nem do refresh token que expira com 4 meses sem uso).
    """
    cfg = app_config()
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cfg["ML_CLIENT_ID"],
        "client_secret": cfg["ML_CLIENT_SECRET"],
    }).encode()
    # Visto em 10/10/2026: 400 error_role_idp transitório, que passa na tentativa seguinte.
    for attempt in range(3):
        r = http_request(
            "POST",
            f"{API}/oauth/token",
            headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
            body=form,
        )
        if r.status == 200 and isinstance(r.body, dict) and r.body.get("access_token"):
            print(f"[oauth] client_credentials ok (escopo: {r.body.get('scope')})")
            return r.body["access_token"]
        detail = (r.body.get("message") or r.body.get("error")) if isinstance(r.body, dict) else str(r.body)[:120]
        print(f"[oauth] client_credentials falhou (tentativa {attempt + 1}/3): http={r.status} {detail}")
        time.sleep(2 * (attempt + 1) + secrets.randbelow(1000) / 1000)
    return None


def extra_item_ids() -> list[str]:
    """IDs passados à mão: `run MLB123,MLB456` ou links de produto (o ID é extraído do link)."""
    if len(sys.argv) < 3:
        return []
    found = re.findall(r"MLB-?(\d{6,})", " ".join(sys.argv[2:]).upper())
    return [f"MLB{n}" for n in dict.fromkeys(found)]


def cmd_run() -> int:
    tokens: list[tuple[str, str]] = []
    try:
        tokens.append(("usuario", access_token()))
    except MissingSecretError as exc:
        print(f"[usuario] pulado: {exc}")
    try:
        app = app_token()
        if app:
            tokens.append(("app", app))
    except MissingSecretError as exc:
        print(f"[segredo] {exc}")
    if not tokens:
        return 2
    base = run_dir("ml")
    for label, token in tokens:
        print(f"\n=== token de {label} ===")
        out = base / label
        out.mkdir()
        run_probes(token, out)
    print(f"\nsaída: {base.relative_to(ROOT)}")
    return 0


def run_probes(token: str, out: Path) -> None:
    rows: list[tuple[str, int, int, str]] = []

    def get(name: str, path: str, *, keep_body: bool = True) -> Any:
        r = http_request("GET", f"{API}{path}", headers={"Authorization": f"Bearer {token}", "accept": "application/json"})
        note = ""
        if isinstance(r.body, dict) and r.status >= 400:
            note = f"{r.body.get('error', '')} {r.body.get('message', '')}".strip()[:120]
        save_json(out, name, {"path": path, "http_status": r.status, "elapsed_ms": r.elapsed_ms,
                              "headers": safe_headers(r.headers), "body": r.body if keep_body else "(omitido)"})
        rows.append((name, r.status, r.elapsed_ms, note))
        print(f"{name:<40} http={r.status} {r.elapsed_ms:>5} ms {note}")
        return r.body if r.status == 200 else None

    # Perguntas do "ML primeiro": o app de não-vendedor lê mais vendidos, item e preço de venda?
    # Quanto do top 20 é ITEM (tem preço por anúncio) e quanto é PRODUCT/USER_PRODUCT?

    # o token é aceito? (corpo omitido: /users/me traz dado pessoal)
    get("users_me_status", "/users/me", keep_body=False)

    item_ids: list[str] = extra_item_ids()
    product_ids: list[str] = []
    user_product_ids: list[str] = []
    type_counts: dict[str, dict[str, int]] = {}
    for niche, q in NICHE_QUERIES.items():
        found = get(f"domain_discovery_{niche}", f"/sites/MLB/domain_discovery/search?{urllib.parse.urlencode({'q': q, 'limit': 3})}")
        category = found[0].get("category_id") if isinstance(found, list) and found else None
        if not category:
            continue
        cat = get(f"category_{niche}_{category}", f"/categories/{category}") or {}
        path = [c.get("id") for c in cat.get("path_from_root", []) if c.get("id")]
        targets = [category] + ([path[-2]] if len(path) >= 2 else [])  # folha e o nível acima
        for target in targets:
            hl = get(f"highlights_{niche}_{target}", f"/highlights/MLB/category/{target}")
            counts = type_counts.setdefault(f"{niche}/{target}", {})
            for entry in (hl or {}).get("content", []):
                kind = entry.get("type", "?")
                counts[kind] = counts.get(kind, 0) + 1
                if kind == "ITEM":
                    item_ids.append(entry["id"])
                elif kind == "PRODUCT":
                    product_ids.append(entry["id"])
                elif kind == "USER_PRODUCT":
                    user_product_ids.append(entry["id"])
        get(f"trends_{niche}_{category}", f"/trends/MLB/{category}")
        get(f"search_keyword_{niche}", f"/sites/MLB/search?{urllib.parse.urlencode({'q': q, 'limit': 5})}")

    get("trends_mlb", "/trends/MLB")

    # Checagens que não dependem da descoberta de categoria. MLB432825 é o exemplo da doc oficial
    # de mais vendidos; só serve para saber se a rota abre para este token.
    get("sites_mlb_categories", "/sites/MLB/categories")
    get(f"category_{DOC_CATEGORY}", f"/categories/{DOC_CATEGORY}")
    hl = get(f"highlights_doc_{DOC_CATEGORY}", f"/highlights/MLB/category/{DOC_CATEGORY}")
    counts = type_counts.setdefault(f"doc/{DOC_CATEGORY}", {})
    for entry in (hl or {}).get("content", []):
        kind = entry.get("type", "?")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "ITEM" and entry["id"] not in item_ids:
            item_ids.append(entry["id"])
        elif kind == "PRODUCT" and entry["id"] not in product_ids:
            product_ids.append(entry["id"])

    for pid in product_ids[:2]:
        get(f"product_{pid}", f"/products/{pid}")
        # anúncios que disputam o produto de catálogo: é aqui que o preço pode aparecer
        offers = get(f"product_items_{pid}", f"/products/{pid}/items")
        for entry in (offers or {}).get("results", [])[:3]:
            if entry.get("item_id") and entry["item_id"] not in item_ids:
                item_ids.append(entry["item_id"])
    for upid in user_product_ids[:2]:
        get(f"user_product_{upid}", f"/user-products/{upid}")
    if product_ids:
        # Rajada curta e sequencial na rota que o coletor do ML vai usar (ofertas do produto de catálogo).
        burst_path = f"/products/{product_ids[0]}/items"
        catalog_burst = []
        for i in range(10):
            r = http_request("GET", f"{API}{burst_path}", headers={"Authorization": f"Bearer {token}"})
            catalog_burst.append({"i": i, "http": r.status, "ms": r.elapsed_ms, "headers": safe_headers(r.headers)})
        save_json(out, "burst_product_items", catalog_burst)
        ms = [b["ms"] for b in catalog_burst]
        rows.append(("burst_product_items x10", max(b["http"] for b in catalog_burst), max(ms),
                     f"http {sorted({b['http'] for b in catalog_burst})}, ms {min(ms)}-{max(ms)}"))
        print(f"{'burst_product_items x10':<40} http={sorted({b['http'] for b in catalog_burst})} ms {min(ms)}-{max(ms)}")

    item_fields: dict[str, dict[str, bool]] = {}
    burst: list[dict[str, Any]] = []
    if item_ids:
        item_ids = list(dict.fromkeys(item_ids))
        get("items_bulk", f"/items/bulk?ids={','.join(item_ids[:20])}")
        for iid in item_ids[:3]:
            body = get(f"item_{iid}", f"/items/{iid}") or {}
            item_fields[iid] = {k: body.get(k) not in (None, "", []) for k in (
                "price", "original_price", "catalog_product_id", "official_store_id", "available_quantity",
                "sold_quantity", "condition", "status", "seller_id", "category_id", "permalink", "thumbnail")}
            get(f"sale_price_{iid}", f"/items/{iid}/sale_price?context=channel_marketplace")
        # Rajada curta e sequencial no preço de venda: só observa limite e cabeçalhos de cota.
        path = f"/items/{item_ids[0]}/sale_price?context=channel_marketplace"
        for i in range(10):
            r = http_request("GET", f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
            burst.append({"i": i, "http": r.status, "ms": r.elapsed_ms, "headers": safe_headers(r.headers)})
        save_json(out, "burst_sale_price", burst)

    lines = ["# Sondagem API Mercado Livre", "", f"Pasta: `{out.relative_to(ROOT).as_posix()}`", "",
             "| Sondagem | HTTP | ms | Nota |", "|---|---|---|---|"]
    lines += [f"| {n} | {s} | {ms} | {note or '—'} |" for n, s, ms, note in rows]
    lines += ["", "## Tipos no top 20 por categoria", "", "```json",
              json.dumps(type_counts, ensure_ascii=False, indent=2), "```",
              "", "## Campos presentes no item (`/items/{id}`)", "", "```json",
              json.dumps(item_fields, ensure_ascii=False, indent=2), "```"]
    if burst:
        ms = [b["ms"] for b in burst]
        lines += ["", f"## Rajada de {len(burst)} chamadas a sale_price", "",
                  f"HTTP: {sorted({b['http'] for b in burst})} · ms mín/máx: {min(ms)}/{max(ms)} · "
                  f"cabeçalhos de cota: {sorted({k for b in burst for k in b['headers']}) or 'nenhum'}"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    commands = {"auth": cmd_auth, "run": cmd_run}
    command = commands.get(sys.argv[1] if len(sys.argv) > 1 else "run")
    if command is None:
        print("uso: ml_probe.py auth|run")
        sys.exit(64)
    try:
        sys.exit(command())
    except MissingSecretError as exc:
        print(f"[segredo] {exc}")
        sys.exit(2)
