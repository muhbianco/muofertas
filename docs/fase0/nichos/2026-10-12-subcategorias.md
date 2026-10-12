# Um nível abaixo: 65 subcategorias (12/10/2026)

## Por que não "top 30"

O dono pediu os 30 mais vendidos em vez de 20. A API não deixa:
`/highlights/MLB/category/{id}` devolve sempre 20, e `limit` e `offset` são ignorados (testado em
12/10 com `limit=50`, `offset=20` e `limit=30`: os mesmos 20 itens). Cada subcategoria tem o próprio
top 20, então descer um nível é o jeito de ver mais produtos.

## Como foi escolhido

Sondagem em produção, ~0,5 chamada/s, sobre as 89 categorias ativas:

1. filhas de cada categoria em `/categories/{id}`: 503 que ainda não usamos;
2. fora pelo nome: outros, peças, refis, capas, cargas, lâminas; e mamadeira, chupeta, bico, fórmula
   e leite (NBCAL, Lei 11.265/2006);
3. top 20 de cada uma: quantos são produto de catálogo (o único com preço pela API) e quantos ainda
   não temos ("novos únicos" desconta o que outra subcategoria já trouxe).

Ficaram as que trazem 8 ou mais produtos novos e combinam com o grupo. Exceção: "Potes para
Alimentos" (5 novos), porque o mais vendido dela era exemplo do dono. Saíram as de pouco apelo de
oferta, mesmo com muitos produtos novos: refratômetros, roldanas, pôquer, telefonia fixa, VoIP,
fliperama, controles de console antigo.

| Nicho | Subcategorias | Produtos novos (aprox.) |
|---|---:|---:|
| Ferramentas | 16 | 212 |
| Beleza | 14 | 213 |
| Tênis e esporte | 7 | 56 |
| Tecnologia | 6 | 100 |
| Casa e mimos | 12 | 177 |
| Pais, mães e filhos | 11 | 149 |
| **Total** | **66** | **~900** |

Eram 89 categorias e ~1.460 produtos; ficam 155 categorias e ~2.400 produtos (+60%).

## Custo de chamadas

Antes: ~1.250 chamadas/h (quase tudo morno a cada 90 min; nada frio, porque o frio só vem com 7 dias
sem mudança). Com +900 produtos mornos: ~1.900/h na primeira semana, abaixo do teto nosso de 2.500/h.
A primeira descoberta das 65 busca o detalhe de cada produto novo (~900 chamadas) e pode bater o teto
da hora; o worker pausa até a virada (`teto de chamadas da hora atingido`) e segue. Depois de 7 dias
os sem mudança vão para o frio (1x/dia) e o custo cai.

Conferir nas primeiras 48 h: 429 em sequência no log do worker. Se vierem, voltar a faixa morna para
120 min antes de cortar categorias.

## Três exemplos do dono que a busca não resolve

- Controle Redfin (2º em Gamepads e Joysticks de PS4): já estava na base (produto 663), bloqueado
  porque Tecnologia só aceita loja oficial e quem vende é MercadoLíder sem loja oficial.
- Kit 10 potes Rishon (1º em Potes para Alimentos): já estava na base (produto 424), regular com 44%
  OFF no anúncio, perdido no meio da lista "com % OFF" de 900+ itens.
- Tênis Sandrini Aero Run (10º em Calçados > Tênis, loja oficial): não é produto de catálogo. O top
  20 de Calçados > Tênis (MLB23332) tem 2 produtos de catálogo, 13 anúncios e 5 "produto de vendedor";
  a API do app não dá preço dos dois últimos. A categoria entrou mesmo assim (traz os 2 de catálogo).
