#!/usr/bin/env bash
# Roda um comando avulso na imagem da MegaPromoOz com o ambiente da stack, na hel1.
#
#   infra/scripts/megapromooz-cli.sh <tag> alembic upgrade head
#   infra/scripts/megapromooz-cli.sh <tag> python -m app.cli seed-niches config/nichos.json
#   infra/scripts/megapromooz-cli.sh <tag> python -m app.cli stats
#
# ENV_FILE (padrão /root/.megapromooz.env, modo 600, fora do repo) traz DB_PASSWORD e
# MIGRATE_DB_PASSWORD; ML_ENV_FILE (padrão /root/.megapromooz-ml.env) traz ML_CLIENT_ID e
# ML_CLIENT_SECRET, só para comandos que falam com o ML.
set -euo pipefail

TAG="${1:?usage: megapromooz-cli.sh <image-tag> <command...>}"
shift
[ "$#" -gt 0 ] || { echo "missing command" >&2; exit 2; }
[ "$TAG" != latest ] || { echo "refusing :latest; pass the commit tag" >&2; exit 2; }
ENV_FILE="${ENV_FILE:-/root/.megapromooz.env}"
ML_ENV_FILE="${ML_ENV_FILE:-/root/.megapromooz-ml.env}"
[ -r "$ENV_FILE" ] || { echo "env file not readable: $ENV_FILE" >&2; exit 2; }

ml_flags=()
if [ -r "$ML_ENV_FILE" ]; then ml_flags=(--env-file "$ML_ENV_FILE"); fi

tty_flags=(-i)
if [ -t 0 ] && [ -t 1 ]; then tty_flags=(-it); fi

exec docker run --rm "${tty_flags[@]}" \
  --network chatbot-net \
  --add-host host.docker.internal:host-gateway \
  --env-file "$ENV_FILE" \
  "${ml_flags[@]}" \
  -e ENVIRONMENT=production \
  -e DB_HOST=host.docker.internal \
  "muhrilobianco/megapromooz:${TAG}" "$@"
