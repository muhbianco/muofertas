#!/usr/bin/env bash
# Migração one-shot da stack `megapromooz`. Roda na hel1 ANTES do StackUpdate da mesma tag, para
# o código novo nunca subir contra schema antigo (expand first). Idempotente.
#
#   infra/scripts/megapromooz-migrate.sh <image-tag>
#
# O Alembic usa o usuário de migração (MIGRATE_DB_PASSWORD de /root/.megapromooz.env). Depois, o
# usuário de runtime (só DML basta) recarrega os nichos de config/nichos.json, que a imagem
# carrega na mesma tag.
set -euo pipefail

TAG="${1:?usage: megapromooz-migrate.sh <image-tag>}"
exec "$(dirname "$0")/megapromooz-cli.sh" "$TAG" \
  sh -c "alembic upgrade head && python -m app.cli seed-niches config/nichos.json"
