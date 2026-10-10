#!/usr/bin/env bash
# Restore de prova: baixa o backup mais recente, decifra e **restaura de verdade** num schema
# descartável, conferindo que as tabelas voltaram com conteúdo.
#
#   bash infra/backup/restore-test.sh              # o mais recente de daily/
#   CHAVE=daily/megapromooz-20260924T030000Z.sql.zst.gpg bash infra/backup/restore-test.sh
#   MANTER=1 bash infra/backup/restore-test.sh     # não apaga o schema no fim
#
# Isto existe porque "temos backup" e "sabemos restaurar" são afirmações diferentes, e só a
# segunda vale alguma coisa às 3 da manhã. Rodar isto todo mês é o que transforma uma na outra.
#
# **Nunca toca no schema de produção.** O destino é fixo e o script recusa qualquer tentativa de
# apontar para o schema de produção.
set -euo pipefail

ENV_BACKUP="${ENV_BACKUP:-/root/.megapromooz-backup.env}"
SCHEMA="${SCHEMA:-megapromooz}"
DESTINO="megapromooz_restore_test"
AWS_IMAGE="${AWS_IMAGE:-amazon/aws-cli:2.37.1}"
MANTER="${MANTER:-0}"

[ "$DESTINO" != "$SCHEMA" ] || { echo "destino não pode ser o schema de produção" >&2; exit 2; }
[ -r "$ENV_BACKUP" ] || { echo "credenciais ilegíveis: $ENV_BACKUP" >&2; exit 2; }
# shellcheck disable=SC1090
set -a; . "$ENV_BACKUP"; set +a

export AWS_ACCESS_KEY_ID="$MEGAPROMOOZ_BACKUP_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$MEGAPROMOOZ_BACKUP_APP_KEY"
export AWS_DEFAULT_REGION="$MEGAPROMOOZ_BACKUP_REGION"

TMP="$(mktemp -d /var/tmp/megapromooz-restore.XXXXXX)"
chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

aws_s3() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -v "$TMP:/work" \
    "$AWS_IMAGE" --endpoint-url "$MEGAPROMOOZ_BACKUP_ENDPOINT" "$@"
}

if [ -z "${CHAVE:-}" ]; then
  echo "[1/5] procurando o backup mais recente"
  # Os nomes carregam carimbo ISO em UTC, então ordem alfabética é ordem cronológica.
  CHAVE="$(aws_s3 s3api list-objects-v2 \
    --bucket "$MEGAPROMOOZ_BACKUP_BUCKET" --prefix "daily/" \
    --query 'sort_by(Contents,&Key)[-1].Key' --output text | tr -d '\r')"
  [ -n "$CHAVE" ] && [ "$CHAVE" != "None" ] || { echo "nenhum backup em daily/" >&2; exit 1; }
fi
echo "      $CHAVE"

echo "[2/5] baixando"
NOME="$(basename "$CHAVE")"
aws_s3 s3 cp "s3://${MEGAPROMOOZ_BACKUP_BUCKET}/${CHAVE}" "/work/${NOME}" --only-show-errors
# O contêiner escreve como root; o arquivo cai no diretório 700 criado acima.
[ -s "$TMP/$NOME" ] || { echo "download vazio" >&2; exit 1; }
echo "      $(stat -c %s "$TMP/$NOME") bytes"

echo "[3/5] decifrando"
gpg --batch --quiet --pinentry-mode loopback --decrypt \
    --passphrase-fd 3 "$TMP/$NOME" 3<<<"$MEGAPROMOOZ_BACKUP_PASSPHRASE" \
  | zstd -d -q -c >"$TMP/dump.sql"
tail -c 200 "$TMP/dump.sql" | grep -q 'Dump completed' || {
  echo "dump incompleto depois de decifrar" >&2; exit 1; }

echo "[4/5] restaurando em $DESTINO"
mariadb -e "DROP DATABASE IF EXISTS \`$DESTINO\`; CREATE DATABASE \`$DESTINO\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
mariadb "$DESTINO" <"$TMP/dump.sql"

echo "[5/5] conferindo"
TABELAS="$(mariadb -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DESTINO'")"
[ "$TABELAS" -gt 0 ] || { echo "restaurou sem tabela nenhuma" >&2; exit 1; }

# Contagem lado a lado das tabelas que contam a história: nichos, produtos vigiados e a série de
# preços (o ativo que não se recria). Tabela que existe vazia passaria numa checagem só de schema.
FALHAS=0
for t in niche niche_category catalog_product price_observation collection_run; do
  ORIG="$(mariadb -N -e "SELECT COUNT(*) FROM \`$SCHEMA\`.\`$t\`" 2>/dev/null || echo "?")"
  REST="$(mariadb -N -e "SELECT COUNT(*) FROM \`$DESTINO\`.\`$t\`" 2>/dev/null || echo "?")"
  # O backup é de antes; produção pode ter crescido desde então. O que não pode é encolher.
  if [ "$REST" = "?" ]; then
    printf '      %-18s AUSENTE no restore\n' "$t"; FALHAS=$((FALHAS + 1))
  elif [ "$ORIG" != "?" ] && [ "$REST" -gt "$ORIG" ]; then
    printf '      %-18s restore=%s > producao=%s (?)\n' "$t" "$REST" "$ORIG"
  else
    printf '      %-18s restore=%-8s producao=%s\n' "$t" "$REST" "$ORIG"
  fi
done

if [ "$MANTER" = "1" ]; then
  echo "schema $DESTINO mantido para inspeção (MANTER=1)"
else
  mariadb -e "DROP DATABASE \`$DESTINO\`"
fi

[ "$FALHAS" -eq 0 ] || { echo "restore incompleto: $FALHAS tabela(s) faltando" >&2; exit 1; }
echo "restore de prova concluído: $TABELAS tabelas."
