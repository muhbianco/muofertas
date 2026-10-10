# Roadmap, métricas, riscos e decisões

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 10. Roadmap de execução

| Fase | Duração [HIPÓTESE] | Entregáveis | Depende de | Pronto quando | Riscos | Indicadores |
|---|---|---|---|---|---|---|
| **0 — Viabilidade** | 1–2 sem. (meta: até ~24/10) | Este documento + **sondagens registradas** (padrão da casa: script lê o segredo de arquivo e nunca o imprime; registro com data, fonte, pedido/resposta saneados e decisão). **Shopee:** introspecção do schema, limite real (erro 10030), feeds FULL/DELTA, `generateShortLink`/subIds, `conversionReport`, exportação de cliques por sub_id, pergunta ao suporte sobre a taxa "sob consulta". **ML:** app OAuth de não-vendedor chamando `highlights`, `trends`, `/items/{id}/sale_price`; perguntas ao suporte de afiliados (publicação automatizada de links gerados no portal em grupos declarados? grupo de WhatsApp vale como mídia?) e ao de desenvolvedores (autorização para uso interno de série de preço); **declarar os grupos como mídia**; criar as etiquetas. **AliExpress:** pedir Portals + app (≈ 2 dias úteis, segundo o fornecedor). **Receita:** ato que zerou o II até US$ 50. **WuzAPI** (`muconnect`, chip de teste): grupo, `announce`, imagem+legenda, texto para `@newsletter`, reconexão, eventos. **Caçador:** criar a caixa de e-mail dedicada e assinar as newsletters; levantar 5–10 fontes candidatas de classe C, com termos e robots.txt lidos. Identidade MegaPromoOz (domínio, INPI) | aprovações externas; chip | Toda premissa [TESTAR] tem registro aprovado pelo dono | Shopee com limite baixo; ML vetar automação | nº de premissas confirmadas/refutadas |
| **1 — Descoberta** | 2–3 sem. | Repo + CI; schema núcleo; conectores Shopee + ML + entrada manual por link colado; coleta em camadas; histórico de preço; portões + nota v1; painel mobile-first (fila de aprovação, **Nichos**, Cupons); caçador classes A/B(e-mail)/D; **modo sombra nos 5 nichos** | Fase 0; liberar deploy da stack nova | ≥ 14 dias de coleta com < 1% de execuções falhas; ≥ 10 ofertas Boa+ por dia por nicho ativo; operador concorda com ≥ 70% do top 10 | Partida a frio do histórico; ruído de classificação | itens monitorados, cobertura, frescor, concordância |
| **2 — Distribuição** | 2 sem. (em paralelo ao fim da F1) | Porta Canal + adaptador WuzAPI (`muconnect`) + adaptador manual; telas **Número** (pareamento por API) e **Grupos** (listar/criar, só admins, convite, **vincular ao nicho**, política); fila "Links ML pendentes"; template; imagem; links com subIds; deduplicação, limites, revalidação, envio no máximo uma vez; alertas por e-mail; kill switch por nicho/fonte/canal; caçador: canais seguidos + 1ª fonte de classe C liberada | F1; chip; co-admin nos grupos | 14 dias com 0 duplicata, 0 envio fora da política, revalidação em 100% | Restrição do número; sessão instável | envios/dia, falhas, detecção→envio p50/p95, uptime da sessão |
| **3 — Medição** | contínua a partir da 4ª–5ª semana | Importação do `conversionReport` (Shopee) e das métricas por etiqueta do ML (CSV/painel); cliques por sub_id, se a Shopee exportar; estados de comissão; resultado por nicho/horário/nível; EPC; ARPM; decisão sobre redirecionador (Shopee/AliExpress) | Dados de 3–4 semanas | ≥ 80% da comissão Shopee atribuída a publicação; conciliação mensal estimado → aprovado → pago | Atraso/latência dos relatórios; ML sem subId | EPC, ARPM, % atribuída |
| **4 — Adaptativa** | só com volume (ex.: ≥ 1.000 publicações e ≥ 200 conversões [HIPÓTESE]) | `p_conv` por categoria com encolhimento; horários; regressão logística simples clique/conversão ~ desconto, preço, nicho, hora; anomalias robustas (MAD); produto canônico; experimentos | F3 | Modelo bate a regra determinística num teste fora da amostra | Sobreajuste com pouco dado | lift de EPC vs. baseline |
| **5 — Escala** | depois de validar | Novos nichos (escolhidos pelo modo sombra), AliExpress, Telegram/outros canais, 2º número, mais frequência dentro dos limites, reavaliação do canal oficial | Resultado positivo | Unidade econômica positiva por nicho | Diluição de foco | ARPM por nicho, horas de operação/semana |

---

## 11. Métricas de sucesso

Cada métrica é marcada como **observada** ou **estimada** e nunca as duas misturadas.

| Grupo | KPI | Tipo |
|---|---|---|
| Coleta | % de execuções ok por fonte; chamadas usadas ÷ limite; itens/h; idade média do preço no ranking | observada |
| Velocidade | detecção → decisão e decisão → envio (p50/p95); lead time sobre concorrentes monitorados | observada / amostral |
| Confiabilidade | uptime da sessão; desconexões/semana; envios `failed`/`unknown`; **duplicatas (meta 0)**; links quebrados | observada |
| Qualidade | concordância do operador com o top 10; % das Suspeitas que eram problema real; reclamações de preço errado / 100 posts; % canceladas na revalidação | observada |
| Audiência | membros por grupo; crescimento líquido/semana; **saídas por 100 posts** (fadiga); custo por membro, se houver anúncio | observada |
| Comercial | CTR por post (cliques ÷ membros); EPC (comissão ÷ clique); pedidos ÷ clique quando o programa informa; comissão **estimada → aprovada → paga** em linhas separadas; taxa de cancelamento por categoria | observada / estimada (marcada) |
| Unidade econômica | **ARPM** (comissão aprovada por membro por mês); receita por publicação; horas de operação/semana | observada |

---

## 12. Registro de riscos

**Escalas.**
- Probabilidade (P) e impacto (I): A = alta, M = média, B = baixa.
- Classe: **ACEITO** = aceitável no MVP; **MITIGAR** = antes do lançamento; **INVIAB.** = pode
  inviabilizar o caminho.

| # | Risco | P | I | Mitigação | Classe | Prioridade |
|---|---|---|---|---|---|---|
| R1 | Número restrito ou banido por automação não oficial (WuzAPI viola a cláusula 5(g) dos termos do Business app) | M–A | A | §9.3 inteira; co-admin; modo degradado manual; marca própria | **INVIAB.** (o canal automatizado) — aceito pelo dono em 10/10/2026 | 1 |
| R2 | Correlação do número de ofertas com a identidade MuhBianco na Meta (portfólio desabilitado em 2025, App Review em curso). A `muconnect` compartilhada põe o número de ofertas no mesmo cliente e IP dos números dos lojistas | ? | **Muito alto** (atinge o produto principal) | Identidade isolada (§9.3.1); marca MegaPromoOz; nunca ligar o número a portfólio/Meta One; token admin restrito (§9.4). Exposição a mais aceita pelo dono | **INVIAB.** (para o produto principal) | 1 |
| R2b | Incidente ou upgrade da `muconnect` derruba os dois produtos ao mesmo tempo | M | M | Upgrade coordenado e testado nas duas sessões; alertas dos dois lados; modo manual (§9.3.7) | ACEITO | 2 |
| R2c | Deploy da stack nova durante o App Review afetar o Chatwoot/canal sob revisão | B | A | A stack nova não mexe em api-agents/Chatwoot; smoke do Chatwoot depois de cada deploy; o dono testa à mão | **MITIGAR** | 1 |
| R3 | Banimento impedir nova conta comercial (termos de 23/09/2026) | B–M | A | Não reincidir com chip novo depois de banimento; considerar Telegram/Canal manual como plano B | **INVIAB.** | 2 |
| R4 | Audiência não cresce (partida do zero) | A | A | Aquisição desde a semana 1; métrica semanal; corte em 30 dias; teste pequeno de anúncio | **MITIGAR** | 1 |
| R5 | Regras do afiliado ML: só pagam mídias declaradas antes de divulgar (1.3); grupos de WhatsApp/Telegram são reconhecidos (1.2.2); proibido disparo massivo (7.3) e encurtar link (1.8) [OFICIAL] | M | A | **Declarar os grupos MegaPromoOz como mídia** antes do 1º post; confirmar com o suporte que grupo de WhatsApp vale como mídia | **MITIGAR** | 1 |
| R6 | Shopee: limite da API baixo ou mudança de schema; marca da Shopee no nome do grupo (§2.3a) | M | M | Orçamento por camada; teste de contrato do schema; kill switch; nome sem marca | **MITIGAR** | 2 |
| R7 | ML sem rota de descoberta para afiliado (busca/itens fechados sem token) | M | M | Lista vigiada manual + `highlights`/`trends` com token de app; reduzir o peso do ML | ACEITO | 2 |
| R8 | Preço errado publicado | M | M | Revalidação ≤ 5 min; data e hora no texto; correção única | **MITIGAR** | 2 |
| R9 | Desconto enganoso (CDC art. 37; Procon) | M | A | Desconto só com série própria; texto padronizado; prova retida 5 anos | **MITIGAR** | 1 |
| R10 | Promover falsificado (LPI art. 190; Shopee §8.2; AliExpress 5.4) | M | A | Portões G7/G8; listas de marca; AliExpress + marca de risco bloqueado | **MITIGAR** | 1 |
| R11 | Clique de robô no link de afiliado (prévia, checagem) tratado como fraude | M | M | Sem `LinkPreview`; saúde via API; nenhum GET nosso no link de afiliado | **MITIGAR** | 2 |
| R12 | Atribuição incompleta (ML sem sub-id) | A | M | Atribuição estimada por janela, marcada como estimada; Shopee com subIds como referência | ACEITO | 3 |
| R13 | Cancelamento/devolução corroem comissão (moda, calçados) | M | M | Medir por categoria; `p_cancel` na nota; mix de nichos | ACEITO | 3 |
| R14 | LGPD (números visíveis no grupo; controlador) | M | M | Não guardar membros; aviso de privacidade; canal de contato; Canal do WhatsApp como complemento | **MITIGAR** | 2 |
| R15 | Denúncias de spam pelos membros | M | A | Frequência limitada; grupos só de admins; sair = opt-out; nada de 1:1 | **MITIGAR** | 2 |
| R16 | Mudança de protocolo derruba a WuzAPI ("405") | M | M | Versão fixa; atualização testada; alertas; modo manual | ACEITO | 3 |
| R17 | Sessão desconecta (14 dias, logout) | M | M | Checagem semanal do celular; alertas; re-pareamento pelo painel | ACEITO | 3 |
| R18 | Segredos (AppSecret da Shopee, tokens) vazarem | B | A | Arquivo do host/Env da stack; nunca no banco, log ou chat; rotação | **MITIGAR** | 2 |
| R19 | Painel exposto | B | A | Host separado, TOTP, rate limit, negar por padrão | **MITIGAR** | 2 |
| R20 | Medição de clique por publicação fraca sem redirecionador | A | B | Shopee: conversão por subId; ML: cliques por etiqueta (grupo); conferir exportação de cliques por sub_id na Shopee | ACEITO | 3 |
| R26 | Termos de desenvolvedor do ML (repasse de dados a terceiros; estatística derivada exige autorização) | M | M | ML sem afirmação de histórico nas mensagens; pedir autorização; leitura jurídica | **MITIGAR** | 2 |
| R27 | ML considerar a publicação automatizada "uso indevido de software/robôs" (3.1.2 — venda não paga) | M | A | Perguntar ao suporte de afiliados **antes**; se houver dúvida, posts do ML só manuais (adaptador `manual`) | **MITIGAR** | 1 |
| R28 | Shopee aplicar taxa "sob consulta" para canais de "ofertas/cupons" | M | M | Perguntar ao suporte na Fase 0; a nota usa a comissão por item da API | **MITIGAR** | 2 |
| R29 | Scraper de terceiro quebrar (HTML muda), ser bloqueado ou violar termos que mudaram | A | B | Registro da fonte com data dos termos; revisão trimestral; disjuntor; teste de contrato; alerta de "0 cupom" | ACEITO | 3 |
| R30 | Cupom **pessoal** (newsletter) publicado como se fosse público → membro não consegue usar → reclamação | M | M | Marca público/pessoal; só `público` sai; `desconhecido` vai para o operador | **MITIGAR** | 2 |
| R31 | Cupom caçado sem validade/regras claras virar "preço final" | M | A | Só CONFIRMADO (classe A) entra no preço final; o resto aparece como INFORMADO, em linha separada | **MITIGAR** | 1 |
| R21 | Termos/comissões mudam sem aviso | M | M | Registrar versão/data dos termos lidos; revisão mensal | ACEITO | 3 |
| R22 | Black Friday distorce a validação | A | B | Comparar a semana do evento com as semanas fora dele | ACEITO | 4 |
| R23 | Horas de operação maiores que o previsto | M | M | Medir horas/semana; automatizar pelo que mais consome | ACEITO | 3 |
| R24 | Concentração na hel1 (ponto único) | B | M | Backup B2 diário + restore de prova; a sessão da WuzAPI fica no backup cifrado | ACEITO | 3 |
| R25 | Enquadramento tributário da comissão (MEI × atividade de afiliado) | M | M | Consultar contador antes do 1º saque | **MITIGAR** | 3 |

---

## 13. Decisões pendentes (antes de codar)

| # | Decisão | Recomendação | Por quê |
|---|---|---|---|
| D1 | ~~Deploy durante o congelamento~~ | **Decidido:** exceção para este projeto; conferir o Chatwoot depois de cada deploy | Histórico precisa começar até ~27/10 |
| D2 | ~~Instância WuzAPI~~ | **Decidido:** `muconnect` compartilhada, interna; token admin restrito (§9.4) | — |
| D3 | ~~Marca~~ | **Decidido:** MegaPromoOz. Conferir domínio e busca no INPI antes de imprimir em qualquer lugar [TESTAR] | Sem marca de marketplace no nome ✔ |
| D4 | Nichos iniciais | Ferramentas + Maquiagem; Tênis no mês 2 | §5 |
| D5 | Escopo "Maquiagem" × "Beleza" (skincare, perfume) | Maquiagem + reposição; perfume fora no MVP | Perfume traz o "contratipo" (falsificação) |
| D6 | ~~Canal de alerta e de aprovação~~ | **Decidido:** sem Telegram. Aprovação no painel mobile-first; alertas críticos por e-mail via n8n (§6.5, §6.6) | — |
| D7 | Co-admin reserva em todos os grupos | Sim: número pessoal ou 2º chip | A base sobrevive a um banimento |
| D8 | Canal do WhatsApp como complemento | Sim, manual no início | LGPD e alcance |
| D9 | Redirecionador próprio | **Não no MVP.** O ML veda encurtar ou modificar o link; Shopee e AliExpress têm subId/`tracking_id`. Reavaliar na Fase 3 | §6.4 |
| D10 | Cupom de afiliado ML com comissão menor | Regra de valor esperado explícita, decisão registrada | §4.5 |
| D11 | Medir concorrentes seguindo canais públicos | Só (hora, ID do item); nunca republicar | §4.8 |
| D12 | Orçamento e canal de aquisição de audiência nos 30 dias | R$ 0 nas semanas 1–2; teste pequeno e fixo na semana 3. **Evitar anúncio na Meta** no começo: liga a MegaPromoOz a uma conta de anúncios da Meta (R2). Preferir TikTok/Google, parcerias de nicho e orgânico | Medir custo por membro antes de escalar |
| D13 | Metas e corte do plano de 30 dias (§14) | O dono fixa os números antes de começar | Evitar mover a trave depois |
| D14 | Horas/semana que o dono aceita operar | Definir (ex.: ≤ 5 h/semana depois do mês 1) | Critério de interrupção |
| D15 | Pedir AliExpress agora | Sim, em paralelo, para não bloquear | A aprovação é externa e o prazo é desconhecido |
| D16 | Nome do repositório/pasta | Sugestão: `muofertas/` (padrão `mu*`) | Consistência com o workspace |

| D17 | Papel de cada marketplace no MVP | **Atualizado 10/10/2026: ML primeiro**, em curadoria assistida (mais vendidos + lista vigiada + link colado). Shopee entra quando aprovada (afiliado e Open API) como espinha dorsal automática; AliExpress quando aprovado, com portão de falsificação duro; Amazon **fora**; Magalu na Fase 5 | A Shopee não está aprovada (correção do dono) |
| D18 | Publicar o ML pela WuzAPI ou só à mão | Depende da resposta do suporte (R27). Até lá, ML só pelo adaptador `manual` | Cláusula 3.1.2 pode anular a comissão |
| D19 | ~~Alcance do caçador de cupons~~ | **Decidido:** fontes permitidas (API, e-mail próprio, canais seguidos) + scraping educado de páginas públicas de terceiros que permitam; Shopee e ML fora do scraping (§4.5) | — |
| D20 | Primeiras fontes de classe C | Levantar na Fase 0 de 5 a 10 candidatas (lojas oficiais e marcas dos 2 nichos), com termos e robots.txt lidos; o dono liga uma a uma | Começar pequeno; cada fonte é manutenção |
| D21 | Caixa de e-mail do caçador | Conta nova, só para isso, sem vínculo com contas pessoais; senha de app em arquivo/Env | Isolar e não misturar cupom pessoal do dono |
