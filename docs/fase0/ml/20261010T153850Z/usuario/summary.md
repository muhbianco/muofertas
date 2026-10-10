# Sondagem API Mercado Livre

Pasta: `docs/fase0/ml/20261010T153850Z/usuario`

| Sondagem | HTTP | ms | Nota |
|---|---|---|---|
| users_me_status | 200 | 199 | — |
| domain_discovery_ferramentas | 403 | 144 | At least one policy returned UNAUTHORIZED. |
| domain_discovery_maquiagem | 403 | 155 | At least one policy returned UNAUTHORIZED. |
| trends_mlb | 403 | 176 | At least one policy returned UNAUTHORIZED. |
| sites_mlb_categories | 403 | 164 | At least one policy returned UNAUTHORIZED. |
| category_MLB432825 | 200 | 170 | — |
| highlights_doc_MLB432825 | 403 | 165 | At least one policy returned UNAUTHORIZED. |

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
