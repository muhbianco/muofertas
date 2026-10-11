# Jardinagem, Tecnologia e Casa e mimos (11/10/2026)

Pedido do dono:
- jardinagem dentro de Ferramentas;
- grupo de Tecnologia (RAM, placa de vídeo, placa-mãe, celulares, fones e headsets, PS5, Xbox,
  consoles e portáteis);
- grupo "Mimos, casa, cama, mesa e banho".

## Como as categorias foram escolhidas
O coletor só enxerga **produto de catálogo** nos mais vendidos (`/highlights/MLB/category/{id}`).
Por isso cada categoria candidata foi conferida pela API em 11/10/2026: quantos dos 20 mais
vendidos são produto de catálogo. Entrou quem tem 12 ou mais.

| Nicho | Categoria | Catálogo no top 20 |
|---|---|---|
| Ferramentas (jardim) | MLB269932 Ferramentas para Jardim | 19 |
| | MLB445854 Roçadeiras e Aparadores | 15 |
| | MLB269940 Cortadores de Grama | 18 |
| | MLB209017 Pulverizadores Manuais | 18 |
| | MLB411908 Utensílios para Jardinagem | 13 |
| Tecnologia | MLB1051 Celulares e Telefones (vinha da observação) | 18 (varredura de 10/10) |
| | MLB1055 Celulares e Smartphones | 20 |
| | MLB196208 Fones de Ouvido | 19 |
| | MLB1664 Headsets (PC Gaming) | 20 |
| | MLB1144 Games (vinha da observação) | 20 |
| | MLB11172 Consoles | 18 |
| | MLB1694 Memórias RAM | 20 |
| | MLB1658 Placas de Vídeo | 20 |
| | MLB1692 Placas-Mãe | 18 |
| Casa e mimos | MLB1618 Cozinha (vinha da observação) | 19 (10/10) |
| | MLB436280 Cozimento (panelas) | 19 |
| | MLB436289 Louça e Artigos para Servir | 20 |
| | MLB436298 Armazenamento e Organização | 17 |
| | MLB438287 Pequenos Eletrodomésticos: Cozinha | 19 |
| | MLB438286 Pequenos Eletrodomésticos: Casa | 18 |
| | MLB436258 Roupa de Cama | 17 |
| | MLB1611 Travesseiros | 12 |
| | MLB438978 Toalhas e Roupões | 16 |
| | MLB264051 Cuidado da Casa e Lavanderia (vinha da observação) | 19 (10/10) |

**Ficaram de fora** por terem pouco catálogo no top 20 (o coletor não veria o preço):
- Cafeteiras (6);
- Mantas para Sofá (8);
- Velas e Castiçais (9);
- Decoração (9);
- Roupa de Mesa (6).

**Motosserra** ficou de fora de propósito: a venda e o uso dependem de registro no IBAMA.

## Regras por nicho
- **Tecnologia: só loja oficial** (`exigir_loja_oficial`).
  - Celular falso, importação paralela e golpe de placa de vídeo usada são o risco maior do nicho.
  - As grandes lojas do setor vendem como loja oficial no ML.
  - Comissão de 5% (celulares, informática e eletrônicos); games pagam 12%, mas o nicho usa um
    número só.
- **Casa e mimos: sem exigir loja oficial**, como em Ferramentas.
  - Stanley, Le Creuset, Zwilling e Contigo são marcas de alto risco: só entram de loja oficial
    ou de vendedor platina/ouro verde.
  - Comissão de 12%; os eletroportáteis pagam 5%.
- **Ferramentas:** Stihl e Husqvarna entram na lista de alto risco (roçadeira falsa é comum).
- **Observação:** fica só com brinquedos.

## O coletor em 11/10/2026 (24 h, medido no banco de produção)
- **Saúde:**
  - 1.774 rodadas; 3 erros (dois 404 e um 206); um 429 às 11:07 UTC.
  - 18 de 18 envios ok.
- **Volume:**
  - cerca de 230 chamadas/h, ~10% do teto nosso (2.500/h);
  - 380 produtos ativos;
  - 96% na faixa morna (a cada 3 h), 4% na quente (20 min).
- **Mudança de preço:** em ~16 h, 25% dos produtos mudaram de preço; 92 mudaram 2 vezes ou mais.

**O que mudou no código:**
- **429:**
  - Antes o laço inteiro dormia 15 min: parava a coleta, os links e o envio.
  - Como o lease não era renovado, disparava o alerta falso "worker parado".
  - Agora só o que chama o ML pausa, a partir de 2 min e dobrando a cada 429 seguido, até 15 min.
- **Faixa morna:** de 180 para 90 min. Estimativa: ~450 chamadas/h com ~650 produtos.

**O maior limite agora é a cobertura, não a frequência.** O coletor só vê os 20 mais vendidos de
cada categoria. Próximo passo sugerido: descer um nível nas categorias com mais disputa de preço
(Ferramentas Elétricas, Pneumáticas, Cuidado da Casa, Eletroportáteis), medindo os 429 depois de
cada aumento.
