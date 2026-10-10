"""Varredura de nichos no Mercado Livre (Fase 0): qual categoria rende melhor para um grupo de ofertas?

Usa só o que a sondagem provou que abre para o token do app (client_credentials):
/sites/MLB/categories, /categories/{id}, /highlights/MLB/category/{id} e /products/{id}/items.

Para cada categoria mede, nos mais vendidos que são **produto de catálogo** (os únicos com preço pela API):
- cobertura: quantos dos 20 mais vendidos são PRODUCT (o que o motor consegue vigiar);
- ticket: mediana do menor preço por produto;
- loja oficial: % de produtos com oferta de loja oficial;
- "de/por" exibido: % de ofertas com original_price > price e o desconto mediano exibido;
- campanha: % de ofertas com deal_ids; frete grátis: % de ofertas;
- disputa: ofertas por produto e dispersão de preço (mediana das ofertas vs. menor preço).
É um retrato de um dia, não histórico. A comissão vem da tabela oficial do afiliado (ajuda 27913).

Uso: python -I probes/ml_niche_scan.py   → docs/fase0/nichos/<carimbo UTC>/
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import ROOT, http_request, run_dir  # noqa: E402
from ml_probe import API, app_token  # noqa: E402

PAUSE_S = 0.35            # ritmo conservador: ~3 chamadas/s no pico
TOP_PRODUCTS = 10         # produtos de catálogo por categoria de 1º nível
TOP_PRODUCTS_CHILD = 5    # por subcategoria
CHILD_PARENTS = ("beleza", "ferramentas", "calcados", "esportes", "casa", "construcao", "bebes", "brinquedos")

# Tabela oficial de comissão do afiliado (venda direta), lida em 10/10/2026 na ajuda 27913.
COMMISSION = [
    (("beleza", "calcados", "esportes"), 0.16),
    (("veiculos", "bebes", "brinquedos", "casa", "construcao", "ferramentas", "games", "joias", "livros"), 0.12),
    (("camera", "celular", "eletrodomestico", "eletronico", "informatica"), 0.05),
    (("alimentos",), 0.0),
]
# Produtos não recomendáveis pelo programa (ajuda 30088): alimentos, saúde, pet, veículos, imóveis, ingressos.
NOT_ALLOWED = ("alimentos", "saude", "pet", "imoveis", "ingressos", "carros, motos")


def norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def commission(name: str) -> float | None:
    n = norm(name)
    for keys, rate in COMMISSION:
        if any(k in n for k in keys):
            return rate
    return 0.12 if n else None  # "Mais categorias" = 12% pela tabela


class Stop(RuntimeError):
    """O ML pediu para parar (429) ou a política fechou a rota."""


class Api:
    def __init__(self, token: str) -> None:
        self.token = token
        self.calls = 0

    def get(self, path: str) -> Any:
        time.sleep(PAUSE_S)
        self.calls += 1
        r = http_request("GET", f"{API}{path}", headers={"Authorization": f"Bearer {self.token}"})
        if r.status == 429:
            raise Stop(f"429 em {path} depois de {self.calls} chamadas")
        return r.body if r.status == 200 else None


def offers_metrics(api: Api, product_ids: list[str]) -> dict[str, Any]:
    min_prices, officials, offer_counts, spreads = [], 0, [], []
    shown_disc, n_offers, n_shown, n_deal, n_free = [], 0, 0, 0, 0
    for pid in product_ids:
        body = api.get(f"/products/{pid}/items") or {}
        offers = [o for o in body.get("results", []) if isinstance(o.get("price"), (int, float))]
        if not offers:
            continue
        prices = sorted(o["price"] for o in offers)
        min_prices.append(prices[0])
        offer_counts.append(len(offers))
        spreads.append(statistics.median(prices) / prices[0] - 1 if prices[0] else 0)
        officials += any(o.get("official_store_id") for o in offers)
        for o in offers:
            n_offers += 1
            orig = o.get("original_price")
            if isinstance(orig, (int, float)) and orig > o["price"]:
                n_shown += 1
                shown_disc.append(1 - o["price"] / orig)
            n_deal += bool(o.get("deal_ids"))
            n_free += bool((o.get("shipping") or {}).get("free_shipping"))
    k = len(min_prices)
    return {
        "produtos_com_preco": k,
        "ticket_mediano": round(statistics.median(min_prices), 2) if k else None,
        "loja_oficial_pct": round(100 * officials / k) if k else None,
        "ofertas_por_produto_med": statistics.median(offer_counts) if k else None,
        "dispersao_preco_med_pct": round(100 * statistics.median(spreads)) if k else None,
        "de_por_exibido_pct": round(100 * n_shown / n_offers) if n_offers else None,
        "desconto_exibido_med_pct": round(100 * statistics.median(shown_disc)) if shown_disc else None,
        "campanha_pct": round(100 * n_deal / n_offers) if n_offers else None,
        "frete_gratis_pct": round(100 * n_free / n_offers) if n_offers else None,
    }


def scan_category(api: Api, cat_id: str, name: str, top: int) -> dict[str, Any]:
    hl = api.get(f"/highlights/MLB/category/{cat_id}") or {}
    content = hl.get("content", [])
    types: dict[str, int] = {}
    for e in content:
        types[e.get("type", "?")] = types.get(e.get("type", "?"), 0) + 1
    products = [e["id"] for e in content if e.get("type") == "PRODUCT"][:top]
    rate = commission(name)
    row: dict[str, Any] = {
        "id": cat_id, "nome": name, "mais_vendidos": len(content),
        "catalogo_no_top20": types.get("PRODUCT", 0), "tipos": types,
        "comissao_direta": rate,
        "permitido": not any(k in norm(name) for k in NOT_ALLOWED),
    }
    row.update(offers_metrics(api, products))
    if row.get("ticket_mediano") is not None and rate is not None:
        row["comissao_por_pedido_med"] = round(row["ticket_mediano"] * rate, 2)
    return row


def main() -> int:
    token = app_token()
    if not token:
        return 2
    api = Api(token)
    out = run_dir("nichos")
    rows: list[dict[str, Any]] = []
    children_rows: list[dict[str, Any]] = []
    try:
        top_level = api.get("/sites/MLB/categories") or []
        print(f"{len(top_level)} categorias de 1º nível")
        for cat in top_level:
            row = scan_category(api, cat["id"], cat["name"], TOP_PRODUCTS)
            rows.append(row)
            print(f"{cat['name'][:38]:<38} catálogo {row['catalogo_no_top20']:>2}/20  ticket {row.get('ticket_mediano')}  "
                  f"oficial {row.get('loja_oficial_pct')}%  de/por {row.get('de_por_exibido_pct')}%  ({api.calls} chamadas)")
        for cat in top_level:
            if not any(p in norm(cat["name"]) for p in CHILD_PARENTS):
                continue
            detail = api.get(f"/categories/{cat['id']}") or {}
            for child in detail.get("children_categories", []):
                row = scan_category(api, child["id"], child["name"], TOP_PRODUCTS_CHILD)
                row["pai"] = cat["name"]
                if row["comissao_direta"] is None or norm(cat["name"]) != norm(child["name"]):
                    row["comissao_direta"] = commission(cat["name"])
                    if row.get("ticket_mediano") is not None and row["comissao_direta"] is not None:
                        row["comissao_por_pedido_med"] = round(row["ticket_mediano"] * row["comissao_direta"], 2)
                children_rows.append(row)
                print(f"  {cat['name'][:14]:<14} > {child['name'][:30]:<30} catálogo {row['catalogo_no_top20']:>2}/20  "
                      f"ticket {row.get('ticket_mediano')}  ({api.calls} chamadas)")
    except Stop as exc:
        print(f"[parado] {exc}")
    (out / "nivel1.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "nivel2.json").write_text(json.dumps(children_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{api.calls} chamadas · saída: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
