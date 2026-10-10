# Arquitetura recomendada

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 6. Arquitetura recomendada

### 6.1 Visão
Monólito modular em Python, um repositório novo, uma imagem e dois processos (web e worker),
rodando numa stack Swarm nova na hel1. Usa o MariaDB nativo da hel1. Sem Redis no MVP.

```text
                     ┌──────── worker (1 réplica, lease no banco) ─────────┐
 APIs dos programas ─►│ conectores → normalização → observações de preço    │
 entrada manual      │ → avaliação (portões, nota, nível, decisão)         │
                     │ → agenda → revalidação → envio (porta Canal)        │
                     │ → importação de relatórios                          │
 Caçador de cupons ─►│ APIs · caixa IMAP · canais seguidos · scraping      │
                     │ educado (fontes liberadas) → cupom + evidência      │
                     └───────────────┬─────────────────────────────────────┘
                                     │ MariaDB 10.11 (schema próprio)
                     ┌───────────────┴──────── web (FastAPI) ──────────────┐
 Operador (celular) ►│ painel mobile-first (host admin, TOTP)              │
 WuzAPI ────────────►│ webhook da sessão (HMAC por instância, rede interna)│
                     │ → alertas críticos por e-mail (webhook n8n)         │
                     └───────────────┬─────────────────────────────────────┘
                                     │ overlay interna (a mesma da api-agents)
                          muconnect / WuzAPI (sem rota pública) ◄──► WhatsApp (chip MegaPromoOz)
```

### 6.2 Módulos e fronteiras
A ordem das camadas é **transporte** (rotas FastAPI, jobs do worker) → **casos de uso** →
**domínio** (puro e testável sem I/O) → **infraestrutura** (repositórios SQLAlchemy, clientes
httpx).

| Módulo | Responsabilidade | Fica de fora |
|---|---|---|
| `sources` | Conectores por fonte (`shopee_affiliate`, `ml_api`, `manual`, depois `aliexpress_affiliate`); limitador por fonte; kill switch | Regra de negócio |
| `catalog` | Marketplace, vendedor, anúncio, variação, atribuição de nicho | Preço |
| `pricing` | Observações, `P_ref`, mínimas, sinais S1–S5 | Envio |
| `coupons` | Registro de cupons, status, público/pessoal, cálculo de elegibilidade e desconto | Fonte específica |
| `coupon_hunter` | Fontes do caçador: API, caixa IMAP, canais seguidos, scraping educado. Política por fonte (registro, robots.txt, ritmo, disjuntor) e extração | Regra de preço |
| `scoring` | Portões, componentes, níveis, `ruleset` versionado, registro de decisão | I/O |
| `distribution` | Rota, agenda, deduplicação, revalidação, renderização da mensagem | Detalhe do WhatsApp |
| `channels` | Porta `Channel.send() → SendResult` com adaptadores `wuzapi_group`, `wuzapi_newsletter` (se a versão suportar), `dry_run`, depois `telegram` e `cloud_api` | Regra comercial |
| `links` | Geração de link por programa (subIds Shopee, `tracking_id` AliExpress, link ML colado pelo operador e validado), checagem de saúde via API | Encurtar ou alterar o link (vedado no ML) |
| `analytics` | Cliques, importação de relatórios, estados de comissão | — |
| `ops` | Execuções de coleta, erros, alertas, lease, kill switches | — |
| `admin` | Painel | Lógica de domínio |

Trocar WuzAPI por Cloud API, Telegram ou outro provedor = escrever outro adaptador de `channels`.
O motor não muda.

### 6.3 Escolhas técnicas
- **Python 3.12, FastAPI, SQLAlchemy 2 async (asyncmy), Alembic, pydantic-settings, httpx com
  timeout explícito.** É o padrão da `api-agents` e da `api-commerce`, então tem custo zero de
  aprendizado e de ferramental (ruff, pytest).
- **MariaDB 10.11 (nativo na hel1).**
  - Schema e usuário próprios, com grants só nesse schema. Entra no backup diário cifrado para o
    B2, junto com os outros (`hel1-ops/scripts/backup-muhbianco.sh`), e ganha um restore de prova.
  - **Por que não Postgres/Timescale:** com gravação só na mudança e índice `(listing_id,
    observed_at)`, o volume do MVP cabe em InnoDB. Uma segunda engine seria operação a mais sem
    necessidade medida. Reavaliar por medição (EXPLAIN, p95) se `price_observation` passar de
    ~100 milhões de linhas.
  - **Atenção de operação:** o `innodb_buffer_pool_size` da hel1 é de 128 MB (lido em 10/10). Ele
    é compartilhado por todos os produtos e tem de ser revisto antes de o histórico crescer.
- **Fila sem Redis.**
  - Tabelas `job` e `publication` com `SELECT … FOR UPDATE SKIP LOCKED` (MariaDB ≥ 10.6 ✔), mais
    um worker asyncio com agendador interno.
  - O volume é de dezenas de envios por dia e milhares de chamadas por hora: folgado para o
    banco. Fila no banco também dá auditoria e idempotência na mesma transação.
  - Saída conhecida, se medirmos necessidade de paralelismo multiprocesso: Celery + Redis, como o
    ADR 0004 do mucommerce.
- **Worker singleton.** Lease no banco que se cura sozinho (lição do `engine_lease` do Mu Tower)
  e `update_config: order: stop-first` na stack, para nunca haver dois agendadores durante um
  deploy.
- **Painel server-rendered** (Jinja2 + HTMX) no próprio serviço web, numa imagem só. Se um dia
  virar produto multiusuário, migrar para Next.js, como `mucommerce/apps/web`.
- **WuzAPI** (decisão do dono):
  - Chamada só pela rede interna, sem roteador Traefik.
  - O painel faz procuração de QR/código de pareamento.
  - Copiar as travas que já existem em `api-agents/app/services/whatsapp_owned/client.py`:
    - token de sessão aleatório e cifrado;
    - HMAC por instância no webhook (hex do corpo cru, header `x-hmac-signature`);
    - eventos só `Message`;
    - rotas conferidas no `spec.yml` da imagem em uso, nunca na memória.
  - Instância `muconnect` compartilhada, por decisão do dono. O token admin fica restrito a duas
    operações: ver §9.4.
- **n8n.** Fora do caminho crítico. No máximo e-mail de alerta, por webhook próprio deste produto
  (um fluxo por produto, como os outros).
- **LLM.** Opcional e fora do caminho crítico (§4.6). Se entrar: modelo pequeno, timeout, saída
  validada por schema, orçamento por chamada, log de modelo/tokens/latência, sem dado pessoal no
  prompt.
- **CI/CD.** Woodpecker (`.woodpecker/ci.yaml` → `deploy.yaml`), imagem
  `muhrilobianco/<nome>:<sha12>`, como os outros repos. Push na `main` = deploy.
- **Capacidade.** Em 10/10/2026 a hel1 tinha ~8,8 GB de RAM disponível, load ~0,5 e 75 GB livres
  em disco. Limites da stack: web 256 MB, worker 384 MB, WuzAPI 256 MB.

### 6.4 Links, rastreio e atribuição

| Programa | Como o link nasce | Granularidade da atribuição | Fonte do resultado |
|---|---|---|---|
| **Shopee** | `generateShortLink`/`generateBatchShortLink` (até 50 por chamada) com **até 5 subIds**, que voltam em `utmContent` [OFICIAL — explorer da Open API]. `subId1` = código da publicação, `subId2` = grupo, `subId3` = versão do template | **Por publicação** (observada) | `conversionReport` (pedido, status, comissão bruta e líquida, `clickTime`); pedidos aparecem em até 24 h |
| **Mercado Livre** | **Manual**, no gerador do portal ou na barra de afiliados. Não há API de afiliado, e gerar link por endpoint interno com cookie da sessão cai na cláusula 3.1.2 (venda por "uso indevido de software, APIs, robôs" não paga) [OFICIAL] | **Por grupo**: uma **etiqueta** por grupo/nicho (≤ 30 caracteres, não apagável, métricas filtráveis a cada 3 h) [OFICIAL] | Painel: cliques, vendas e ganhos por etiqueta. Atribuição por publicação = **estimada** (janela de 24 h × item) |
| **AliExpress** | `link.generate` com `tracking_id` (até 50 por chamada) [OFICIAL] | Por `tracking_id`; `custom_parameters` volta no pedido [TESTAR como gravá-lo no link] | `order.list` |

- **Sem redirecionador próprio no MVP.**
  - Os termos do afiliado ML **proíbem encurtar ou modificar o link** (1.8) [OFICIAL].
  - Na Shopee, o subId já dá atribuição por publicação.
  - Um salto extra pode quebrar a abertura direta no app do marketplace [TESTAR].
  - Se o domínio cair, todos os links antigos morrem.
  - Reavaliar na Fase 3, só para Shopee/AliExpress, e só se faltar contagem de clique por
    publicação. Primeiro conferir se o painel da Shopee exporta cliques por sub_id [TESTAR].
- **Fluxo do link ML (assistido).**
  1. O robô escolhe a oferta ML.
  2. O item entra na fila **"Links ML pendentes"** do painel, com botão que abre o anúncio e a
     etiqueta certa do grupo já indicada.
  3. O operador gera o link no portal/app do ML e cola no painel (no celular é copiar e colar).
  4. O robô valida o formato do link, revalida o preço e agenda.
  - O volume do ML fica limitado pelo tempo do operador. Por isso o ML entra como **curadoria
    assistida** e a Shopee como **espinha dorsal automática**.
- **Nada nosso "clica" no link de afiliado.**
  - A saúde do link é conferida pela API: item ativo, mais o formato do link gerado.
  - Prévia de link desligada (§4.9).
  - Assim, clique de robô não suja o relatório e não arrisca a regra antifraude dos programas
    (AliExpress 1.7; ML 3.1.2).

### 6.5 Painel (web, pensado para celular) — o único console da operação

**Mobile-first.** A aprovação acontece no celular, pelo navegador:
- cartões grandes com foto, preço, nota e o motivo em uma linha;
- **Aprovar / Rejeitar / Bloquear** ao alcance do polegar;
- **idempotência**: um segundo toque não aprova de novo, e cada ação grava `decision` com
  `decided_by=operator`;
- pode ser instalado como atalho na tela inicial (PWA simples).

| Tela | Faz |
|---|---|
| **Fila de aprovação** | Ofertas que pedem decisão (calibração, Excepcional, Suspeita), em cartões, com filtros por nicho, marketplace, nível, desconto e nota |
| **Links ML pendentes** | Itens ML escolhidos que esperam o link colado (§6.4) |
| Detalhe da oferta | Gráfico do histórico, decomposição da nota, portões, cupons aplicados, snapshot da decisão |
| **Número** | Conectar o número pela API (QR ou código de pareamento por procuração), estado da sessão, reconectar, desconectar |
| **Grupos** | Lista os grupos em que o número está (`/group/list`). Cria grupo novo pela API, liga "só admins enviam" e gera/renova o link de convite. **Vincula cada grupo a um nicho.** Define a política: mensagens/dia, intervalo, janela, pausar/retomar |
| **Nichos** | O que cada nicho **pesquisa**: categorias por marketplace, palavras-chave, marcas permitidas e de alto risco, lista vigiada, pesos e limiares da nota. Pausar o nicho para tudo |
| **Cupons** | Cupons caçados, com fonte, evidência, status (CONFIRMADO / INFORMADO / EXPIRADO), público × pessoal e validade. Cadastro manual. Fontes do caçador com estado, último erro e kill switch |
| Publicações | Agenda, enviadas, falhas/`unknown` |
| Saúde | Fontes de dados, erros, alertas abertos, uso das cotas de API |

**Resposta à pergunta do dono.** Sim: conectar o número e gerenciar os grupos ligados a cada
nicho de pesquisa fazem parte do painel desde a Fase 2. A tela **Nichos** existe já na Fase 1,
porque é ela que diz ao coletor o que procurar.

- **Adiar:**
  - relatórios comerciais ricos (nas 4 primeiras semanas, SQL + exportação CSV bastam);
  - multiusuário;
  - edição de template pela tela.
- **Segurança do painel**
  - Host próprio.
  - Senha com argon2 + TOTP.
  - Cookie `HttpOnly`/`Secure`/`SameSite=Strict`, CSRF e rate limit no login.
  - Negar por padrão.
  - Nenhum segredo de programa exibido ou gravado no banco: segredos só em arquivo do host ou no
    Env da stack, pelo `portainer-stack-update.py`.

### 6.6 Alertas (sem Telegram)
- **No painel.** Central de alertas com contador visível em todas as telas. Todo alerta tem
  estado (aberto / reconhecido / resolvido).
- **Críticos também por e-mail**, pelo webhook n8n próprio deste produto. Um fluxo por produto,
  como os outros; apontar para o fluxo de outro produto responde 400 em silêncio.
  - O que é crítico: sessão caiu ou foi deslogada, publicação `unknown`, fonte parada há mais de
    6 h, ou fila parada.
  - Throttle por tipo de alerta (no máximo 1 e-mail a cada 30 min por tipo), para não virar spam.
- **Nunca** pelo número de ofertas.
- **Evolução (Fase 2+):** Web Push pelo PWA do painel. Fica no celular, sem terceiro; no iOS,
  exige o atalho instalado.

| Alerta | Quando dispara |
|---|---|
| Sessão WuzAPI | caiu ou deslogou |
| Fonte | N falhas seguidas, ou 0 itens numa coleta que costuma trazer itens |
| Publicação | falhou ou ficou `unknown` |
| Oferta Excepcional | aguardando aprovação (na fase de calibração) |
| Link | quebrado |
| Revalidação | taxa de "preço mudou" acima do normal |
| Relatório de conversão | atrasado |
| Cupom | expirado ainda referenciado |
