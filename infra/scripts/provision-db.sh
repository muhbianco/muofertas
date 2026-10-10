#!/usr/bin/env bash
# Cria (idempotente) o schema e os usuários da MegaPromoOz no MariaDB nativo da hel1.
# Rodar como root na hel1, a partir de um checkout do repo:
#
#   infra/scripts/provision-db.sh
#
# As senhas são geradas aqui e gravadas em /root/.megapromooz.env (modo 600). Nada é impresso
# além de nomes de chave. Se o arquivo já existir, as senhas dele são reaproveitadas (rodar de
# novo não troca senha nem derruba quem já está conectado).
set -euo pipefail

ENV_FILE="${ENV_FILE:-/root/.megapromooz.env}"
umask 077

if [ ! -f "$ENV_FILE" ]; then
  {
    echo "DB_PASSWORD=$(openssl rand -hex 24)"
    echo "MIGRATE_DB_PASSWORD=$(openssl rand -hex 24)"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "criado $ENV_FILE com as chaves: DB_PASSWORD, MIGRATE_DB_PASSWORD"
else
  echo "reaproveitando $ENV_FILE (chaves: $(cut -d= -f1 "$ENV_FILE" | paste -sd, -))"
fi

app_pw="$(sed -n 's/^DB_PASSWORD=//p' "$ENV_FILE")"
migrate_pw="$(sed -n 's/^MIGRATE_DB_PASSWORD=//p' "$ENV_FILE")"
[ -n "$app_pw" ] && [ -n "$migrate_pw" ] || { echo "faltam senhas em $ENV_FILE" >&2; exit 2; }

# SQL pelo stdin (nunca pelo argv, que aparece no ps). root@localhost entra por unix_socket.
mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`megapromooz\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'megapromooz_migrate'@'%' IDENTIFIED BY '${migrate_pw}';
CREATE USER IF NOT EXISTS 'megapromooz_app'@'%' IDENTIFIED BY '${app_pw}';
GRANT ALL PRIVILEGES ON \`megapromooz\`.* TO 'megapromooz_migrate'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`megapromooz\`.* TO 'megapromooz_app'@'%';
FLUSH PRIVILEGES;
SQL
echo "ok: schema megapromooz e usuários megapromooz_migrate, megapromooz_app"
