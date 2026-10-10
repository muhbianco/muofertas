"""Sondagem da Shopee Affiliate Open API (Fase 0).

Confere na prática o que o levantamento marcou como [TESTAR]: campos reais, listas, feeds,
link com subIds, relatório de conversão e sinais de limite de chamadas.

Segredo: ~/.secrets/megapromooz/shopee-affiliate.env (ou $MPO_SECRETS_DIR) com
SHOPEE_APP_ID e SHOPEE_APP_SECRET. Nada do segredo é impresso ou gravado.

Assinatura (conferida no código do explorer oficial, 10/10/2026):
  Authorization: SHA256 Credential={AppId}, Timestamp={ts}, Signature=sha256_hex(AppId + ts + payload + Secret)
  payload = corpo JSON exato enviado, {"query": "..."}.

Uso:  python probes/shopee_probe.py
Saída: docs/fase0/shopee/<carimbo UTC>/ (JSON por sondagem + summary.md)
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    ROOT,
    MissingSecretError,
    http_request,
    load_env_file,
    run_dir,
    safe_headers,
    save_json,
)

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
SCHEMA = json.loads(
    (ROOT / "docs" / "fase0" / "shopee-open-api-schema-2026-10-10.json").read_text(encoding="utf-8")
)
NICHE_KEYWORDS = {"ferramentas": "parafusadeira", "maquiagem": "base maquiagem"}
BURST_CALLS = 10


# ---------------------------------------------------------------- GraphQL a partir do schema oficial

def selection(type_name: str, depth: int = 0) -> str:
    """Seleção com todos os campos do tipo, expandindo objetos aninhados até 3 níveis."""
    fields = SCHEMA["types"][type_name]["fields"]
    parts: list[str] = []
    for f in fields:
        child = f.get("type")
        if child in SCHEMA["types"] and depth < 3:
            parts.append(f"{f['name']} {{ {selection(child, depth + 1)} }}")
        elif child not in SCHEMA["types"]:
            parts.append(f["name"])
    return " ".join(parts)


def render_value(value: Any) -> str:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(render_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {render_value(v)}" for k, v in value.items()) + "}"
    raise TypeError(type(value))


class Enum:
    """Valor de enum GraphQL: vai sem aspas."""

    def __init__(self, name: str) -> None:
        self.name = name


def operation(name: str, args: dict[str, Any]) -> str:
    op = next(o for o in SCHEMA["operations"] if o["name"] == name)
    rendered = ", ".join(f"{k}: {render_value(v)}" for k, v in args.items())
    call = f"{name}({rendered})" if rendered else name
    kind = "mutation" if op["opType"] == "mutation" else "query"
    returns = op.get("returnType")
    return f"{kind} {{ {call} {{ {selection(returns)} }} }}"


# ---------------------------------------------------------------- transporte

class Client:
    def __init__(self, app_id: str, secret: str) -> None:
        self._app_id = app_id
        self._secret = secret

    def call(self, query: str) -> dict[str, Any]:
        body = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
        ts = str(int(time.time()))
        signature = hashlib.sha256((self._app_id + ts + body + self._secret).encode("utf-8")).hexdigest()
        res = http_request(
            "POST",
            ENDPOINT,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"SHA256 Credential={self._app_id}, Timestamp={ts}, Signature={signature}",
            },
            body=body.encode("utf-8"),
        )
        errors = res.body.get("errors") if isinstance(res.body, dict) else None
        return {
            "http_status": res.status,
            "elapsed_ms": res.elapsed_ms,
            "headers": safe_headers(res.headers),
            "errors": errors,
            "data": res.body.get("data") if isinstance(res.body, dict) else res.body,
        }


# ---------------------------------------------------------------- análise

def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def describe_products(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not nodes:
        return {"count": 0}

    def share(pred) -> str:
        return f"{sum(1 for n in nodes if pred(n))}/{len(nodes)}"

    rates = [r for r in (to_float(n.get("commissionRate")) for n in nodes) if r is not None]
    seller = [r for r in (to_float(n.get("sellerCommissionRate")) for n in nodes) if r is not None]
    prices = [p for p in (to_float(n.get("price")) for n in nodes) if p is not None]
    return {
        "count": len(nodes),
        "commissionRate_min_med_max": [min(rates), statistics.median(rates), max(rates)] if rates else None,
        "sellerCommissionRate_gt0": share(lambda n: (to_float(n.get("sellerCommissionRate")) or 0) > 0),
        "sellerCommissionRate_med": statistics.median(seller) if seller else None,
        "price_min_med_max": [min(prices), statistics.median(prices), max(prices)] if prices else None,
        "priceMin_ne_priceMax": share(lambda n: n.get("priceMin") != n.get("priceMax")),
        "priceDiscountRate_gt0": share(lambda n: (n.get("priceDiscountRate") or 0) > 0),
        "periodEndTime_set": share(lambda n: bool(n.get("periodEndTime"))),
        "shopType_values": sorted({str(n.get("shopType")) for n in nodes}),
        "ratingStar_present": share(lambda n: bool(n.get("ratingStar"))),
        "sales_present": share(lambda n: n.get("sales") is not None),
        "example": {k: nodes[0].get(k) for k in ("itemId", "productName", "price", "priceMin", "priceMax",
                                                  "priceDiscountRate", "commissionRate", "sellerCommissionRate",
                                                  "shopeeCommissionRate", "sales", "ratingStar", "shopType",
                                                  "periodStartTime", "periodEndTime", "productCatIds")},
    }


def nodes_of(result: dict[str, Any], op: str) -> list[dict[str, Any]]:
    data = result.get("data") or {}
    block = data.get(op) or {}
    return block.get("nodes") or []


# ---------------------------------------------------------------- sondagens

def main() -> int:
    try:
        secret = load_env_file("shopee-affiliate.env", ("SHOPEE_APP_ID", "SHOPEE_APP_SECRET"))
    except MissingSecretError as exc:
        print(f"[segredo] {exc}")
        return 2
    client = Client(secret["SHOPEE_APP_ID"], secret["SHOPEE_APP_SECRET"])
    out = run_dir("shopee")
    summary: list[tuple[str, dict[str, Any]]] = []

    def probe(name: str, query: str) -> dict[str, Any]:
        result = client.call(query)
        save_json(out, name, {"query": query, **result})
        summary.append((name, result))
        status = "erro" if result["errors"] else "ok"
        print(f"{name:<34} http={result['http_status']} {result['elapsed_ms']:>5} ms  {status}")
        return result

    fields_note = "todos os campos do schema"
    first_item: dict[str, Any] | None = None
    analyses: dict[str, Any] = {}

    for niche, keyword in NICHE_KEYWORDS.items():
        for sort_name, sort_type in (("vendidos", 2), ("comissao", 5)):
            r = probe(
                f"product_{niche}_{sort_name}",
                operation("productOfferV2", {"keyword": keyword, "sortType": sort_type, "page": 1, "limit": 20}),
            )
            nodes = nodes_of(r, "productOfferV2")
            analyses[f"{niche}/{sort_name}"] = describe_products(nodes)
            if first_item is None and nodes:
                first_item = nodes[0]

    for list_name, list_type in (("highest_commission", 1), ("top_performing", 2)):
        r = probe(f"product_list_{list_name}", operation("productOfferV2", {"listType": list_type, "page": 1, "limit": 20}))
        analyses[f"lista/{list_name}"] = describe_products(nodes_of(r, "productOfferV2"))

    probe("shopee_offers", operation("shopeeOfferV2", {"sortType": 1, "page": 1, "limit": 20}))
    probe("shop_offers_official", operation("shopOfferV2", {"shopType": [1], "sortType": 2, "page": 1, "limit": 10}))

    feeds = probe("item_feeds", operation("listItemFeeds", {}))
    feed_list = ((feeds.get("data") or {}).get("listItemFeeds") or {}).get("feeds") or []
    full = next((f for f in feed_list if f.get("feedMode") == "FULL"), None)
    delta = next((f for f in feed_list if f.get("feedMode") == "DELTA"), None)
    for label, feed in (("full", full), ("delta", delta)):
        if feed and feed.get("datafeedId"):
            probe(f"item_feed_data_{label}", operation("getItemFeedData", {"datafeedId": feed["datafeedId"], "offset": 0, "limit": 5}))

    burst: list[dict[str, Any]] = []
    if first_item:
        item_id = int(first_item["itemId"])
        probe("product_by_item_id", operation("productOfferV2", {"itemId": item_id, "page": 1, "limit": 1}))
        link_url = first_item.get("productLink") or first_item.get("offerLink")
        if link_url:
            probe(
                "short_link_subids",
                operation("generateShortLink", {"input": {"originUrl": link_url, "subIds": ["fase0", "probe", "t1"]}}),
            )
        # Rajada curta e sequencial: só observa se aparece limite (10030) ou cabeçalho de cota.
        q = operation("productOfferV2", {"itemId": item_id, "page": 1, "limit": 1})
        for i in range(BURST_CALLS):
            r = client.call(q)
            codes = [((e.get("extensions") or {}).get("code"), e.get("message")) for e in (r["errors"] or [])]
            burst.append({"i": i, "http": r["http_status"], "ms": r["elapsed_ms"], "errors": codes, "headers": r["headers"]})
        save_json(out, "burst_item_lookup", burst)

    now = int(time.time())
    probe(
        "conversion_report_30d",
        operation("conversionReport", {"purchaseTimeStart": now - 30 * 86400, "purchaseTimeEnd": now, "limit": 10}),
    )

    save_json(out, "analysis", analyses)
    write_summary(out, summary, analyses, burst, fields_note)
    print(f"\nsaída: {out.relative_to(ROOT)}")
    return 0


def write_summary(out: Path, summary, analyses, burst, fields_note: str) -> None:
    lines = [
        "# Sondagem Shopee Affiliate Open API",
        "",
        f"Pasta: `{out.relative_to(ROOT).as_posix()}` · consultas com {fields_note}.",
        "",
        "| Sondagem | HTTP | ms | Erros |",
        "|---|---|---|---|",
    ]
    for name, r in summary:
        errs = "; ".join(
            f"{(e.get('extensions') or {}).get('code', '')} {e.get('message', '')}".strip() for e in (r["errors"] or [])
        )
        lines.append(f"| {name} | {r['http_status']} | {r['elapsed_ms']} | {errs or '—'} |")
    lines += ["", "## Análise dos produtos", "", "```json", json.dumps(analyses, ensure_ascii=False, indent=2), "```"]
    if burst:
        ms = [b["ms"] for b in burst]
        errs = [b for b in burst if b["errors"] or b["http"] != 200]
        lines += [
            "",
            f"## Rajada de {len(burst)} chamadas sequenciais",
            "",
            f"Latência mín/mediana/máx: {min(ms)}/{statistics.median(ms)}/{max(ms)} ms · chamadas com erro: {len(errs)}",
            f"Cabeçalhos de cota vistos: {sorted({k for b in burst for k in b['headers']}) or 'nenhum'}",
        ]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
