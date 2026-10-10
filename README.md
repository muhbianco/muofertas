# MegaPromoOz

Inteligência de ofertas de afiliado. Fase 1: **coletor do Mercado Livre e histórico de preços**.

- Planejamento e decisões: `docs/`. A Fase 0 está em `docs/fase0/README.md`; a escolha dos nichos,
  em `docs/fase0/nichos/README.md`.
- Marca: `brand/`.

## O que roda
- **`megapromooz-worker`** (`python -m app.worker`): um agendador só, garantido por lease no banco.
  - Descobre produtos de catálogo nos mais vendidos das categorias de `config/nichos.json`.
  - Atualiza o preço deles por `/products/{id}/items`.
  - Grava `price_observation` quando o preço muda, mais um batimento diário.
  - Camadas: quente 20 min, morna 3 h, fria 24 h.
  - Ritmo do ML: 1 chamada/s com teto de 2.500/h. Um 429 pausa o ciclo por 15 min.
- **`megapromooz-web`** (`uvicorn app.main:app`): `/health/live`, `/health/ready` e
  `/health/collector`. Sem rota pública na Fase 1.

## Desenvolvimento
```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/mypy app && .venv/Scripts/python -m pytest -q
```

Coletor local contra o ML de verdade, em SQLite:
```bash
DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///./local-e2e.db .venv/Scripts/alembic upgrade head
```

```bash
DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///./local-e2e.db .venv/Scripts/python -m app.cli seed-niches config/nichos.json
```

```bash
set -a; . ~/.secrets/megapromooz/ml-app.env; set +a; DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///./local-e2e.db .venv/Scripts/python -m app.cli run-once
```

## Produção (hel1)
- Stack `megapromooz` (Portainer id 17), criada em 10/10/2026 na tag `7eaa91a9a576`, com os
  serviços `megapromooz_megapromooz-web` e `megapromooz_megapromooz-worker`.
- Checkout em `/usr/src/muofertas`.
- Segredos só em arquivos do host, modo 600:
  - `/root/.megapromooz.env`: senhas do banco, geradas pelo `provision-db.sh`;
  - `/root/.megapromooz-ml.env`: app do ML;
  - `/root/.megapromooz-stack.env`: o Env da criação da stack, com `DB_PASSWORD` e as chaves do ML.
- Pendente: ativar o repositório no Woodpecker. Até lá, push na `main` não faz deploy.

## Primeiro deploy (uma vez)
1. **Repositório** no GitHub e ativação no Woodpecker (`ci.muhbianco.com.br`).
2. **Banco** na hel1, como root e a partir de um checkout do repo:
   ```bash
   infra/scripts/provision-db.sh
   ```
   Cria o schema `megapromooz`, os usuários `megapromooz_migrate`/`megapromooz_app` e o
   `/root/.megapromooz.env` (modo 600). Nenhuma senha é impressa.
3. **Credenciais do ML** em `/root/.megapromooz-ml.env` (modo 600, colocado pelo dono), com
   `ML_CLIENT_ID` e `ML_CLIENT_SECRET`.
4. **Criar a stack** com o Env vindo dos arquivos:
   ```bash
   portainer-stack-update.py --create --stack megapromooz --yaml infra/docker-stack.yml --env-file <arquivo combinado> --set-env MEGAPROMOOZ_TAG=<sha12>
   ```
5. A partir daí: **push na `main` = deploy** (Woodpecker: build → migração → StackUpdate).
   Depois de cada deploy, conferir o Chatwoot (exceção ao congelamento do App Review).

## Rollback
```bash
ROLLBACK=1 infra/scripts/deploy.sh <sha12 anterior>
```

As migrations são só aditivas.
