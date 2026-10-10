#!/usr/bin/env bash
# Instala (ou atualiza) o timer de backup da MegaPromoOz na hel1. Roda como root:
#
#   bash infra/backup/install-backup-hel1.sh
#
# Idempotente: pode rodar de novo a cada deploy que mexer nos units.
#
# Ele **não** instala credencial — isso é o `set-backup-credentials-hel1.sh`. E não define
# retenção: ela é regra de ciclo de vida no bucket, de propósito, para a chave do B2 não precisar
# de permissão de apagar (ver o cabeçalho de `backup.sh`).
set -euo pipefail

REPO="${REPO:-/usr/src/muofertas}"
ENV_BACKUP="${ENV_BACKUP:-/root/.megapromooz-backup.env}"

[ -r "$ENV_BACKUP" ] || { echo "rode set-backup-credentials-hel1.sh antes: $ENV_BACKUP ausente" >&2; exit 2; }
[ -x "$REPO/infra/backup/backup.sh" ] || chmod +x "$REPO/infra/backup/backup.sh"
[ -x "$REPO/infra/backup/restore-test.sh" ] || chmod +x "$REPO/infra/backup/restore-test.sh"

install -m 644 "$REPO/infra/systemd/megapromooz-backup.service" /etc/systemd/system/
install -m 644 "$REPO/infra/systemd/megapromooz-backup.timer"   /etc/systemd/system/
mkdir -p /var/lib/megapromooz-backup

systemctl daemon-reload
systemctl enable --now megapromooz-backup.timer

echo
systemctl list-timers megapromooz-backup.timer --no-pager
echo
echo "primeiro backup à mão:      systemctl start megapromooz-backup.service"
echo "acompanhar:                 journalctl -u megapromooz-backup.service -f"
echo "ensaio sem enviar nada:     DRY_RUN=1 $REPO/infra/backup/backup.sh"
echo "restore de prova (mensal):  $REPO/infra/backup/restore-test.sh"
