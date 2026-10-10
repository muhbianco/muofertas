# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T154014Z/app`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 192 | — |
| domain_discovery_ferramentas | 200 | 624 | — |
| category_ferramentas_MLB271379 | 200 | 164 | — |
| highlights_ferramentas_MLB271379 | 200 | 144 | — |
| highlights_ferramentas_MLB271220 | 200 | 162 | — |
| trends_ferramentas_MLB271379 | 200 | 145 | — |
| search_keyword_ferramentas | 403 | 155 | forbidden forbidden |
| domain_discovery_maquiagem | 200 | 502 | — |
| category_maquiagem_MLB29871 | 200 | 166 | — |
| highlights_maquiagem_MLB29871 | 200 | 165 | — |
| highlights_maquiagem_MLB437489 | 200 | 173 | — |
| trends_maquiagem_MLB29871 | 200 | 174 | — |
| search_keyword_maquiagem | 403 | 138 | forbidden forbidden |
| trends_mlb | 200 | 163 | — |
| sites_mlb_categories | 200 | 171 | — |
| category_MLB432825 | 200 | 172 | — |
| highlights_doc_MLB432825 | 200 | 175 | — |
| product_MLB63274546 | 200 | 229 | — |
| product_items_MLB63274546 | 200 | 223 | — |
| product_MLB33390256 | 200 | 209 | — |
| product_items_MLB33390256 | 200 | 485 | — |
| user_product_MLBU3889027129 | 403 | 199 | forbidden caller is not allowed to access this user product |
| user_product_MLBU3376686264 | 403 | 177 | forbidden caller is not allowed to access this user product |
| burst_product_items x10 | 200 | 346 | http [200], ms 213-346 |
| items_bulk | 200 | 168 | — |
| item_MLB3102417023 | 403 | 155 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3102417023 | 403 | 175 | Access to the requested resource is forbidden |
| item_MLB3756906841 | 403 | 163 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB3756906841 | 403 | 154 | Access to the requested resource is forbidden |
| item_MLB4136460605 | 403 | 164 | access_denied Access to the requested resource is forbidden |
| sale_price_MLB4136460605 | 403 | 165 | Access to the requested resource is forbidden |

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

HTTP: [403] · ms mín/máx: 160/411 · cabeçalhos de cota: ['Content-Type', 'Date', 'X-Request-Id']
