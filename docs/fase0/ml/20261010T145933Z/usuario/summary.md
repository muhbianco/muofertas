# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T145933Z/usuario`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 187 | — |
| domain_discovery_ferramentas | 403 | 158 | At least one policy returned UNAUTHORIZED. |
| domain_discovery_maquiagem | 403 | 166 | At least one policy returned UNAUTHORIZED. |
| trends_mlb | 403 | 188 | At least one policy returned UNAUTHORIZED. |
| sites_mlb_categories | 403 | 178 | At least one policy returned UNAUTHORIZED. |
| category_MLB432825 | 200 | 202 | — |
| highlights_doc_MLB432825 | 403 | 185 | At least one policy returned UNAUTHORIZED. |

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
