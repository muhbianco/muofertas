# Escolha dos 3 grupos com dados do ML (varredura de 10/10/2026)

## Método
- **Script:** `probes/ml_niche_scan.py`, com o token do app. Foram 1.007 chamadas, cerca de 3/s,
  sem nenhum 429.
- **O que foi medido:** em cada categoria e subcategoria, os mais vendidos que são **produto de
  catálogo**, os únicos com preço pela API. Para cada um, as ofertas de `/products/{id}/items`.
- **Indicadores:**
  - cobertura de catálogo no top 20 (o que o motor consegue vigiar);
  - ticket mediano;
  - comissão por pedido pela tabela oficial (16% beleza/calçados/esportes, 12% ferramentas,
    casa etc., 5% eletrônicos);
  - % de produtos com loja oficial;
  - ofertas por produto;
  - **dispersão de preço** (quanto a oferta mediana fica acima da mais barata, ou seja, espaço
    para achar oferta boa);
  - % com frete grátis.
- **Limites:** é um retrato de um dia e de 5 a 10 produtos por categoria, sem histórico nem
  conversão. **Confiança: média.** Dados brutos em `20261010T160503Z/`.

## O que os dados mostraram

| Nicho (subcategorias) | Catálogo no top 20 | Ticket mediano | Comissão/pedido | Ofertas/produto | Dispersão | Frete grátis | Loja oficial |
|---|---|---|---|---|---|---|---|
| **Ferramentas** (elétricas, pneumáticas, caixas) | 10–15 | R$ 79–138 | R$ 9,5–16,5 | **38–46** | **43–141%** | **76–97%** | 80–100% |
| **Beleza** (perfumes, pele, aparelhos de cabelo, barbearia, maquiagem) | 11–20 | R$ 30–99 | R$ 4,8–15,8 | 10–49 | 29–67% | 54–97% | 60–100% |
| **Tênis e esporte** (tênis esportivos, monitores/smartwatch, ciclismo) | 15–20 | **R$ 54–227** | **R$ 8,7–36** | 4–43 | 14–26% | **95–99%** | 80–100% |
| Casa (cozinha, cuidado da casa) | 19 | R$ 27–62 | R$ 3,3–7,4 | 4–38 | 34–95% | 49–87% | 80–100% |
| Brinquedos (de montar, ar livre) | 13–14 | R$ 103–430 | R$ 12–52 | 1–91 | 0–86% | 97–100% | 60–100% |
| Moda / calçados (Calçados, Roupas e Bolsas) | **1–11** | R$ 30–59 | R$ 4,8–9,5 | 1–20 | 2–34% | 5–50% | 40–100% |

## Ranking e recomendação

| # | Grupo | Por que entra | Risco principal |
|---|---|---|---|
| 1 | **Ferramentas** | A melhor dinâmica de oferta da amostra: muitas ofertas por produto, dispersão de preço altíssima (elétricas 141%), frete grátis em 94–97% e loja oficial. Pouca falsificação; produto com modelo padronizado (bom para histórico) | Comissão de 12%; público mais nichado |
| 2 | **Beleza: maquiagem, perfumes e cuidados** | 16%, recompra alta e a demanda mais comprovada (§5 do levantamento). Aparelhos de cabelo e barbearia têm ticket e disputa altos (49 ofertas/produto). Agrupar maquiagem com perfume, pele e cabelo, como você propôs, aumenta o volume de boas ofertas | Maquiagem sozinha tem ticket baixo (R$ 30); perfume exige o portão de autenticidade ("contratipo") |
| 3 | **Tênis e esporte** | 16%, ticket alto (tênis ~R$ 180, smartwatch ~R$ 227), catálogo cobre bem (15–20 de 20) e quase tudo com frete grátis e loja oficial. **O tênis entra por Esportes**, onde há catálogo (Olympikus etc.), e não por Calçados (moda), onde só 3 de 20 são catálogo | Dispersão menor (menos "achado"); falsificação de marca famosa: portão de loja oficial |

**Ficaram de fora como grupo, mas seguem em observação** (o coletor vigia, sem publicar):
- **Casa:** comissão por pedido baixa.
- **Brinquedos:** forte no Natal; pode virar grupo sazonal em dezembro.
- **Games e celulares:** público enorme de caça-oferta, mas celular paga 5% e é o nicho mais
  disputado entre os canais de oferta.

**Moda/calçados como grupo:** fora. Quase nada é catálogo, então o motor não enxerga o preço.

## Experimento que confirma (ou troca) o 3º grupo
- O coletor vigia os quatro conjuntos (os três grupos e a observação) desde o primeiro deploy.
- Depois de **14 dias** de histórico, comparar por categoria:
  - quedas reais de preço por dia (desconto contra a mediana de 30 dias);
  - % de quedas em loja oficial;
  - comissão esperada por queda.
- Se Casa, Brinquedos ou Games renderem claramente mais "quedas reais" por dia do que Tênis e
  esporte, troca-se o 3º grupo antes de investir em audiência nele.
