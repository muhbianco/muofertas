# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T153930Z/app`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 200 | — |
| domain_discovery_ferramentas | 200 | 294 | — |
| category_ferramentas_MLB271379 | 200 | 163 | — |
| highlights_ferramentas_MLB271379 | 200 | 158 | — |
| highlights_ferramentas_MLB271220 | 200 | 154 | — |
| trends_ferramentas_MLB271379 | 200 | 158 | — |
| search_keyword_ferramentas | 403 | 164 | forbidden forbidden |
| domain_discovery_maquiagem | 200 | 471 | — |
| category_maquiagem_MLB29871 | 200 | 161 | — |
| highlights_maquiagem_MLB29871 | 200 | 167 | — |
| highlights_maquiagem_MLB437489 | 200 | 172 | — |
| trends_maquiagem_MLB29871 | 200 | 175 | — |
| search_keyword_maquiagem | 403 | 163 | forbidden forbidden |
| trends_mlb | 200 | 144 | — |
| sites_mlb_categories | 200 | 170 | — |
| category_MLB432825 | 200 | 156 | — |
| highlights_doc_MLB432825 | 200 | 155 | — |
| product_MLB63274546 | 200 | 200 | — |
| product_items_MLB63274546 | 200 | 211 | — |
| product_MLB33390256 | 200 | 191 | — |
| product_items_MLB33390256 | 200 | 336 | — |
| user_product_MLBU3889027129 | 403 | 184 | forbidden caller is not allowed to access this user product |
| user_product_MLBU3376686264 | 403 | 168 | forbidden caller is not allowed to access this user product |
| items_bulk | 200 | 154 | — |
| item_MLB3102417023 | 403 | 181 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3102417023 | 403 | 163 | Access to the requested resource is forbidden |
| item_MLB3756906841 | 403 | 172 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3756906841 | 403 | 151 | Access to the requested resource is forbidden |
| item_MLB4136460605 | 403 | 179 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB4136460605 | 403 | 176 | Access to the requested resource is forbidden |

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

HTTP: [403] · ms mín/máx: 164/386 · cabeçalhos de cota: ['Content-Type', 'Date', 'X-Request-Id']
