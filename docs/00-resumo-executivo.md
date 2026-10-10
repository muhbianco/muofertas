# MegaPromoOz — Resumo executivo

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## Contexto

Projeto novo do MuhBianco: plataforma que descobre, avalia, classifica e distribui ofertas de
afiliado (Mercado Livre, Shopee, AliExpress) em grupos de WhatsApp por nicho, e aprende com os
resultados. Este documento é o entregável da Fase 0, para ser revisado antes de qualquer código.

**Fatos confirmados com o dono em 10/10/2026**
- Os grupos ainda não existem: audiência zero.
- Aprovado **só no afiliado do Mercado Livre**. Shopee e AliExpress ainda não (correção do dono em
  10/10/2026: a Shopee não está aprovada). Ver "Correção de premissa" logo abaixo.
- Distribuição por **WuzAPI desde o início**, na instância **`muconnect`**. Ela já é só interna
  (o DNS foi removido). A conexão do número nasce da interface da ferramenta nova, por API.
- **Exceção ao congelamento de deploy** (em vigor desde 09/10, por causa do App Review da Meta)
  para este projeto. Depois de **todo** deploy, o Chatwoot é conferido; o dono testa à mão quando
  for preciso. Motivo declarado: o potencial de caixa aqui é maior.
- Marca pública dos grupos: **MegaPromoOz**.
- **Sem Telegram.** Toda aprovação e operação acontece no **painel web, pensado para celular**.
  Alertas críticos chegam por e-mail (webhook n8n próprio do produto).
- **Caçador de cupons em background** com fontes permitidas: APIs oficiais, caixa de e-mail
  própria assinando newsletters, canais oficiais seguidos pelo número e **scraping educado de
  páginas públicas de terceiros** cujos termos e robots.txt permitam. **Sem scraping de Shopee ou
  ML** (os contratos de afiliado vedam).

> **Correção de premissa (10/10/2026, depois da aprovação do levantamento): começar pelo Mercado
> Livre.** A Shopee **não** está aprovada. Onde este documento diz "Shopee = espinha dorsal
> automática", leia: **a Shopee entra quando for aprovada (afiliado e, depois, Open API)**. A
> arquitetura não muda, porque o conector da Shopee vira só mais um adaptador.
>
> **O MVP passa a ser ML-primeiro, em curadoria assistida:**
> - **Descoberta:** mais vendidos por categoria (`/highlights`, top 20) e lista vigiada que o
>   operador alimenta colando links. Não há busca por palavra-chave nem feed de ofertas relâmpago
>   para afiliado.
> - **Motor:** vigia esse universo e acusa queda real de preço (`/items/{id}/sale_price`).
> - **Link:** manual, na fila "Links ML pendentes", cerca de 1 min por link.
> - **Envio:** pela WuzAPI só depois que o suporte responder a P5; até lá, adaptador manual.
> - **Mensagem:** só com o que o anúncio mostra (P6).
> - **Volume:** com audiência zero, 2 grupos × 4–6 posts/dia = 8–12 links/dia, uns 10–15 min/dia
>   do operador. Cabe.
> - **Caminho crítico:** a sondagem do ML (P4). Se um app de não-vendedor não ler mais vendidos,
>   item e preço de venda, o motor do ML vira só lista manual.

**Legenda de evidência**
- **[OFICIAL]**: documentação ou termos oficiais.
- **[FORNECEDOR]**: publicado pela empresa, ainda depende de validação prática.
- **[COMUNIDADE]**: relato de terceiros.
- **[HIPÓTESE]**: hipótese de negócio.
- **[RECOMENDAÇÃO]**: recomendação técnica.
- **[TESTAR]**: exige teste de viabilidade.

---

## 1. Resumo executivo

**Visão.** Um motor de curadoria comercial (não um robô de volume) para a MegaPromoOz:
- descobre ofertas pelas APIs oficiais;
- separa desconto real de preço inflado com **histórico de preço próprio**;
- classifica com regras explicáveis;
- publica pouco e verificado no grupo certo;
- mede comissão por publicação para aprender.

**Viabilidade**

| Dimensão | Avaliação | Por quê |
|---|---|---|
| Técnica | **Alta** | A Shopee fecha o ciclo inteiro por API oficial: catálogo com comissão por item, feeds diários, link com 5 subIds, relatório de conversão. A stack da casa e a hel1 (~8,8 GB livres) comportam o projeto |
| Dados de mercado | **Média** | Não existe fonte terceira legítima de histórico de preço para ML/Shopee; tem de ser construído (e leva semanas). O ML **não tem API de afiliado** e fechou a busca por palavra-chave |
| Distribuição | **Frágil** | Não há caminho oficial para automatizar grupos grandes (a Groups API trava em 8 participantes). A WuzAPI viola os termos do Business app desde 23/09/2026. O dono aceitou o risco |
| Comercial | **Incerta** | Audiência zero, e a conversão não tem benchmark confiável. O gargalo dos primeiros 60 dias é **audiência**, não algoritmo |

**Cinco descobertas que mudam o desenho**
1. **Shopee = espinha dorsal automática.** WhatsApp e Telegram são canais permitidos por escrito.
   Tem atribuição por publicação via subId. Em contrapartida: não traz cupom na API, a comissão
   base é de 3% e o extra do vendedor decide.
2. **Mercado Livre = curadoria assistida.**
   - Link só manual. Os termos proíbem encurtar ou modificar o link e não pagam venda ligada a
     "software/robôs".
   - Os termos de desenvolvedor restringem repassar dados e publicar estatística derivada.
   - Paga bem (12–16% direto), então o tempo humano vai para itens ML de alto valor.
3. **Desconto real exige série própria.** O coletor precisa ligar **até ~27/10** para comprovar
   desconto na Black Friday (27/11).
4. **Cupom estruturado só existe na API da AliExpress.** O resto vem do **caçador em
   background**: campanhas da Shopee, caixa de e-mail própria assinando newsletters, canais
   oficiais seguidos e scraping educado de páginas públicas de terceiros que permitam. Os sites
   da Shopee e do ML ficam fora, porque os contratos de afiliado vedam scraping. Só cupom
   CONFIRMADO entra no "preço final"; cupom pessoal nunca sai.
5. **Regras brasileiras viram requisito de produto:**
   - "#publi" na primeira linha (Guia CONAR 2026, que cita afiliados);
   - preço à vista em reais;
   - "de/por" só com registro documental do preço anterior;
   - filtro de falsificação (camisas e tênis lideram a pirataria).

**Riscos principais** (§12)
- Restrição ou banimento do número, e correlação com a identidade MuhBianco na Meta (R1/R2).
- Audiência não crescer (R4).
- O ML tratar a publicação automatizada como uso indevido (R27).
- Desconto enganoso ou falsificação (R9/R10).

**Recomendação em uma frase.** Ligar o coletor já. Começar com Ferramentas + Maquiagem: Shopee
automática e ML assistido, poucos posts verificados, WuzAPI com isolamento de identidade e modo
manual de reserva. Decidir o resto com os dados do dia 30.

---

## 15. Resposta à pergunta central

**A melhor estratégia é construir primeiro um motor de curadoria com histórico de preço próprio
— não um robô de volume.** Ele publica pouco e só o que pode provar, em dois nichos, e transforma
cada publicação em dado de comissão. Em ordem de prioridade:

1. **Ligar o coletor antes de tudo (meta ~27/10).**
   - O ativo que a concorrência não copia é saber se o desconto é real.
   - Ele só existe com semanas de série própria, e a Black Friday é o teste natural.
2. **Shopee como espinha dorsal automática, ML como curadoria assistida, AliExpress depois.**
   - A Shopee é a única com ciclo completo por API oficial, com WhatsApp permitido e atribuição
     por publicação.
   - O ML paga mais por pedido, mas o link é manual e os termos vetam automação de link e
     redistribuição de dados. O tempo humano vai para o ML de alto valor.
3. **Decisão determinística e auditável.**
   - Portões → nota explicável → níveis → snapshot imutável.
   - LLM fora do caminho crítico.
   - Machine learning só depois de ~1.000 publicações e ~200 conversões, e só se bater a regra.
4. **Poucas mensagens, todas verificáveis.**
   - Revalidação até 5 min antes do envio; envio no máximo uma vez; deduplicação no banco.
   - "#publi" na primeira linha.
   - Nenhum "% OFF" sem série própria.
   - Cupom caçado só vira "preço final" quando é CONFIRMADO.
5. **Um painel web para celular como único console.**
   - Aprovar, colar link do ML, conectar o número e vincular grupo a nicho.
   - Alertas críticos por e-mail.
6. **Ferramentas + Maquiagem primeiro** (complementares em ticket e recompra).
   - Tênis no mês 2, com portão duro de loja oficial.
   - Bolsas e camisas em modo sombra.
7. **WhatsApp pela WuzAPI, como o dono decidiu, mas blindado:**
   - identidade MegaPromoOz isolada da MuhBianco;
   - co-admin humano em todos os grupos;
   - entrada só por link e ritmo humano;
   - modo manual de reserva;
   - porta `Channel` para trocar de transporte sem reescrever o motor.
8. **Medir o que paga.**
   - Comissão por publicação (subId Shopee), por grupo (etiqueta ML) e por membro (ARPM).
   - Comparar com horas de operação e com custo por membro.
   - **Audiência é o gargalo dos primeiros 60 dias:** o esforço de aquisição começa na semana 1.
9. **Cortar ou crescer no dia 30**, com as metas da §14 fixadas antes de começar.
