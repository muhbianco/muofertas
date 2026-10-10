#!/usr/bin/env bash
# Grava na hel1 a sessão do portal de afiliados do ML (link curto oficial pelo createLink).
#
#   cd /usr/src/muofertas && git pull --ff-only && bash infra/scripts/set-ml-session-hel1.sh
#
# De onde vêm os valores (Chrome logado no ML, no computador):
#   mercadolivre.com.br/afiliados/linkbuilder → F12 → Rede → gere um link → requisição
#   "createLink" → Headers → Request headers → copie o valor de:
#     Cookie         (inteiro)
#     X-Csrf-Token
# Nada aparece na tela nem vai para log. O arquivo fica 600 em /root/megapromooz-secrets, montado
# só leitura no worker, que o relê a cada link: renovar a sessão não pede deploy.
set -euo pipefail

dir=/root/megapromooz-secrets
file="$dir/ml-session"
[ "$(id -u)" = 0 ] || { echo "rode como root" >&2; exit 2; }
[ -t 0 ] || { echo "rode num terminal interativo: os valores são colados" >&2; exit 2; }
install -d -m 700 "$dir"

saved=$(stty -g)
trap 'stty "$saved"' EXIT
# -icanon: o cookie passa de 4 KB, o limite de linha do modo canônico do terminal.
# -echo: o valor colado não aparece.
stty -icanon -echo
printf 'Cole o valor do Cookie e tecle Enter: '
IFS= read -r cookie
printf '\nCole o valor do X-Csrf-Token e tecle Enter: '
IFS= read -r csrf
stty "$saved"
trap - EXIT
printf '\n'

cookie=${cookie%$'\r'}
csrf=${csrf%$'\r'}
cookie=${cookie#[Cc]ookie: }
csrf=${csrf#[Xx]-[Cc]srf-[Tt]oken: }
[ "${#cookie}" -ge 200 ] || { echo "cookie curto demais (${#cookie} caracteres); nada gravado" >&2; exit 1; }
[ "${#csrf}" -ge 16 ] || { echo "token curto demais; nada gravado" >&2; exit 1; }

tmp=$(mktemp "$dir/.ml-session.XXXXXX")
chmod 600 "$tmp"
printf 'cookie=%s\ncsrf=%s\n' "$cookie" "$csrf" > "$tmp"
mv -f "$tmp" "$file"
echo "gravado: $file (cookie com ${#cookie} caracteres, token com ${#csrf})."
echo "O worker usa no próximo link aprovado. Se a sessão cair, chega o alerta por e-mail."
