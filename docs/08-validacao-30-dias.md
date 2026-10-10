# Plano de validação de 30 dias

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 14. Plano de validação de 30 dias

**Pergunta do experimento.** Com dois nichos, partindo de audiência zero, o robô encontra ofertas
que um curador humano aprovaria? Consegue publicá-las sem erro? E a audiência responde com
cliques e comissão registrada a ponto de justificar crescer?

**Pré-requisito (dias −10 a 0).**
- Sondagens da Fase 0 registradas.
- Chip dedicado aquecido com uso normal.
- Co-admin reserva definido.
- Nome e identidade dos grupos prontos.
- Stack nova liberada para deploy, conforme a decisão D1 da §13.

**Ponto de calendário.** Começar a coleta até ~27/10 deixa 30 dias de histórico para comprovar
desconto na Black Friday (27/11/2026). Isso tem dois efeitos:
- a BF é a melhor vitrine para o diferencial "desconto real verificado";
- ela também **infla** os números do mês, então a decisão compara a semana da BF com as semanas
  fora dela.

| Semana | Foco | Entregas | Medir |
|---|---|---|---|
| 1 | Coleta e audiência | Coletor Shopee + ML ligado; modo sombra nos 5 nichos; 2 grupos criados (só admins enviam); descrição com aviso de publicidade e privacidade; início da aquisição orgânica (rede pessoal, perfis próprios, página simples com convite) | itens/dia, cobertura do histórico, membros |
| 2 | Publicação com aprovação | Envio pela WuzAPI só do que o operador aprovar no painel pelo celular (3–6 por grupo/dia); caçador de cupons com API + caixa de e-mail; itens ML pelo adaptador manual até a resposta do suporte (D18); calibração da nota | concordância, detecção→envio, CTR, saídas por post |
| 3 | Automação com limites | Automático para Excelente+ (Excepcional ainda avisa); Boa ainda aprovada; ML continua assistido (link colado); teste de formato (imagem+legenda × texto); teste opcional de aquisição paga com orçamento pequeno e fixo, para medir custo por membro | EPC inicial, custo por membro, duplicatas, uptime |
| 4 | Medir e decidir | Importação do `conversionReport` (Shopee) e das métricas por etiqueta (ML); relatório de 30 dias; decisão | ARPM projetado, comissão registrada × aprovada |

**Metas para continuar** [HIPÓTESE; o dono ajusta antes de começar]

| Dimensão | Meta |
|---|---|
| Técnica | 0 duplicatas; revalidação em 100% dos envios; sessão ≥ 98% do tempo conectada; nenhuma fonte parada > 6 h sem alerta |
| Qualidade | operador concorda com ≥ 70% do top 10 na semana 2 e ≥ 80% na semana 4; ≤ 1 reclamação procedente de preço errado por 100 posts |
| Audiência | ≥ 300 membros somando os 2 grupos sem anúncio; saídas < 1 por 100 membros por semana |
| Comercial | CTR mediano por post ≥ 1% dos membros; comissão registrada > 0 nos dois nichos; EPC medido nos dois |

**Decisão no dia 30**

| Decisão | Quando |
|---|---|
| **Continuar** (Fase 3 completa, 3º nicho) | Técnica e qualidade OK, audiência crescendo e EPC × crescimento projetando ARPM que pague as horas de operação |
| **Corrigir** | Motor OK mas audiência parada → o problema é aquisição, não produto: atacar canal e oferta de valor do grupo |
| | Audiência OK mas EPC baixo → rever mix (ticket × comissão), horários, nichos e link direto × redirecionado |
| | Qualidade abaixo da meta → mais 2 semanas de calibração antes de automatizar |
| **Interromper ou trocar de canal** | Número restrito/banido sem recuperação |
| | < 100 membros depois de 30 dias de esforço |
| | CTR < 0,3% e nenhuma conversão |
| | Operação exigindo mais horas/semana do que o dono definiu, sem tendência de queda |

---

## Como verificar este plano
- **Fase 0:** cada [TESTAR] vira registro de sondagem aprovado (data, fonte, pedido/resposta
  saneados, decisão). Premissa refutada volta para este documento antes de virar código.
- **Fases 1 e 2:** critérios mensuráveis da §10.
- **Hipótese comercial:** plano de 30 dias da §14, com metas e corte fixados pelo dono antes de
  começar.
