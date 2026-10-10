# Estratégia de inteligência comercial

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 4. Estratégia de inteligência comercial

### 4.1 O que dá para prometer — e o que não dá
- **Antecipação absoluta não existe.** As fontes são consultadas por polling. Até onde a pesquisa
  chegou, nenhum programa avisa o afiliado por push quando um preço muda.
- Contra canais grandes, como Promobit, Pelando e grupos com 100 mil membros, a vantagem possível
  vem de quatro lugares [RECOMENDAÇÃO]:
  1. **Profundidade num nicho estreito.** Uma lista de itens e marcas vigiada com mais frequência
     do que um canal generalista consegue vigiar.
  2. **Filtro de qualidade.** O histórico próprio de preço separa desconto real de "metade do
     dobro". Isso é diferencial, não só velocidade.
  3. **Latência interna baixa.** Minutos entre detectar a oferta e publicar. Isso é controlável
     e mensurável.
  4. **Calendário.** Campanhas com hora marcada, como a janela das ofertas `shopeeOfferV2`, as
     promos em destaque do AliExpress, o 11.11, a Black Friday e o "Dia Shopee" mensal, entram
     agendadas na camada quente.
- Comprovar que chegamos antes é métrica, não promessa (§4.8).

### 4.2 Pipeline
`descoberta → normalização → observação de preço → enriquecimento (cupom, comissão, vendedor)
→ avaliação (portões → pontuação → nível → decisão gravada) → distribuição (rota, agenda,
deduplicação, revalidação, envio) → medição (cliques, conversões, comissão)`.

Cada seta grava estado no banco. Nenhuma etapa depende de memória de processo, para que redeploy
e reinício não percam nem dupliquem nada.

### 4.3 Monitoramento em camadas (orçamento de chamadas)
Toda fonte tem teto de chamadas. A regra é gastar alta frequência só onde há potencial.

| Camada | O que entra | Frequência inicial [HIPÓTESE] | Sobe quando | Desce quando |
|---|---|---|---|---|
| **Quente** | Lista do operador; item com fim de promoção conhecido; item a até 5% da mínima registrada; item que já foi publicado e está ativo | 15–30 min | queda ≥ 8% numa leitura; campanha começa; vendas acelerando | 48 h sem mudança |
| **Morna** | Top N por categoria-alvo (vendidos/relevância) e resultados das buscas por nicho | 2–6 h | sinais acima | 7 dias sem mudança |
| **Fria** | Cauda; itens sem mudança há 14 dias | 24 h | sinais acima | sai do monitoramento após 60 dias sem interesse |
| **Descoberta** | Shopee: feed diário `listItemFeeds`/`getItemFeedData` (FULL na 1ª carga, DELTA depois) + `productOfferV2` por categoria/palavra (`TOP_PERFORMING`, `HIGHEST_COMMISSION`) + `shopeeOfferV2` (campanhas com janela). ML: `highlights` (top 20 por categoria) + `trends` (semanal) — **a busca por palavra-chave do ML está fechada** (403). AliExpress, quando aprovado: `hotproduct`/`featuredpromo` | 1–3 h (feed: diário) | — | — |
| **Revalidação** | Item que vai ser enviado agora | sob demanda | — | — |

- **Orçamento.** Um token bucket por fonte, com teto de ~50% do limite medido. A reserva fica com
  a revalidação pré-envio, que tem prioridade máxima.
- **Capacidade da camada quente:** `itens ≈ (limite/h × fração_quente × itens_por_chamada) ÷
  (60 ÷ intervalo_min)`.
  - Os limites reais da Shopee e do ML não são publicados de forma confiável → [TESTAR] na Fase 0.
  - O desenho já sai paramétrico, sem número cravado.

### 4.4 Desconto real × preço inflado (anti "metade do dobro")
- **Série própria por anúncio/variação.** Grava observação só quando o preço muda, mais um batimento
  diário. Ter histórico próprio é o ativo que mais leva tempo para construir: **o relógio só começa
  quando o coletor liga.**
- **Preço de referência `P_ref`.** Mediana dos mínimos diários dos últimos 30 dias, excluídas as
  últimas 48 h. Só vale com cobertura ≥ 10 dias com leitura no período.
- **Desconto real** = `1 − P_final / P_ref`. Mínima de 90 dias `P_min90` também só com cobertura.
- **Sinal "subiu para descer".** Se o máximo dos 14 dias antes da queda passa de 1,15 × a mediana
  de 60 dias, o desconto real é recalculado contra a mediana de 60 dias e a oferta vai para revisão
  (S1).
- **"De" exibido pelo marketplace.** O preço riscado (`original_price`, `priceDiscountRate` da
  Shopee) **nunca** é base de desconto. Serve só como dado auxiliar, e só quando bate com a série
  própria (±5%).
- **Partida a frio (primeiras 2–4 semanas).**
  - Sem cobertura, o desconto fica "não comprovado".
  - O nível máximo é **Boa**, e a mensagem sai **sem percentual de economia**.
- **Comparação entre vendedores.** Mesmo produto de catálogo, como o `catalog_product_id` do ML ou
  marca+modelo exatos → mediana entre vendedores como segunda referência.
- **Eventos (Black Friday, 11.11).** A referência fica congelada numa janela que termina 21 dias
  antes do evento, porque o preço costuma subir no aquecimento.
- **Consequência de calendário.** Para comprovar desconto na Black Friday de 27/11/2026, o coletor
  precisa estar rodando **até ~27/10**.
- **Mercado Livre é exceção por contrato** [OFICIAL — termos de desenvolvedor do ML]:
  - 2(f) e 7.2/7.3: vedam repassar a terceiros, fora do app, dados obtidos pela integração.
  - 5.4: estatísticas derivadas (preço médio, volume, conversão) exigem **autorização expressa**.
  - Termos do afiliado 5.4: só citar o que consta no anúncio.
  - Consequências **até a autorização ser pedida e concedida** (Fase 0):
    - nada de afirmação com base em histórico nas mensagens de itens do ML;
    - o preço publicado é o do anúncio, e "de/por" só se o próprio anúncio mostrar;
    - a série do ML serve só para filtrar internamente, e mesmo isso fica pendente de leitura
      jurídica.
  - Shopee e AliExpress não têm essa trava nos documentos lidos.

### 4.5 Cupons — o caçador em background

Um serviço do worker "caça" cupons continuamente em fontes **permitidas**. Cada cupom vai para o
banco com a evidência de onde e quando foi visto, e o operador vê tudo na tela **Cupons**.

**Fontes, por classe** (decisão do dono, 10/10/2026)

| Classe | Fonte | Como | Situação |
|---|---|---|---|
| **A — API oficial** | AliExpress `promo_code_info` e `featuredpromo` | Junto com a coleta de produtos | Quando o AliExpress for aprovado |
| | Shopee `shopeeOfferV2` | Campanhas com janela de início e fim. Não traz o código do voucher | Fase 1 |
| **B — Canal próprio** | **Caixa de e-mail dedicada** (IMAP sobre TLS) assinando as newsletters de Shopee, ML, AliExpress e das lojas oficiais das marcas dos nichos | O parser lê cada e-mail novo e extrai código, valor, mínimo, validade e escopo. O e-mail chegou para nós, então ler não é scraping | Fase 1 |
| | **Canais oficiais no WhatsApp** de marketplaces e marcas, seguidos pelo número MegaPromoOz | O post chega como evento da WuzAPI; o parser extrai o cupom [TESTAR se o evento de newsletter chega] | Fase 2 |
| **C — Scraping educado de terceiros** | Páginas **públicas** de cupons e promoções de lojas oficiais e marcas, ou agregadores **cujos termos não vedem** | Só depois de conferir termos e robots.txt **fonte a fonte**, e só com o dono ligando a fonte no painel | Fase 2, fonte a fonte |
| **D — Manual** | Cupom de afiliado do ML (Central, atrás de login) e cupons que o operador vê no app | Cadastro rápido na tela Cupons | Fase 1 |
| **Vedado** | Sites e apps de **Shopee e Mercado Livre** (termos do afiliado Shopee 4.3d e ML 4.3c; ML dev 7.6); Promobit (proíbe bots); Pelando (uso não autorizado do conteúdo); qualquer coisa atrás de login, captcha ou antirrobô | — | Fora |

**Regras do scraping educado (classe C)**
- **Registro da fonte.** Cada fonte é um registro com: termos lidos (data e trecho resumido),
  resultado do robots.txt, ritmo máximo, dono que liberou e data. Sem registro, o coletor não
  roda.
- **Ritmo.**
  - Uma requisição por vez por domínio, com intervalo mínimo (ex.: 30–60 s) e janela diária.
  - Cache com `ETag`/`If-Modified-Since`.
  - User-agent que identifica o robô e traz um contato.
- **Disjuntor.** Parar sozinho e alertar em 403, 429, captcha ou página de desafio. **Sem
  contornar antirrobô**: nada de proxy rotativo, browser furtivo ou resolver captcha.
- **Escopo.** Só cupom, campanha e preço anunciado. Nenhum dado pessoal; avaliação e comentário
  de usuário não são coletados.
- **Tecnologia.** httpx + parser de HTML (selectolax ou lxml) com teste de contrato por fonte
  (HTML salvo como fixture). Fonte que só funciona com navegador headless fica **fora do MVP**:
  são ~300–500 MB de RAM, e é sinal de proteção ativa.
- **Manutenção.** Fonte que devolve 0 cupom por N rodadas seguidas abre alerta: o HTML mudou.

**Extração de cupom de texto livre** (e-mail, post de canal). Primeiro regras: regex de código,
valor e "acima de R$". O LLM entra só nos casos em que a regra falha e precisa ser medido antes:
- modelo pequeno, saída por schema validado;
- orçamento por chamada;
- o texto vai sem dado pessoal (o e-mail pode trazer nome do destinatário, que é removido antes).

**Público × pessoal.** Newsletter traz muito cupom **pessoal** (só para aquela conta), que não
serve para o grupo.
- O parser marca `audience = público | pessoal | desconhecido`.
- Só `público` pode aparecer numa mensagem.
- `desconhecido` fica para o operador decidir.

**Status do cupom**
- **CONFIRMADO**: fonte classe A, dentro da validade, regras numéricas conhecidas, checado nas
  últimas 6 h. Pode entrar no "preço final".
- **INFORMADO**: classe B, C ou D, ou regras incompletas. Aparece em linha separada, com a
  condição e "sujeito a disponibilidade". **Nunca** entra no "preço final".
- **EXPIRADO** ou **INVÁLIDO**: some sozinho no fim da validade, ou quando o operador marca.

**Deduplicação.** A chave é `(marketplace, código, escopo, início)`. Cada nova aparição do mesmo
cupom vira uma linha de evidência, não um cupom novo. Várias fontes concordando aumentam a
confiança.
- **Preço final com cupom** só aparece quando o cupom está CONFIRMADO e o cálculo é determinístico:
  - Fórmula: `desconto = min(valor ou % × base, teto)`, só se `base ≥ mínimo`.
  - Combinação com outra promoção só entra quando a fonte diz que combina. Fora disso, o cupom
    aparece em linha separada, com a condição e "sujeito a disponibilidade".
- **Restrições de contrato.**
  - Shopee §13.1.1: nunca divulgar cupom nominal de outro afiliado.
  - Redes (Awin/Admitad): o cupom só vale com o link da rede, o que conflita com o link nativo.
  - Por isso, **no MVP, só cupons dos programas nativos**.
- **Cupom de afiliado ML pode reduzir a comissão.** O relato é de 3–5% contra até 16%
  [COMUNIDADE]. O motor calcula o valor esperado com e sem cupom e registra qual escolheu. A
  escolha é explícita, nunca implícita.
- Cupom com usuário restrito (só novo cliente, só app, só certo pagamento) aparece com a restrição
  escrita, ou não aparece.

### 4.6 Classificação (pontuação explicável)

**Portões (bloqueiam; avaliados nesta ordem)**

| # | Portão | Regra inicial (configurável) |
|---|---|---|
| G1 | Frescor | preço lido ≤ 6 h para ranking; ≤ 5 min para envio |
| G2 | Disponibilidade | ativo e com estoque |
| G3 | Link | link de afiliado gerado e resolvido para o item certo no domínio do marketplace |
| G4 | Nicho | classificação com confiança ≥ limiar |
| G5 | Vendedor | reputação/nota ≥ limiar e vendas ≥ N |
| G6 | Produto | nota ≥ 4,3 com ≥ 20 avaliações (lançamento de loja oficial isento) |
| G7 | Autenticidade | marcas de alto risco do nicho só de loja oficial / Mall / revenda autorizada; AliExpress + marca de alto risco = bloqueio |
| G8 | Conformidade | título com "réplica", "inspired", "1ª linha", "AAA", "tailandesa", "primeira linha" etc. = bloqueio |

**Sinais de suspeita** (mandam para o nível *Suspeita*; a oferta fica bloqueada até revisão humana)
- **S1** "subiu para descer".
- **S2** desconto real > 70% em marca conhecida (erro de preço ou falso).
- **S3** preço abaixo do piso plausível da marca/categoria.
- **S4** vendedor novo com preço muito abaixo da mediana entre vendedores.
- **S5** divergência > 10% entre a leitura do ranking e a revalidação.
- **S6** cupom com regras inconsistentes.

**Componentes (0–100, fórmula determinística, versionada)**
- **F — Atratividade financeira**
  - Desconto real, interpolado por trechos: 0% → 0; 10% → 35; 20% → 65; 30% → 85; ≥ 40% → 100.
  - +10 se estiver na mínima de 90 dias.
  - Ajuste logarítmico pela economia absoluta em R$.
  - Desconto não comprovado → F = 0.
- **D — Potencial comercial.** Percentil, dentro do nicho, de vendas (ou Δvendas/dia quando a fonte
  dá contagem), nota e nº de avaliações; bônus se o item está em `highlights`/tendência do ML.
- **Q — Confiabilidade**
  - Frescor e confiabilidade da fonte.
  - Reputação do vendedor; loja oficial.
  - Status do cupom.
  - Cobertura do histórico.
- **U — Urgência**
  - Fim conhecido: ≤ 6 h → 100; ≤ 24 h → 70; desconhecido → 30.
  - Volatilidade observada.
  - Estoque baixo, quando informado.
- **R — Rentabilidade.** Comissão esperada = `P_final × taxa_efetiva × p_conv(categoria) × (1 −
  p_cancel)`, normalizada por percentil no nicho.
  - Os `p_conv`/`p_cancel` começam **iguais para todas as categorias** (prior neutro, sem número
    inventado).
  - Na Fase 3 passam a ser estimados com dados próprios, com encolhimento bayesiano: a média da
    categoria só pesa conforme o nº de cliques.

**Nota** = `0,35·F + 0,20·D + 0,15·Q + 0,10·U + 0,20·R` [RECOMENDAÇÃO]. Os pesos são por nicho, e
editar cria uma nova `ruleset_version`, imutável.

**Níveis**

| Nível | Condição inicial | Ação |
|---|---|---|
| Excepcional | nota ≥ 85 **e** F ≥ 70 **e** Q ≥ 70 **e** desconto comprovado | publica no próximo slot, respeitando o intervalo mínimo; alerta o operador |
| Excelente | nota ≥ 75 | alta prioridade na agenda |
| Boa | nota ≥ 60 | preenche slots livres |
| Regular | nota < 60 | não publica; continua monitorada |
| Suspeita | qualquer S1–S6 | bloqueada até revisão humana |

**Calibração.** Nas 2 primeiras semanas, toda decisão passa por aprovação no painel e o operador
marca concordo/discordo. É assim que pesos e limiares se ajustam; nenhuma nota é tratada como
verdade.

**LLM.** Não dá nota nem decide nível. Usos candidatos, cada um medido contra regra antes de
entrar:
- classificar nicho de título ambíguo, com saída estruturada validada por schema;
- extrair atributos do título (voltagem, tamanho, tom).

### 4.7 Distribuição
- **Rota.** Oferta → nicho primário, mais secundários com confiança ≥ limiar. Vai para mais de um
  grupo **só se for Excepcional**, com intervalo entre os grupos e cabeçalho adaptado ao nicho.
- **Política por grupo (configurável)**
  - Máximo de mensagens/dia: 8–15 [HIPÓTESE].
  - Intervalo mínimo: 30–45 min.
  - Janela de envio: 08h–22h.
  - Teto por marketplace, marca e vendedor por dia.
  - Pausa por nicho.
- **Agenda por slots.** A Excepcional ocupa o próximo slot. A Boa preenche slots livres nos
  horários de melhor desempenho, aprendidos na Fase 3+.
- **Deduplicação** (chave determinística + índice único no banco, nunca só em memória)
  - **Produto × grupo.** O mesmo anúncio (ou produto de catálogo) não volta ao mesmo grupo em 7
    dias. Exceção: preço ≥ 10% abaixo do último publicado **e** nível ≥ Excelente.
  - **Variação.** Variações do mesmo anúncio contam como o mesmo produto.
  - **Vendedor/marca.** ≤ N por dia por grupo.
  - **Cupom.** O mesmo cupom não é anunciado como novidade mais de 1×/dia/grupo.
  - **Entre marketplaces.** No MVP só com marca+modelo exatos. Por similaridade, só na Fase 4.
- **Revalidação pré-envio (obrigatória).** Até 5 min antes, relê preço/estoque, recalcula o cupom
  e confere o link.
  - Preço subiu > 3% ou estoque zerou → nova avaliação, que pode cancelar o envio.
  - Preço caiu → a mensagem é atualizada.
- **Envio no máximo uma vez.** Timeout ambíguo **não** gera reenvio automático:
  1. A publicação vira `unknown`.
  2. A confirmação vem do id/evento da mensagem.
  3. O operador é alertado.
  - Duplicata em grupo custa mais do que uma oferta perdida.
- **Correção.** Preço comprovadamente errado gera uma única mensagem de correção no grupo. Mudança
  normal de preço depois do envio não gera nada (a mensagem já diz a hora da verificação).

### 4.8 Medir se chegamos antes
- **Controlável.** Latência interna `detectado → publicado`, p50/p95.
- **Relativa (amostragem).**
  - Os termos do Promobit vedam bots, e os do Pelando vedam uso não autorizado do conteúdo. **Não
    raspar esses sites.**
  - Opção [TESTAR + decisão do dono]: o número de ofertas segue, como usuário comum, alguns
    canais/grupos públicos de concorrentes do nicho. Os eventos de mensagem que a WuzAPI recebe dão
    o par (hora, ID do item). Esse par serve **só** para comparar tempo: a regra fica escrita no
    código e nada é republicado. "Espelhar" grupos alheios está **rejeitado**.
- **Indicadores**
  - % das nossas publicações que saíram antes do 1º concorrente monitorado.
  - Lead time mediano.
  - % de itens nossos que nunca apareceram nos concorrentes (diferenciação).

### 4.9 Formato da publicação
Template determinístico. Cada linha só aparece quando o dado está comprovado.

```text
#publi · link de afiliado (posso receber comissão)
🔧 FERRAMENTAS · preço verificado em 12/11 às 14:32
Parafusadeira de impacto <marca> <modelo> (127 V) · loja oficial
💰 R$ 389,90 à vista na Shopee
📉 18% abaixo da mediana de 30 dias que registramos (R$ 475,00)      ← só com histórico
🎟️ Cupom XXXX: −R$ 30,00 acima de R$ 300 → R$ 359,90               ← só cupom CONFIRMADO
Preço e estoque podem mudar a qualquer momento.
🛒 <link>
```

Os valores acima são ilustrativos. A linha 📉 **não sai em item do Mercado Livre** enquanto não
houver autorização (§4.4). No ML, o texto usa só o que o anúncio mostra.

- **Identificação publicitária na primeira linha.** O Guia de Influenciadores do CONAR de 2026
  cita afiliados por "links, cupons ou outros mecanismos rastreáveis" e exige identificação
  "visível em primeiro plano" (#publi/#publicidade) [OFICIAL]. CDC art. 36: publicidade
  identificável fácil e imediatamente.
- **Regras de texto**
  - Nunca "menor preço histórico". Escrever "o menor que registramos desde DD/MM".
  - Nunca "% OFF" sobre o "de" do vendedor.
  - Data e hora da verificação, sempre.
  - Preço **à vista em reais**. Preço da AliExpress convertido para real e com impostos
    explicitados (Decreto 5.903/2006, art. 9º, V) [OFICIAL; imposto: TESTAR].
  - Frete só quando a fonte informar.
- **"De/por" só com registro documental do preço anterior.** É o que a recomendação SEDCON–Procon/RJ
  (11/11/2025) exige, e no CDC (art. 38) o ônus da prova é de quem patrocina [OFICIAL]. A série
  de preços e o snapshot da decisão **são** esse registro, então ficam retidos (§7.4).
- **Nome, foto e descrição do grupo**
  - Sem marca ou logo da Shopee (termos §2.3a) [OFICIAL].
  - Sem termos que pareçam golpe: a central de ajuda do WhatsApp diz que nome e foto do grupo são
    analisados [OFICIAL].
- **Imagem com legenda, montada com dados da API.** Sem `LinkPreview`, porque com ele o servidor
  da WuzAPI baixa a URL, e no link de afiliado isso vira **clique de robô**. O acordo da AliExpress
  (1.7) trata clique gerado por script como fraude [OFICIAL].
- **Nichos fora:** nada de álcool, medicamento isento de prescrição ou aposta. A Business Messaging
  Policy proíbe divulgar esses itens pelo Business app [OFICIAL].
- **Links:** ver §6.4.
