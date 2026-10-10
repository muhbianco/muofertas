# Pesquisa de mercado técnico e matriz de integrações

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 2. Pesquisa de mercado técnico

### 2.1 Mercado Livre — "Afiliados e Criadores" (dono já aprovado)
- **Programa** [OFICIAL — ajuda 30228, 27913, 30088]
  - Comissão: tabela da §8.1. Janela de 24 h.
  - Só contam as **mídias declaradas antes** de divulgar.
  - Grupos de WhatsApp/Telegram são reconhecidos. A categoria "Divulgador de Ofertas" (média
    > R$ 50 mil/mês em vendas por 90 dias) recebe tabela própria, enviada pelo ML.
  - **Vedado:**
    - encurtar ou modificar o link (1.8);
    - software client-side ou extensão para distribuir link (1.9);
    - scraping (4.3c);
    - disparo massivo de mensagens (7.3);
    - publicidade comparativa (5.3);
    - citar informação que não está no anúncio (5.4);
    - falsificados (11.1).
  - Exigido: identificar como publicidade (5.1).
  - Quem é **vendedor no ML não pode ser afiliado**.
- **Link e atribuição**
  - Gerador do portal, barra de afiliados ou ID do produto. **Não há API de afiliado** (1.7 diz
    que o ML *pode* oferecer uma um dia).
  - Etiquetas de até 30 caracteres, não apagáveis, com métricas a cada 3 h.
- **Cupons de afiliado** (aba da Central, set/2026)
  - Bancados pelo vendedor e atrelados ao link [COMUNIDADE].
  - Comissão de 3–5% em venda com cupom [COMUNIDADE].
  - Campanhas de vendedor com comissão extra estão na seção 13 dos termos [OFICIAL].
- **API de desenvolvedor** [OFICIAL — developers.mercadolivre.com.br]
  - OAuth: token de 6 h, refresh de 6 meses, **1 app por conta** no Brasil.
  - **Busca por palavra-chave fechada:** 403 sem token em 10/10/2026 e relatos de 403 com token
    [COMUNIDADE]; a doc de 31/08/2026 só documenta busca por vendedor.
  - **Úteis com token** (se funcionam para app de não-vendedor é [TESTAR]):
    - `/highlights/MLB/category/{id}`: top 20;
    - `/trends/MLB`: até 50 termos, semanal;
    - `/items/{id}/sale_price` (`amount`, `regular_amount`);
    - `domain_discovery` (preditor de categoria).
  - `/items?ids=` está sendo descontinuado em favor de `/items/bulk` (migrar até 25/10/2026).
  - Rate limit não publicado (429 + backoff).
  - **Termos:**
    - 2(f) e 7.2/7.3: não repassar dados a terceiros;
    - 5.3: anúncios atualizados a cada 6 h;
    - 5.4: estatística derivada só com autorização;
    - 7.6: sem robôs de scraping.

### 2.2 Shopee — Programa de Afiliados + Affiliate Open API (dono já aprovado, com API)
- **Programa** [OFICIAL — termos de 09/09/2026 e central de ajuda]
  - Pode divulgar **inclusive em WhatsApp e Telegram**.
  - Comissão e pagamento: §8.1. Atribuição: 7 dias, último clique.
  - **Vedado:**
    - marca, nome ou logo da Shopee na mídia do afiliado (2.3a);
    - scraping (4.3d);
    - falsificados (8.2c-iv);
    - divulgar cupom nominal de outro afiliado (13.1.1);
    - anúncio em buscador com a marca.
  - Ponto de atenção: para "sites/apps de ofertas e cupons" a comissão é **"sob consulta"**.
- **Open API (GraphQL)** [OFICIAL — explorer da Open API]
  - Assinatura: SHA-256 de AppId + timestamp + payload + secret.
  - **Queries:**
    - `productOfferV2` (filtros `listType`/`sortType`/categoria/loja/item);
    - `shopOfferV2` (comissão da loja, `remainingBudget`);
    - `shopeeOfferV2` (campanhas com janela);
    - `listItemFeeds`/`getItemFeedData` (feeds diários **FULL/DELTA**, 500 linhas por página);
    - `conversionReport`/`validatedReport`.
  - **Mutations:** `generateShortLink` e `generateBatchShortLink` (50 por chamada), com até
    **5 subIds** que voltam em `utmContent`.
  - **Campos do produto:** preço, mínimo e máximo, `priceDiscountRate`, vendas, nota, comissões
    (total/Shopee/vendedor, app/web, cliente novo/existente), imagem, links, janela.
  - **Não há preço original nem voucher.**
  - Relatório de até 3 meses. Pedido aparece em até 24 h.
  - Rate limit não publicado (erro 10030) → [TESTAR].
- **Shopee Open Platform** é do lado do vendedor; não serve para afiliado.

### 2.3 AliExpress — Portals + Affiliate API (ainda não aprovado)
- **Programa**
  - "Até 9%" base e "até 90%" em hot products; **grupos de chat aceitos**; aprovação em ~2 dias
    úteis [FORNECEDOR].
  - Tabela por categoria, cookie e pagamento: não verificados.
  - Acordo [OFICIAL]:
    - 1.7: clique ou venda por script/robô = fraude;
    - 8.2: limita "obra derivada" do conteúdo;
    - 6.2(b)(ii): cumprir a lei de mensagens não solicitadas.
- **API** [OFICIAL — openservice.aliexpress.com; parte das páginas é de 2022]
  - Onboarding: Portals → conta de desenvolvedor → perfil Affiliate → app → "Apply Online".
  - **Métodos:** `product.query`/`hotproduct.query`/`featuredpromo.*`/`productdetail.get`/
    `link.generate` (`tracking_id`, 50 por chamada)/`order.list`.
  - **Parâmetros e campos:** `ship_to_country=BR`, `target_currency=BRL`, `promo_code_info`,
    **`tax_rate`**, `custom_parameters` no pedido. Rate limit não encontrado.
- **Impostos** [OFICIAL — página da Receita atualizada em 14/09/2026]
  - Remessa Conforme até US$ 50: **Imposto de Importação de 0%**; acima: 60% − US$ 30.
  - ICMS de 17% a 20% "por dentro", conforme o estado.
  - O ato legal da mudança não foi localizado. A imprensa cita a MP 1.357/2026 [COMUNIDADE].
  - Se o preço da API já inclui imposto: **[TESTAR]** (o campo `tax_rate` sugere que não).

### 2.4 Outros programas e redes
- **Amazon Associados BR** [OFICIAL] — **fora**:
  - as redes aceitas não incluem WhatsApp, e grupo fechado é vetado;
  - a PA-API 5 foi descontinuada e trocada pela Creators API.
- **Magalu (Influenciador Magalu)** [FORNECEDOR]:
  - aceita apps de mensagem;
  - comissão de 1% a 12% (Ferramentas 8–12%);
  - API não verificada → Fase 5.
- **Redes (Awin, Admitad, Rakuten, Lomadee)** [OFICIAL/COMUNIDADE]:
  - o cupom da rede só vale com o link da rede, o que conflita com o link nativo;
  - o ML não está em rede nenhuma;
  - a Lomadee está ativa (API de 60 req/min), sem cobertura de ML, Shopee ou AliExpress
    encontrada → **fora do MVP**.

### 2.5 Cupons — o que dá para verificar

| Fonte | Automático | Exige checagem manual |
|---|---|---|
| AliExpress `promo_code_info` | Validade, gasto mínimo, quantidade emitida | Restrição a usuário novo, esgotamento, combinação |
| Shopee | Só a janela das campanhas (`shopeeOfferV2`) | Todo cupom (resgate limitado, app-only) |
| ML | Nada (sem API) | Todo cupom, pela Central de Afiliados |
| Redes (Awin/Admitad) | Status, datas, termos | Elegibilidade; conflito com link nativo |

Nenhuma API simula o checkout. Testar cupom no carrinho com conta automatizada viola Shopee
4.3(d) e AliExpress 1.7. A prova de que um cupom funcionou chega **depois**, pelo relatório de
conversão.

Para ir além das APIs, o **caçador de cupons em background** (§4.5) usa:
- caixa de e-mail própria;
- canais oficiais seguidos;
- scraping educado de terceiros que permitam.

Shopee e ML ficam fora do scraping.

### 2.6 Histórico de preço, tendências e concorrência
- **Histórico**
  - Keepa: só Amazon.
  - Zoom/Buscapé: sem API pública conhecida; o robots.txt bloqueia bots.
  - Ferramentas de mercado (Real Trends R$ 115–445/mês; Nubimetrics e JoomPulse sob consulta)
    são **para vendedor** [FORNECEDOR].
  - → **série própria** a partir das APIs (§4.4).
- **Tendências**
  - ML `trends` (semanal) e `highlights` [OFICIAL].
  - Google Trends API em **alpha**, por formulário, sem quota publicada [OFICIAL].
  - pytrends foi arquivado em 17/04/2025; não usar.
  - TikTok Creative Center "Top Products": login manual; cobertura do Brasil não confirmada.
  - Fase 1 usa ML `trends`/`highlights` e a lista `TOP_PERFORMING` da Shopee. O resto, só se
    medirmos ganho.
- **Comunidades (Promobit, Pelando, Gatry)**
  - Sem API nem RSS.
  - O Promobit veda crawlers e bots expressamente, e o Pelando veda uso não autorizado do
    conteúdo [OFICIAL] → **não raspar** (§4.8).

### 2.7 WhatsApp e regras brasileiras
- **WhatsApp:** detalhe na §9.
- **Regras com efeito no produto** [OFICIAL]:
  - Guia CONAR 2026: afiliados citados; identificação em primeiro plano.
  - CDC arts. 30, 31, 36, 37 e 38.
  - Decreto 7.962/2013 e Decreto 5.903/2006: preço à vista, moeda estrangeira convertida.
  - Recomendação SEDCON–Procon/RJ de 11/11/2025: "de/por" com registro documental.
  - LGPD: controlador, base legal, canal de contato.
  - LPI art. 190: "oferecer ou expor à venda" falsificado.

---

## 3. Matriz de integrações

| Serviço | Finalidade | Dados | Custo conhecido | Requisito de acesso | Limitações | Alternativa | Situação |
|---|---|---|---|---|---|---|---|
| Shopee Affiliate Open API | Descoberta, preço, comissão, link, atribuição | `productOfferV2`, feeds FULL/DELTA, `shopeeOfferV2`, short link com subIds, `conversionReport` | Nenhum publicado | Afiliado + pedido de API | Sem voucher e sem preço original; limite não publicado; taxa "sob consulta" para canal de ofertas | Painel manual | **Aprovado** → sondar |
| Painel/relatórios Shopee | Cliques por sub_id | CSV | R$ 0 | Afiliado | Existência por sub_id [TESTAR] | — | Testar |
| ML Afiliados (portal) | Link, etiquetas, métricas, cupons de afiliado | Cliques/vendas/ganhos por etiqueta (3 h) | R$ 0 | **Aprovado**; declarar mídias | Sem API; link manual; sem encurtar | — | Aprovado → declarar grupos |
| ML API de desenvolvedor | Verificar item/preço; top vendidos; tendências | `sale_price`, `highlights`, `trends`, `domain_discovery`, `/items/bulk` | R$ 0 | App OAuth (1 por conta) | Sem busca por palavra; termos limitam repasse e estatística; limite não publicado | Lista vigiada manual | Criar app → testar |
| AliExpress Portals + API | Descoberta, preço BRL, comissão, cupom, link, pedidos | `product.query`, `hotproduct`, `featuredpromo`, `link.generate`, `order.list` | Nenhum publicado | Portals + app aprovados | Imposto/preço final; falsificação nos nichos; docs antigas | — | **Pedir já** |
| WuzAPI (`muconnect`) | Publicar em grupos/canal; pareamento | Envio, admin de grupo, webhooks | Infra existente | Interna | Não oficial; termos; sessão de 14 dias | Manual / Telegram | Existe → sessão nova |
| E-mail de alerta (webhook n8n próprio) | Alertas críticos | — | R$ 0 | Workflow novo no n8n | Mais lento que push | Web Push (PWA do painel), Fase 2+ | Criar workflow |
| Caixa de e-mail dedicada (IMAP) | Caçador de cupons: newsletters de marketplaces e lojas oficiais | Cupons e campanhas anunciados | R$ 0 (cotar se for caixa em domínio próprio) | Conta nova, só para isso | Cupom pessoal não serve para o grupo; formato muda | — | Criar caixa e assinar |
| Canais oficiais no WhatsApp (seguidos pelo número) | Caçador de cupons | Posts oficiais de marcas e marketplaces | R$ 0 | Seguir como usuário | Evento de newsletter pela WuzAPI [TESTAR] | E-mail | Testar |
| Scraping educado (páginas públicas de terceiros) | Caçador de cupons | Cupons/campanhas de lojas e marcas | R$ 0 + manutenção | Termos e robots.txt conferidos por fonte; dono liga a fonte | Quebra quando o HTML muda; nada atrás de login ou antirrobô | APIs/e-mail | Por fonte |
| WhatsApp Cloud API (Groups) | — | — | Por mensagem × participante | OBA | **8 participantes** | — | Inviável |
| Google Trends API | Tendência de busca | Séries de 5 anos | Não publicado | Formulário alpha | Acesso restrito | SerpApi (US$ 25–150/mês) | Opcional; pedir alpha |
| TikTok Creative Center | Tendência (moda/beleza) | Ranking de produtos | R$ 0 | Login manual | Brasil não confirmado | — | Manual |
| Awin / Admitad / Rakuten / Lomadee | Cupons de rede | Vouchers | Não publicado | Publisher + adesão ao programa | Só com link da rede; ML ausente | Cupons nativos | Fora do MVP |
| Real Trends / Nubimetrics / JoomPulse | Inteligência de mercado | Demanda/concorrência | R$ 115–445/mês (Real Trends); outros sob consulta | Conta de vendedor | Feitas para vendedor | Série própria | Fora do MVP |
| Pelando / Promobit / Gatry | Benchmark | — | — | Uso humano | Termos vetam bots/uso | Seguir canais como usuário (§4.8) | Manual |
| Receita Federal (página de impostos) | Regra de imposto AliExpress | Alíquotas | R$ 0 | Pública | Muda sem aviso | — | Revisar todo mês |
