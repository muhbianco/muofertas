# Sondagens da Fase 0

Scripts que conferem na prática as premissas marcadas como [TESTAR] no levantamento.

- Usam só a biblioteca padrão do Python (3.11+).
- Leem o segredo de arquivo e nunca o imprimem nem o gravam na saída.
- O resultado saneado vai para `docs/fase0/<provedor>/<carimbo UTC>/`.

## Arquivos de segredo (o dono cria; ninguém lê o conteúdo)

Pasta padrão: `~/.secrets/megapromooz/`. Para usar outra, defina `MPO_SECRETS_DIR`.

| Arquivo | Chaves | De onde vem |
|---|---|---|
| `shopee-affiliate.env` | `SHOPEE_APP_ID`, `SHOPEE_APP_SECRET` | Painel de afiliado Shopee → Open API |
| `ml-app.env` | `ML_CLIENT_ID`, `ML_CLIENT_SECRET`, `ML_REDIRECT_URI` | DevCenter do ML → app novo (ligar PKCE). O `ML_REDIRECT_URI` tem de ser idêntico ao cadastrado |
| `ml-tokens.json` | — | Escrito por `ml_probe.py auth`; não editar |

Formato dos `.env`: `CHAVE=valor`, uma por linha.

## Como rodar

```bash
python -I probes/shopee_probe.py
```

```bash
python -I probes/ml_probe.py auth
```

O `auth` é só para o dono, no terminal dele:
1. Abre o login do ML no navegador.
2. Depois de autorizar, copie o `code` da barra de endereço.
3. Cole o `code` quando o script pedir. Ele não aparece na tela.

```bash
python -I probes/ml_probe.py run
```
