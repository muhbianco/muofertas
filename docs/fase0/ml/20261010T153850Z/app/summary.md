# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T153850Z/app`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 192 | — |
| domain_discovery_ferramentas | 200 | 467 | — |
| category_ferramentas_MLB271379 | 200 | 169 | — |
| highlights_ferramentas_MLB271379 | 200 | 165 | — |
| highlights_ferramentas_MLB271220 | 200 | 150 | — |
| trends_ferramentas_MLB271379 | 200 | 163 | — |
| search_keyword_ferramentas | 403 | 160 | forbidden forbidden |
| domain_discovery_maquiagem | 200 | 458 | — |
| category_maquiagem_MLB29871 | 200 | 159 | — |
| highlights_maquiagem_MLB29871 | 200 | 163 | — |
| highlights_maquiagem_MLB437489 | 200 | 171 | — |
| trends_maquiagem_MLB29871 | 200 | 165 | — |
| search_keyword_maquiagem | 403 | 160 | forbidden forbidden |
| trends_mlb | 200 | 155 | — |
| sites_mlb_categories | 200 | 159 | — |
| category_MLB432825 | 200 | 170 | — |
| highlights_doc_MLB432825 | 200 | 150 | — |
| product_MLB63274546 | 200 | 202 | — |
| product_MLB33390256 | 200 | 195 | — |
| items_bulk | 400 | 155 | bad_request Duplicate item id: MLB3102417023 |
| item_MLB3102417023 | 403 | 170 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3102417023 | 403 | 158 | Access to the requested resource is forbidden |
| item_MLB3756906841 | 403 | 178 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3756906841 | 403 | 174 | Access to the requested resource is forbidden |
| item_MLB4136460605 | 403 | 166 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB4136460605 | 403 | 170 | Access to the requested resource is forbidden |

## Tipos no top 20 por categoria

```json
{
  "ferramentas/MLB271379": {
    "PRODUCT": 6,
    "USER_PRODUCT": 14
  },
  "ferramentas/MLB271220": {
    "PRODUCT": 12,
    "USER_PRODUCT": 8
  },
  "maquiagem/MLB29871": {
    "PRODUCT": 14,
    "USER_PRODUCT": 4,
    "ITEM": 2
  },
  "maquiagem/MLB437489": {
    "PRODUCT": 14,
    "ITEM": 3,
    "USER_PRODUCT": 3
  },
  "doc/MLB432825": {
    "USER_PRODUCT": 15,
    "PRODUCT": 5
  }
}
```

## Campos presentes no item (`/items/{id}`)

```json
{
  "MLB3102417023": {
    "price": false,
    "original_price": false,
    "catalog_product_id": false,
    "official_store_id": false,
    "available_quantity": false,
    "sold_quantity": false,
    "condition": false,
    "status": false,
    "seller_id": false,
    "category_id": false,
    "permalink": false,
    "thumbnail": false
  },
  "MLB3756906841": {
    "price": false,
    "original_price": false,
    "catalog_product_id": false,
    "official_store_id": false,
    "available_quantity": false,
    "sold_quantity": false,
    "condition": false,
    "status": false,
    "seller_id": false,
    "category_id": false,
    "permalink": false,
    "thumbnail": false
  },
  "MLB4136460605": {
    "price": false,
    "original_price": false,
    "catalog_product_id": false,
    "official_store_id": false,
    "available_quantity": false,
    "sold_quantity": false,
    "condition": false,
    "status": false,
    "seller_id": false,
    "category_id": false,
    "permalink": false,
    "thumbnail": false
  }
}
```

## Rajada de 10 chamadas a sale_price

HTTP: [403] · ms mín/máx: 152/181 · cabeçalhos de cota: ['Content-Type', 'Date', 'X-Request-Id']
