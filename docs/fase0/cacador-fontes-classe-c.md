# Caçador de cupons — fontes candidatas de classe C (sondagem de 10/10/2026)

Método:
- leitura do robots.txt e dos termos de cada domínio, só com GET público, sem login e sem
  contornar bloqueio;
- rótulos: [OFICIAL], [FORNECEDOR] (texto do próprio site), [COMUNIDADE], [NÃO VERIFICADO].

## Achado que muda a estratégia
**Cupom publicado no site da marca só vale na loja da própria marca.**
- Está escrito assim em Océane, Sephora, Ferramentas Kennedy, Tramontina e Bosch [FORNECEDOR].
- A Tramontina exclui marketplaces de forma explícita.
- Nenhuma marca publica, no próprio site, cupom para a loja oficial dela no ML ou na Shopee. Esses
  cupons ficam dentro do marketplace, onde não raspamos.

Para cupons de ML e Shopee sobram três caminhos:
1. blogs que noticiam cupom da plataforma e têm RSS;
2. APIs oficiais de afiliado (classe A);
3. canais e caixa de e-mail próprios (classe B).

## Candidatas

| # | Fonte | Robots / termos | Serve para | Recomendação |
|---|---|---|---|---|
| 1 | Passageiro de Primeira, RSS `https://passageirodeprimeira.com/feed/` | `/feed/` e posts liberados. Os termos só falam de comentários | Cupons gerais do ML (ex. 10/10: SITE10/40/150 com mínimo), às vezes Shopee e Amazon | **Liberar com condições**: só o RSS, no máximo 1 requisição a cada 30–60 min, guardar só fatos (código, valor, mínimo, validade, link de origem), sem copiar texto. Não usar `/destaques-json-api/` |
| 2 | Melhores Cartões, RSS `https://www.melhorescartoes.com.br/feed` | `Disallow:` vazio; não há página de termos (404) | ML, Shopee, Amazon, Magalu | **Liberar com condições** depois que uma pessoa confirmar que não há termos em outra URL |
| 3 | Cupom Válido `/desconto/mercado-livre/` (e `/shopee/`, `/aliexpress/`) | `Allow: /`; não há termos (404) | Os códigos estão no HTML (`onclick`). **Nunca seguir o link de saída**: é o link de afiliado deles | Precisa leitura humana. Não ter termos não é licença |
| 4 | Adoro Cupom `/cupons-de-desconto/mercado-livre/` | Bloqueia `/out/`; não há termos | ML/Shopee; o texto parece modelo pronto | Precisa leitura humana |
| — | Bosch Friday, Tramontina, Océane, Sephora, Kennedy, Avon, WAP | Permitem, mas o cupom só vale no site da marca | Só como **calendário de campanha** (Bosch Friday, T Friday em nov/2026) | Não liberar como fonte de cupom |

**Vetadas pelos termos ou com bloqueio antirrobô.** Nenhuma destas será usada.

| Fonte | Motivo |
|---|---|
| Cuponomia | Termos vedam reproduzir os cupons |
| Méliuz | 6.4(a) veda cópia e obra derivada |
| Picodi | Termos e robots bloqueiam `/br/cupom/*` |
| Cuponation | Termos vedam reprodução |
| Zoom / Buscapé | 10.1(v) proíbe mineração de dados e robôs |
| Cupom Online | Termos proíbem ferramenta automatizada |
| Descontos Top | Termos vedam reprodução |
| Radar Cupom, cupom.org | Bloqueio do Cloudflare |
| Grupo Boticário, Natura, Beleza na Web | Bloqueio Akamai/403 |
| Leroy Merlin | Bloqueio DataDome |
| Loja do Mecânico | Bloqueio Radware |
| Promobit, Pelando | Termos vedam bots/uso não autorizado |

## Regras novas para o caçador
- **Código de influenciadora ou de outro afiliado nunca é publicado.**
  - A Shopee veda isso por escrito (§13.1.1).
  - No ML, conferir na política do programa [TESTAR].
  - O parser marca esses códigos como `audience = pessoal de terceiro`.
- **Fonte de blog vale como "INFORMADO", nunca como "CONFIRMADO".** O cupom só aparece em linha
  separada, com a condição escrita.
- **RSS antes de HTML.** Onde houver feed, o coletor lê o feed e não a página.

## Canais (classe B, leitura humana ou número seguidor)
- A Natura tem canal verificado no WhatsApp desde nov/2023 [COMUNIDADE].
- O canal do Boticário (> 350 mil seguidores) traz notícias e tendências, não cupons [COMUNIDADE].
- Instagram das marcas: só leitura humana.
