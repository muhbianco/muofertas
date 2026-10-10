# Fase 0 — Registro de viabilidade

Cada premissa [TESTAR] do levantamento ganha um registro com: data, fonte, pedido e resposta
saneados, e decisão. **Código de provedor só depois que o dono aprova o registro.**

Início: 10/10/2026.

## Quadro das premissas

| # | Premissa | Estado | Evidência | Decisão |
|---|---|---|---|---|
| P1 | Formato e assinatura da Shopee Open API | ✅ confirmado na fonte oficial | Código do explorer oficial: `SHA256 Credential={AppId}, Timestamp={ts}, Signature=sha256_hex(AppId+ts+payload+Secret)`, `POST https://open-api.affiliate.shopee.com.br/graphql` | Usar como está |
| P2 | Campos e operações da Shopee (subIds, feeds, relatório) | ✅ schema oficial salvo | [shopee-open-api-schema-2026-10-10.json](shopee-open-api-schema-2026-10-10.json): `productOfferV2` (25 campos, **sem preço original**), `generateShortLink` com `subIds` (5), `getItemFeedData` (≤ 500 linhas, FULL/DELTA), `conversionReport` (`utmContent`, pedidos, itens, `fraudStatus`, `itemNotes`) | Desenho da §6.4 mantido |
| P3 | Shopee na prática: dados reais, limite de chamadas, latência | ⏸️ **Shopee não aprovada** (correção do dono, 10/10/2026) | `probes/shopee_probe.py` pronto para quando houver Open API | Pedir o afiliado Shopee agora; a Open API se pede depois de aprovado |
| P4a | ML, 1ª rodada (10/10/2026, `docs/fase0/ml/20261010T145933Z`) | ❌ **fechado com as permissões mínimas** | **Os dois tokens** (de usuário, por `authorization_code`+PKCE, e do app, por `client_credentials`) dão o mesmo resultado. 200 em `/users/me` e `/categories/{id}`. **403 `At least one policy returned UNAUTHORIZED`** em `/highlights`, `/trends`, `/sites/MLB/domain_discovery` e `/sites/MLB/categories`. App configurado com só "Usuários" em leitura e escrita, e todo o resto "Sem acesso" | Próxima rodada: liberar **leitura** nas permissões que cubram catálogo e métricas, e testar `/items` + `sale_price` com IDs reais (`run <links>`) |
| P4b | ML, 2ª rodada, depois de liberar **Publicação e sincronização** e **Métricas do negócio** (10/10/2026, `docs/fase0/ml/20261010T154014Z`) | ✅ **viável para produto de catálogo** | **Token do app (`client_credentials`):** 200 em `domain_discovery`, `/categories`, `/sites/MLB/categories`, `/highlights` (top 20: ferramentas 6–12 PRODUCT de 20, maquiagem 14 de 20; o resto é USER_PRODUCT ou ITEM), `/trends`, `/products/{id}` (sem preço; `buy_box_winner` vazio) e **`/products/{id}/items`**. Esta última traz **todas as ofertas do produto de catálogo** (17 e 68 no teste), com `price`, `original_price`, `official_store_id`, `shipping.free_shipping`/`cost`, `deal_ids`, `condition`, `seller_id`, `item_id`, garantia. **403:** busca por palavra, `/items/{id}`, `/items/bulk` (403 item a item), `sale_price`, `/user-products/{id}`. Rajada de 10 em `/products/{id}/items`: todas 200, 213–346 ms, sem cabeçalho de cota. O token de usuário segue com os escopos antigos (403); refazer o `auth` só se precisar | **O motor do ML monitora produtos de catálogo (`/p/MLB…`).** Descoberta pelas entradas PRODUCT dos mais vendidos, mais a lista do operador (links `/p/`). Anúncio fora do catálogo: só manual. Produção usa o token do app, sem depender de login. Ver "Ajustes" abaixo |
| P4 | **CAMINHO CRÍTICO (ML primeiro).** O app de não-vendedor lê `highlights` (folha e nível acima), `/categories`, `/items`, `/items/bulk` e `sale_price`? A busca por palavra continua fechada? Quanto do top 20 é ITEM × PRODUCT × USER_PRODUCT? Rajada de 10 chamadas a `sale_price` | ⏳ falta app/OAuth | `probes/ml_probe.py` pronto; rotas conferidas na doc (10/10/2026); OAuth só `authorization_code` (+ PKCE), sem `client_credentials` | Dono cria o app e roda `auth`. Se falhar, o motor do ML vira só lista manual |
| P5 | ML permite publicar automaticamente, em grupos declarados, links gerados no portal (3.1.2) | ⏳ pergunta ao suporte | Rascunho abaixo | Até a resposta, ML só pelo adaptador manual |
| P6 | ML autoriza uso interno da série de preço (termos de dev 5.4) | ⏳ pergunta ao suporte | Rascunho abaixo | Até a resposta, nada de afirmação de histórico em item do ML |
| P7 | Shopee: taxa "sob consulta" para canal de ofertas; limite da API; cliques por sub_id | ⏳ pergunta ao suporte | Rascunho abaixo | — |
| P8 | WuzAPI da `muconnect`: versão e capacidades | ✅ confirmado | Serviço `wuzapi_wuzapi`, imagem `asternic/wuzapi:1.0.9` fixada por digest. Spec da tag `v1.0.9`: grupos (`create`, `list`, `info`, `announce`, `invitelink`, `joinapprovalmode`, `updateparticipants`…), `/chat/send/image` (base64 + `Caption`), `/newsletter/list`. **Não há envio a Canal nem `join` de canal na 1.0.9** (entraram depois da tag) | Canal do WhatsApp fica manual no MVP; atualizar a imagem só com upgrade coordenado (§9.4) |
| P9 | Criar grupo pela API sem participante | ❓ não documentado | O spec diz "with the specified name and participants" | **Criar os grupos no celular**, já com o número co-admin como 1º participante, depois promovê-lo a admin. O painel lista (`/group/list`), liga `announce`, gera o convite e vincula ao nicho |
| P10 | Post de canal seguido chega como evento na WuzAPI | ⏳ teste com o número | A doc lista os eventos `Message`, `ReadReceipt`, `Presence`, `HistorySync`, `ChatPresence` | Testar no pareamento |
| P11 | Imposto de importação até US$ 50 (AliExpress) | ✅ confirmado | **Lei 15.502/2026** (conversão da MP 1.357, de 12/05/2026) e Portaria MF 1.342/2026. Página da Receita atualizada em 14/09/2026: Remessa Conforme até US$ 50 → **II 0%**; acima → 60% − US$ 30; fora do programa → 60%. **ICMS** 17% (maioria, inclusive SP/RJ/MG/PR/RS/SC/DF), 18% AP, 19% PA, 20% AC/AL/BA/CE/PB/PI/RN/RR/SE, calculado "por dentro" (≈ +20,5% a +25% sobre o valor). A lei manda avaliar periodicamente o setor de **cosméticos (NCM cap. 33)**: a regra pode mudar para maquiagem | Preço da AliExpress sempre com ICMS explícito; reler a página da Receita todo mês |
| P12 | Fontes de classe C do caçador | ✅ levantado | [cacador-fontes-classe-c.md](cacador-fontes-classe-c.md). **Cupom de site de marca só vale no site da marca.** Candidatas reais: RSS de Passageiro de Primeira e Melhores Cartões. Vetadas: Cuponomia, Méliuz, Picodi, Cuponation, Zoom/Buscapé e outras | O dono libera as 2 de RSS; Cupom Válido e Adoro Cupom precisam de leitura humana |
| P13 | Domínio e marca MegaPromoOz | 🟡 parcial | RDAP: `megapromooz.com.br` e `megapromooz.com` com 404 (provavelmente livres). Nenhum uso público encontrado. INPI exige busca manual (classe 35; talvez 9 e 42); "mega promo" tende a ser descritivo, então a distintividade está no "Oz" | Dono: registrar o `.com.br` e fazer a busca manual no INPI |
| P14 | Preço da AliExpress com `ship_to_country=BR` inclui imposto? | ⏳ depende da aprovação do AliExpress | — | — |

## Fase 1 — coletor (10/10/2026)
- Código em `app/` com:
  - worker com lease;
  - cliente do ML com ritmo, teto por hora, tentativa só em GET e token com nova tentativa;
  - histórico só na mudança + batimento;
  - camadas quente/morna/fria.
- Também: migration `0001_coletor`, 23 testes, ruff, mypy estrito, CI e stack.
- **Execução real contra o ML** (SQLite local): 20 categorias descobertas, **302 produtos de
  catálogo** (beleza 90, ferramentas 82, tênis e esporte 65, observação 65), 1º lote de 40 preços
  gravado, **364 chamadas, 0 erros**.
- Volume previsto na camada morna (3 h): cerca de 2,4 mil chamadas/dia, muito abaixo do teto
  configurado (2.500/h).

## Decisões do dono (10/10/2026, depois da sondagem)
- **Três grupos iniciais, um nicho cada.**
  - Mais custoso para juntar membros do que um grupo único, mas segmenta melhor.
  - Os três nichos saem da varredura com dados do ML (`probes/ml_niche_scan.py`,
    `docs/fase0/nichos/`).
- **Link do ML:**
  - O gerador da Central ("Gerador de produtos recomendados") aceita **várias URLs de uma vez**,
    uma por linha, com etiqueta (hoje existe `megapromooz`).
  - O link curto que ele devolve (`meli.la`/`/sec/`) é o encurtador **oficial** do ML e vai como
    está. Vedado é encurtar ou alterar por fora (termos 1.8).
  - O painel junta as URLs do dia num bloco para colar no gerador e recebe de volta o bloco de
    links gerados.
  - Criar **uma etiqueta por grupo** (≤ 30 caracteres, minúsculas e números, não apagável) para
    medir por grupo.
- **Histórico de preços do ML: vamos fazer, independente da resposta do suporte (P6).** É uso
  interno, para filtrar desconto falso.
  - Publicar afirmação de histórico em item do ML (ex.: "18% abaixo da mediana") continua sendo
    decisão separada.
  - Até a resposta, a mensagem do ML mostra o preço e o "de/por" **do próprio anúncio** e nada
    calculado por nós.

## Ajustes de desenho que saem da sondagem do ML (10/10/2026)
- **Unidade monitorada no ML = produto de catálogo.** O preço por vendedor vem de
  `/products/{id}/items`.
  - Para cada produto a série guarda: menor preço geral, menor preço de **loja oficial**, número de
    ofertas, frete grátis, `deal_ids` e `original_price` de cada oferta.
  - Esse último é o "de" do vendedor; serve como dado auxiliar, **nunca** como base de desconto.
- **Descoberta:** entradas PRODUCT dos mais vendidos (folha + nível acima), `trends` como pista
  para o operador e lista vigiada aceitando links `/p/MLB…`.
- **USER_PRODUCT e ITEM** (anúncios fora do catálogo) não têm preço pela API: ficam só para
  curadoria manual.
- **Token:** produção usa `client_credentials`. Não depende do login do dono nem do refresh token
  de 6 meses.
- **Escopo a conferir:** o token traz `publish-sync:/read-write`.
  - O dono confere no DevCenter se "Publicação e sincronização" está em **Leitura**.
  - Se o ML só oferecer leitura e escrita, o código nunca chama rota de escrita, e o segredo do
    app fica em arquivo do host (vazamento = alguém poderia publicar na conta).
- **Ritmo:** 10 chamadas seguidas sem 429. O limite real não é publicado, então o coletor começa
  conservador (ex.: 1 chamada/s com teto por hora) e mede.
- **P6 continua valendo.** Usar essa série internamente e publicar afirmação de histórico depende
  da resposta do suporte de desenvolvedores.

## Preço por vendedor elegível (10/10/2026, regras v2)
O dono comparou o painel com a página do ML e achou dois desvios:
- **Galaxy A17:** o painel mostrava R$ 1.499; a página mostrava R$ 1.399.
- **Gift card PSN:** o painel mostrava R$ 70; a página mostrava R$ 125.

**Causa.**
- A v1 usava o menor preço de **qualquer** vendedor, inclusive vendedor sem reputação, sem vendas
  e com "entrega a combinar". No gift card, os R$ 70 vinham de um vendedor com 3 vendas.
- Para marca de alto risco, a v1 só aceitava loja oficial. No Galaxy, isso deu R$ 1.499, a
  oficial, contra R$ 1.299 de um vendedor verde com 3.520 vendas.
- A página ainda mostra o desconto do Pix, que a API não expõe (R$ 1.349,10 na oficial).

**Regra nova.** A ajuda 30088 diz que o programa só paga vendedor amarelo ou verde.
- **Elegível:**
  - produto novo;
  - vendedor `3_yellow`/`4_light_green`/`5_green`;
  - envio pelo ML (`me1`/`me2`);
  - ≥ 50 vendas (`MIN_SELLER_SALES`).
  - Loja oficial é a marca: dispensa as vendas mínimas e o envio pelo ML (gift card não tem
    frete).
- **Confiável:** loja oficial, ou elegível verde com MercadoLíder Gold/Platinum.
- **Três réguas para o preço:**
  - **oficial:** nicho com `exigir_loja_oficial` (beleza, tênis), para conter falsificação;
  - **confiável:** marca de alto risco;
  - **elegível:** o resto.
  - Em todas, a loja oficial é preferida quando custa até 2% a mais.
- O menor preço bruto continua gravado, só como sinal. O painel avisa quando há oferta mais
  barata fora da régua.
- A reputação vem de `/users/bulk` (20 por chamada), com cache de 7 dias em `marketplace_seller`.
- A série da nota usa a mesma régua do preço. As linhas de antes da `0004` só entram na régua
  oficial.
- **Base zerada** a pedido do dono, logo depois do deploy das regras v2. O histórico conta a
  partir de 10/10/2026 ~18:50 UTC.

## Fase 2a — envio manual pelo celular (10/10/2026)
Enquanto o suporte do ML não responde se automação de link é permitida (P5/D18), o ML vai **só à
mão**.

**Fluxo no painel:**
1. **Grupos:** cadastrar cada grupo com:
   - nicho;
   - etiqueta do ML;
   - link de convite;
   - política: envios por dia, intervalo e janela em hora de Brasília;
   - pausa.
2. **Fila → Aprovar:** cria uma publicação para cada grupo do nicho.
   - Aprovar de novo a mesma avaliação não duplica (`idempotency_key`).
   - O mesmo produto não volta ao mesmo grupo em 7 dias, a menos que esteja 10% mais barato e
     seja Excelente+.
   - O produto sai da fila até o preço mudar.
3. **Links:** o painel lista as URLs do dia por grupo (a página do produto com a oferta
   escolhida).
   - O operador cola as URLs no gerador do ML, com a etiqueta do grupo, e devolve os links
     gerados na mesma ordem.
   - Só entra link curto oficial: `meli.la` ou `mercadolivre.com/sec/`.
4. **Postar:**
   - "Revalidar preço" relê **a oferta do link**, não o menor preço do produto.
     - Preço subiu mais de 3% ou a oferta saiu do ar: não sai.
     - Preço caiu: vale o preço novo.
   - Com a revalidação de até 5 minutos e o grupo liberado pela política, a mensagem é montada:
     - `#publi` na primeira linha;
     - preço com a hora da verificação;
     - nada calculado sobre o histórico do ML (P6).
   - "Abrir no WhatsApp" (`wa.me/?text=`) leva o texto pronto; o operador escolhe o grupo.
   - "Postei no grupo" registra o envio uma vez só.

**Limites conhecidos:**
- Sem imagem: o WhatsApp monta a prévia do link curto. Ela pode ser apagada antes de enviar.
- A política vale no momento de abrir o WhatsApp. O registro do envio é o toque em "Postei".
- WuzAPI (telas Número e envio automático) é a Fase 2b, e depende do chip.

## O que só o dono pode fazer (nesta ordem) — ML primeiro

1. **ML (caminho crítico):**
   - criar um app no DevCenter (no Brasil é 1 por conta) e ligar o PKCE;
   - cadastrar um `redirect_uri` HTTPS que seja seu. Se a página der 404 não tem problema, o
     `code` aparece na barra de endereço;
   - gravar `ml-app.env`;
   - rodar `python -I probes/ml_probe.py auth` no seu terminal. Depois me avise, e eu rodo o
     `run`.
2. **Mandar ao ML as duas perguntas abaixo** (afiliados e desenvolvedores). As respostas decidem
   o envio automático (P5) e o texto das mensagens (P6).
3. **Shopee:** pedir o cadastro no Programa de Afiliados. Depois de aprovado, pedir a Open API e
   criar `shopee-affiliate.env`.
4. **AliExpress:** cadastro no Portals → conta de desenvolvedor → perfil Affiliate → app →
   "Apply Online".
5. **Shopee (quando aprovada):** enviar a pergunta da Shopee abaixo.
6. **Chip dedicado** para a MegaPromoOz, mais o **número co-admin** (pessoal ou um 2º chip).
7. **Caixa de e-mail dedicada** do caçador, para assinar as newsletters. Conta nova, sem vínculo
   com contas pessoais.
8. **Declarar a mídia no ML** quando os grupos existirem. Os termos (1.3) pagam só mídia declarada
   **antes** da divulgação.

## Rascunhos para os suportes

**ML — Afiliados e Criadores**
> Olá! Sou afiliado aprovado no programa Afiliados e Criadores. Vou divulgar ofertas do Mercado
> Livre em grupos de WhatsApp próprios, com entrada só por link de convite e só admins postando.
> 1. Grupo de WhatsApp pode ser cadastrado como mídia? Como faço o cadastro?
> 2. Os links serão gerados no gerador do portal, com uma etiqueta por grupo, sem encurtar nem
>    alterar. Um sistema meu agenda e publica as mensagens nos grupos. Isso é permitido, ou se
>    enquadra em "uso indevido de softwares, APIs, robôs" da cláusula 3.1.2?
> 3. Que tabela de comissão vale para esse perfil?

**ML — Desenvolvedores (DevCenter)**
> Olá! Tenho um app de afiliado (não sou vendedor). Uso `/highlights`, `/trends`, `/items` e
> `/items/{id}/sale_price` para escolher produtos que divulgo com meu link de afiliado.
> 1. Esse uso é permitido pelos Termos de Desenvolvedor?
> 2. Posso guardar internamente o histórico de preços desses itens só para filtrar descontos
>    reais, sem exibir estatística a terceiros? Se a cláusula 5.4 exigir autorização, como
>    solicito?
> 3. Qual o limite de chamadas para um app desse perfil?

**Shopee — Afiliados**
> Olá! Sou afiliado com acesso à Open API e vou divulgar ofertas em grupos de WhatsApp próprios.
> 1. Esse perfil entra na taxa "sob consulta" para sites/apps de ofertas e cupons?
> 2. Qual o limite de chamadas da Open API (erro 10030)?
> 3. O painel exporta cliques por Sub_id?
