# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T145716Z/app`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 177 | — |
| domain_discovery_ferramentas | 403 | 162 | At least one policy returned UNAUTHORIZED. |
| domain_discovery_maquiagem | 403 | 163 | At least one policy returned UNAUTHORIZED. |
| trends_mlb | 403 | 159 | At least one policy returned UNAUTHORIZED. |
| sites_mlb_categories | 403 | 150 | At least one policy returned UNAUTHORIZED. |
| category_MLB432825 | 200 | 150 | — |
| highlights_doc_MLB432825 | 403 | 153 | At least one policy returned UNAUTHORIZED. |

## Tipos no top 20 por categoria

```json
{
  "doc/MLB432825": {}
}
```

## Campos presentes no item (`/items/{id}`)

```json
{}
```
