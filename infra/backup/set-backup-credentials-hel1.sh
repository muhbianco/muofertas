#!/usr/bin/env bash
# Instala as credenciais do destino de backup (Backblaze B2, API S3-compatível) e a senha de
# cifragem dos dumps. Roda **na hel1**, como root, e tudo é digitado ali — nada passa por chat,
# commit ou log:
#
#   bash infra/backup/set-backup-credentials-hel1.sh
#
# **Arquivo próprio, e não o `/root/.megapromooz-stack.env`.** Aquele vira a Env da stack: o que está lá é
# visível aos contêineres da MegaPromoOz. Uma chave de backup dentro do serviço da MegaPromoOz significa que
# quem comprometer o serviço apaga os backups — que é justamente o cenário em que eles existiriam
# para salvar. O job de backup lê este arquivo; os serviços da stack nunca.
#
# Antes de rodar, na conta do Backblaze:
#
# - Crie uma **app key** (a Master Application Key não funciona na API S3) **restrita ao bucket**
#   da MegaPromoOz. Chave que enxerga a conta inteira transforma um vazamento num problema maior do que
#   precisava ser.
# - Considere ligar Object Lock ou versionamento no bucket. Sem isso, um `delete` com a chave em
#   mãos apaga o histórico todo, e backup que o atacante apaga não é backup.
#
# ⚠️ **A senha de cifragem precisa existir fora da hel1 também.** Ela fica aqui para o job
# conseguir cifrar sozinho, mas se o servidor morrer — o cenário do backup — e a senha só
# existir nele, os dumps viram lixo cifrado. Guarde uma cópia no seu gerenciador de senhas antes
# de terminar.
#
# **Nenhum valor é impresso**: a saída lista nomes de chave e um hash curto de cada uma.
set -euo pipefail

ENV_BACKUP="${ENV_BACKUP:-/root/.megapromooz-backup.env}"
[ -t 0 ] || { echo "rode num terminal: as credenciais são digitadas, não canalizadas" >&2; exit 2; }

hash_curto() { printf '%s' "$1" | sha256sum | cut -c1-8; }

ler_segredo() {
  local rotulo="$1" destino="$2" valor
  read -rs -p "$rotulo: " valor
  echo
  [ -n "$valor" ] || { echo "vazio; abortado" >&2; exit 2; }
  printf -v "$destino" '%s' "$valor"
}

ler_texto() {
  local rotulo="$1" destino="$2" valor
  read -rp "$rotulo: " valor
  [ -n "$valor" ] || { echo "vazio; abortado" >&2; exit 2; }
  printf -v "$destino" '%s' "$valor"
}

echo "Backblaze B2 (API S3-compatível)"
ler_texto   "  Endpoint S3 (ex.: s3.us-west-004.backblazeb2.com)" ENDPOINT
ler_texto   "  Nome do bucket" BUCKET
ler_texto   "  keyID (vira o access key id)" KEY_ID
ler_segredo "  applicationKey (vira o secret access key)" APP_KEY
echo
echo "Cifragem dos dumps"
ler_segredo "  Senha (guarde uma cópia FORA da hel1)" PASSPHRASE
ler_segredo "  Repita a senha" PASSPHRASE2

[ "$PASSPHRASE" = "$PASSPHRASE2" ] || { echo "as senhas não conferem; abortado" >&2; exit 2; }

# O endpoint pode vir colado do painel com esquema e barra; normaliza antes de derivar a região.
ENDPOINT="${ENDPOINT#https://}"
ENDPOINT="${ENDPOINT#http://}"
ENDPOINT="${ENDPOINT%/}"

# A região é o segundo segmento do endpoint. Derivar em vez de perguntar evita o erro silencioso
# de digitar uma região que não combina com o host — a assinatura v4 falha com mensagem obscura.
case "$ENDPOINT" in
  s3.*.backblazeb2.com) ;;
  *) echo "endpoint fora do formato s3.<regiao>.backblazeb2.com; abortado" >&2; exit 2 ;;
esac
REGIAO="${ENDPOINT#s3.}"
REGIAO="${REGIAO%.backblazeb2.com}"
[ -n "$REGIAO" ] || { echo "não consegui extrair a região de $ENDPOINT; abortado" >&2; exit 2; }

umask 077
NOVO="$(mktemp)"
trap 'rm -f "$NOVO"' EXIT
{
  echo "MEGAPROMOOZ_BACKUP_ENDPOINT=https://${ENDPOINT}"
  echo "MEGAPROMOOZ_BACKUP_REGION=${REGIAO}"
  echo "MEGAPROMOOZ_BACKUP_BUCKET=${BUCKET}"
  echo "MEGAPROMOOZ_BACKUP_KEY_ID=${KEY_ID}"
  echo "MEGAPROMOOZ_BACKUP_APP_KEY=${APP_KEY}"
  echo "MEGAPROMOOZ_BACKUP_PASSPHRASE=${PASSPHRASE}"
} >"$NOVO"
cat "$NOVO" >"$ENV_BACKUP"
chmod 600 "$ENV_BACKUP"

echo
echo "endpoint      https://${ENDPOINT}  (região ${REGIAO})"
echo "bucket        ${BUCKET}"
echo "key id        sha256:$(hash_curto "$KEY_ID")"
echo "app key       sha256:$(hash_curto "$APP_KEY")"
echo "senha         sha256:$(hash_curto "$PASSPHRASE")"
echo "$ENV_BACKUP: $(sed 's/=.*//' "$ENV_BACKUP" | paste -sd, -)"
echo
echo "guardou a senha fora da hel1? sem ela os dumps não voltam."
