# Modelo de dados conceitual

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 7. Modelo de dados conceitual

### 7.1 Produto × oferta × publicação × decisão
- **Produto.** Na prática, é anúncio + variação: o que um vendedor vende num marketplace.
- **Oferta.** Um produto numa condição comercial (preço, cupom, promoção, frete) durante um
  intervalo.
  - Tem `fingerprint` (anúncio, variação, faixa de preço, cupom, janela), início e expiração.
  - Um produto tem muitas ofertas ao longo do tempo.
- **Publicação.** Uma oferta enviada a um canal, com a mensagem renderizada, o preço e o cupom no
  envio, o link e o estado. Uma oferta pode gerar várias publicações.
- **Decisão.** Registro **imutável** de cada avaliação, inclusive descartes e revalidações:
  - snapshot dos dados de entrada;
  - `ruleset_version`;
  - portões e sinais disparados;
  - componentes, nota e nível;
  - quem decidiu (automático/operador) e motivo.
  - A pergunta "por que foi Excepcional e foi publicada?" se responde com `publication →
    decision`.

### 7.2 Entidades

| Entidade | Papel | MVP |
|---|---|---|
| `marketplace` | ML, Shopee, AliExpress | ✔ |
| `affiliate_program` | Conta por marketplace, janela de atribuição, versão/data dos termos lidos (credencial **fora** do banco) | ✔ |
| `source` | Fonte de dados, tipo, orçamento, confiabilidade, kill switch | ✔ |
| `collection_run` | Execução: início/fim, chamadas, itens, status | ✔ |
| `integration_error` | Erro saneado por fonte/execução | ✔ |
| `seller` | Vendedor + loja oficial/Mall + reputação atual | ✔ (histórico de reputação: adiar) |
| `listing` | Anúncio ("produto") por marketplace | ✔ |
| `listing_variant` | Tamanho/cor/voltagem/tom | ✔ mínimo |
| `canonical_product` | Mesmo produto entre marketplaces (marca+modelo/GTIN) | Fase 4 |
| `price_observation` | Série temporal (só na mudança + batimento) | ✔ |
| `price_stats` | `P_ref`, mínimas e cobertura (recalculável) | ✔ |
| `promotion` | Campanha/relâmpago com janela | ✔ simples |
| `coupon` | Cupom, regras numéricas, status, público/pessoal, última verificação; escopo em JSON | ✔ |
| `coupon_source` | Fonte do caçador: classe (A–D), termos lidos (data), robots.txt, ritmo, quem liberou, kill switch | ✔ |
| `coupon_sighting` | Evidência: fonte, quando, trecho bruto (sem dado pessoal), hash | ✔ |
| `coupon_scope` | Elegibilidade normalizada (loja/categoria/item) | adiar |
| `niche` | Nicho + mapeamento de categorias, palavras, listas de marca (alto risco, permitidas) | ✔ |
| `niche_assignment` | Anúncio ↔ nicho com confiança e método (regra/LLM/manual) | ✔ |
| `watchlist` | Itens/termos vigiados pelo operador | ✔ |
| `offer` | Oportunidade | ✔ |
| `offer_coupon` | Cupom aplicado, com desconto calculado | ✔ |
| `scoring_ruleset` | Pesos/limiares versionados e imutáveis | ✔ |
| `decision` | Avaliação imutável (append-only) | ✔ |
| `channel` | Grupo/canal, nicho, política de frequência, pausa | ✔ |
| `affiliate_link` | URL do programa, subIds/etiqueta/`tracking_id`, origem (API ou colado pelo operador), saúde | ✔ |
| `publication` | Estado (`planned → validating → sending → sent / failed / unknown / skipped`), chave de idempotência, mensagem, preço no envio, id externo | ✔ |
| `click_stat` | Cliques importados por subId/etiqueta/período (painel/CSV dos programas) | Fase 3 |
| `click_event` | Clique em redirecionador próprio, sem IP cru | só se a Fase 3 adotar redirecionador |
| `report_import` | Importação de relatório (programa, período, linhas) | Fase 3 |
| `conversion` | Linha do relatório: pedido, valor, comissão, status (pendente/aprovada/cancelada/paga), subIds, atribuição observada × estimada | Fase 3 |
| `commission_payout` | Pagamento recebido | Fase 3 |
| `job`, `worker_lease` | Fila e lease | ✔ |
| `alert` | Alerta e reconhecimento | ✔ |
| `experiment` | Testes A/B | Fase 4 |

### 7.3 Relacionamentos principais
`marketplace 1─N seller, listing` · `listing 1─N listing_variant` ·
`listing/variant 1─N price_observation` · `offer N─1 listing/variant` ·
`offer N─M coupon (offer_coupon)` · `offer 1─N decision` · `offer 1─N publication` ·
`publication N─1 channel` · `channel N─1 niche` · `publication 1─1 affiliate_link` ·
`publication 1─N click_stat` · `conversion N─1 publication` (nulo = não atribuída) ·
`decision N─1 scoring_ruleset`.

### 7.4 Regras de integridade e dados pessoais
- **Idempotência.** Índice único `(channel_id, dedup_key)` com janela, e `publication.idempotency_key`
  único.
- **Relatórios importados.** O upsert usa `(program, external_order_id, item)`, e a reimportação
  não duplica.
- **Nenhum dado de membro é guardado.**
  - Nem lista do grupo, nem telefone, nem identidade de comprador (os programas não fornecem).
  - Webhook de mensagem de grupo é descartado na entrada, exceto o que §4.8 permitir, e lá só
    (hora, ID do item).
  - Na WuzAPI: histórico de sincronização desligado e só os eventos necessários assinados.
- **Retenção.** `decision` e as observações de preço citadas numa publicação ficam guardadas por
  **5 anos** (prazo do art. 27 do CDC), porque são a prova do "de/por". O resto do histórico vira
  agregados diários depois de 180 dias.
- **Dinheiro em centavos inteiros**, sempre com a moeda. Hora em UTC com offset explícito, para
  não repetir a armadilha do "+3h" já conhecida na casa.
