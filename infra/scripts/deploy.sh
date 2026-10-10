#!/usr/bin/env bash
# Deploy da MegaPromoOz na hel1: migração one-shot (expand first) → StackUpdate do Portainer com o
# YAML do repo e MEGAPROMOOZ_TAG=<tag> (dry-run antes). Rodado pelo Woodpecker
# (.woodpecker/deploy.yaml, dentro de `hel1-deploy exec`) ou à mão como break-glass:
#
#   infra/scripts/deploy.sh <sha12>
#   ROLLBACK=1 infra/scripts/deploy.sh <sha12 anterior>
#
# ROLLBACK=1 pula a migração: as migrations são só aditivas, então o código anterior roda no
# schema mais novo. Segredos só do host: /root/.megapromooz.env e /root/.portainer-token.
#
# Depois de TODO deploy: conferir o Chatwoot (exceção ao congelamento do App Review da Meta,
# decidida pelo dono em 10/10/2026). Esta stack não toca api-agents nem chatwoot.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
[ "$TAG" != latest ] || { echo "refusing :latest; pass the commit tag being deployed" >&2; exit 2; }
cd "$(dirname "$0")/../.."
read -r -a UPDATER <<<"${PORTAINER_STACK_UPDATE:-portainer-stack-update}"

if [ "${ROLLBACK:-0}" = 1 ]; then
  echo "rollback to $TAG: migrations skipped (expand-only schema; older code runs on it)"
else
  infra/scripts/megapromooz-migrate.sh "$TAG"
fi
"${UPDATER[@]}" --stack megapromooz --yaml infra/docker-stack.yml --set-env MEGAPROMOOZ_TAG="$TAG" --dry-run
"${UPDATER[@]}" --stack megapromooz --yaml infra/docker-stack.yml --set-env MEGAPROMOOZ_TAG="$TAG"
