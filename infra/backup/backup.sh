#!/usr/bin/env bash
# Backup do schema `megapromooz`: despeja, comprime, cifra, **confere** e envia para fora da hel1.
#
#   bash infra/backup/backup.sh            # o que o timer do systemd chama
#   DRY_RUN=1 bash infra/backup/backup.sh  # faz tudo menos o upload
#
# Credenciais em /root/.megapromooz-backup.env (600), instaladas pelo
# `set-backup-credentials-hel1.sh`. Nenhum valor aparece na saída nem na linha de comando.
#
# Quatro decisões que valem explicação:
#
# 1. **Nunca apaga nada.** A retenção é feita por regra de ciclo de vida no bucket (`daily/` por
#    8 dias, `weekly/` por 35), não por este script. Assim a chave do B2 não precisa de permissão
#    de apagar — e backup que o invasor apaga com a chave que achou no servidor não é backup.
# 2. **Confere antes de confiar.** Depois de cifrar, o arquivo é decifrado e descomprimido de
#    volta e o fim do dump é checado. É o que pega dump truncado, disco cheio no meio e senha
#    errada — três coisas que passam despercebidas até o dia do restore.
# 3. **A senha nunca vai em argv.** `ps` é legível por qualquer processo; a senha entra por
#    descritor de arquivo. As chaves do B2 entram no contêiner por nome de variável, não valor.
# 4. **Dump como root pelo socket Unix.** Não cria mais uma credencial para guardar: o job já roda
#    como root no host, onde o datadir é legível de qualquer jeito.
#
# ⚠️ `--single-transaction` dá um dump consistente sem travar escrita, mas **não** protege contra
# DDL rodando no meio. As migrations só rodam em deploy; se um deploy coincidir com o backup, o
# dump daquela noite pode sair inconsistente. O horário do timer fica longe da janela de deploy.
set -euo pipefail

ENV_BACKUP="${ENV_BACKUP:-/root/.megapromooz-backup.env}"
SCHEMA="${SCHEMA:-megapromooz}"
AWS_IMAGE="${AWS_IMAGE:-amazon/aws-cli:2.37.1}"
ESTADO="${ESTADO:-/var/lib/megapromooz-backup}"
DRY_RUN="${DRY_RUN:-0}"

[ -r "$ENV_BACKUP" ] || { echo "credenciais ilegíveis: $ENV_BACKUP" >&2; exit 2; }
# shellcheck disable=SC1090
set -a; . "$ENV_BACKUP"; set +a

for v in MEGAPROMOOZ_BACKUP_ENDPOINT MEGAPROMOOZ_BACKUP_REGION MEGAPROMOOZ_BACKUP_BUCKET \
         MEGAPROMOOZ_BACKUP_KEY_ID MEGAPROMOOZ_BACKUP_APP_KEY MEGAPROMOOZ_BACKUP_PASSPHRASE; do
  [ -n "${!v:-}" ] || { echo "faltou $v em $ENV_BACKUP" >&2; exit 2; }
done

command -v mariadb-dump >/dev/null || { echo "mariadb-dump não encontrado" >&2; exit 2; }
command -v zstd >/dev/null         || { echo "zstd não encontrado" >&2; exit 2; }
command -v gpg >/dev/null          || { echo "gpg não encontrado" >&2; exit 2; }

CARIMBO="$(date -u +%Y%m%dT%H%M%SZ)"
NOME="${SCHEMA}-${CARIMBO}.sql.zst.gpg"
TMP="$(mktemp -d /var/tmp/megapromooz-backup.XXXXXX)"
chmod 700 "$TMP"
# O dump em claro vive aqui dentro; sair sem apagar deixaria a base inteira legível em /var/tmp.
trap 'rm -rf "$TMP"' EXIT

BRUTO="$TMP/dump.sql"
CIFRADO="$TMP/$NOME"

echo "[1/5] despejando $SCHEMA"
# `nice`/`ionice`: a hel1 é compartilhada com commerce, chatwoot e os builds do Woodpecker.
nice -n 19 ionice -c3 mariadb-dump \
  --single-transaction --hex-blob --routines --events --triggers \
  --default-character-set=utf8mb4 \
  "$SCHEMA" >"$BRUTO"

# mysqldump/mariadb-dump fecham com esta linha. Sem ela, o dump acabou no meio.
tail -c 200 "$BRUTO" | grep -q 'Dump completed' || {
  echo "dump não terminou (sem marca de conclusão)" >&2; exit 1; }
echo "      $(stat -c %s "$BRUTO") bytes"

echo "[2/5] comprimindo e cifrando"
# `--compress-algo none`: o zstd já comprimiu; o gpg comprimir de novo é CPU jogada fora.
nice -n 19 zstd -9 -q -c "$BRUTO" \
  | gpg --batch --yes --quiet --pinentry-mode loopback \
        --symmetric --cipher-algo AES256 --digest-algo SHA512 \
        --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 \
        --compress-algo none \
        --passphrase-fd 3 -o "$CIFRADO" 3<<<"$MEGAPROMOOZ_BACKUP_PASSPHRASE"
TAMANHO="$(stat -c %s "$CIFRADO")"
echo "      $TAMANHO bytes cifrados"

echo "[3/5] conferindo o arquivo cifrado"
# Volta o caminho inteiro. É o que separa "gerou um arquivo" de "gerou um backup".
gpg --batch --quiet --pinentry-mode loopback --decrypt \
    --passphrase-fd 3 "$CIFRADO" 3<<<"$MEGAPROMOOZ_BACKUP_PASSPHRASE" \
  | zstd -d -q -c | tail -c 200 | grep -q 'Dump completed' || {
    echo "o arquivo cifrado não volta íntegro; nada foi enviado" >&2; exit 1; }
echo "      decifra e descomprime, e o dump está completo"

if [ "$DRY_RUN" = "1" ]; then
  echo "[4/5] DRY_RUN=1: upload pulado"
  echo "[5/5] nada a registrar"
  exit 0
fi

echo "[4/5] enviando para o bucket"
# As chaves entram no contêiner **por nome**: com `-e VAR=valor` elas apareceriam em `ps`.
export AWS_ACCESS_KEY_ID="$MEGAPROMOOZ_BACKUP_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$MEGAPROMOOZ_BACKUP_APP_KEY"
export AWS_DEFAULT_REGION="$MEGAPROMOOZ_BACKUP_REGION"

aws_s3() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
    -v "$CIFRADO:/tmp/$NOME:ro" \
    "$AWS_IMAGE" --endpoint-url "$MEGAPROMOOZ_BACKUP_ENDPOINT" "$@"
}

aws_s3 s3 cp "/tmp/$NOME" "s3://${MEGAPROMOOZ_BACKUP_BUCKET}/daily/${NOME}" --only-show-errors

REMOTO="$(aws_s3 s3api head-object \
  --bucket "$MEGAPROMOOZ_BACKUP_BUCKET" --key "daily/${NOME}" \
  --query ContentLength --output text | tr -d '\r')"
[ "$REMOTO" = "$TAMANHO" ] || {
  echo "tamanho no bucket ($REMOTO) difere do local ($TAMANHO)" >&2; exit 1; }
echo "      daily/${NOME} confere ($TAMANHO bytes)"

# Domingo ganha uma cópia no prefixo semanal. Cópia no servidor, sem subir de novo.
if [ "$(date -u +%u)" = "7" ]; then
  aws_s3 s3 cp \
    "s3://${MEGAPROMOOZ_BACKUP_BUCKET}/daily/${NOME}" \
    "s3://${MEGAPROMOOZ_BACKUP_BUCKET}/weekly/${NOME}" --only-show-errors
  echo "      weekly/${NOME} (domingo)"
fi

echo "[5/5] registrando o sucesso"
mkdir -p "$ESTADO"
printf '%s daily/%s %s\n' "$CARIMBO" "$NOME" "$TAMANHO" >"$ESTADO/last-success"
echo "pronto."
