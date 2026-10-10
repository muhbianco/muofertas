# Estratégia de WhatsApp

> Levantamento da Fase 0 da MegaPromoOz, aprovado pelo dono em 10/10/2026.
> As referências §N apontam para as seções numeradas destes arquivos.
> Legenda: [OFICIAL] [FORNECEDOR] [COMUNIDADE] [HIPÓTESE] [RECOMENDAÇÃO] [TESTAR].

## 9. Estratégia de WhatsApp

### 9.1 O que cada caminho realmente faz (out/2026)

| Opção | Oficial? | Posta em grupo | Posta em Canal | Custo | Risco de banimento | Encaixe para 5 grupos de ofertas |
|---|---|---|---|---|---|---|
| **WuzAPI + chip dedicado (decisão do dono)** | **Não** | Sim: texto, imagem, menções, `announce`, convite (código da v1.0.9) [OFICIAL — repositório] | Lista, entra e sai; enviar texto a `@newsletter` provavelmente funciona, mídia provavelmente não [TESTAR] | Servidor (já existe) + chip | **Médio a alto, impossível de estimar** (§9.2) | Técnico: total. Contratual: viola os termos |
| Outras não oficiais (Z-API, Evolution/Baileys, W-API, WAHA, Whapi) | Não | Sim | Algumas | Mensalidade ou servidor | Mesma classe da WuzAPI | Igual à WuzAPI; nenhuma vantagem de risco |
| Cloud API — **Groups API** (desde 06/10/2025) | Sim | **Máximo de 8 participantes por grupo**; exige Official Business Account, **nunca concedida a número do Business app** [OFICIAL] | Não | Por mensagem × participante | Baixo | **Inviável** |
| Cloud API — templates 1:1 de marketing | Sim | Não | Não | ~R$ 0,32 por mensagem de marketing (tabela BRL citada por fornecedor). 1.000 pessoas × 10 ofertas/dia ≈ R$ 96 mil/mês | Baixo | **Inviável no custo** |
| Coexistence (app + Cloud API no mesmo número) | Sim | Grupos não sincronizam; Canais "not supported" [OFICIAL] | Não | — | Baixo | Não resolve |
| Canal do WhatsApp postado à mão no app | Sim | — | Sim, seguidores ilimitados; seguidores não veem números uns dos outros [OFICIAL] | R$ 0 | Baixo | Bom complemento, e melhor para a LGPD; manual |
| Telegram (Bot API) | **Sim** | Sim (20 msg/min por grupo) | Sim (ilimitado) | R$ 0 | Baixo | Tecnicamente ótimo; alcance menor no Brasil (instalado em 57% dos smartphones contra 99% do WhatsApp, jan/2025) [FORNECEDOR] |

**Conclusão técnica [OFICIAL].** Hoje não existe caminho oficial que automatize postagem em grupos
grandes no WhatsApp: a Groups API trava em 8 pessoas, e 1:1 por template custa caro demais. A
porta `Channel` (§6.2) mantém a troca possível se a Meta mudar isso. Conferir o changelog a cada
trimestre.

### 9.2 O risco do caminho escolhido, sem atenuar
- **Contrato.**
  - Termos do WhatsApp Business app, em vigor desde 23/09/2026, cláusula 5(g): proíbe "develop or
    use any applications that interact with our Business App Services without our prior written
    consent".
  - Business Messaging Policy §7: uso não autorizado pode levar à proibição de "all future use".
  - Depois de uma conta encerrada, **é proibido criar outra conta comercial sem permissão**.
  - Messaging Guidelines: clientes não oficiais contam como comportamento adversarial.
  - O README da própria WuzAPI avisa que o número pode ser banido. [OFICIAL]
- **Técnica.**
  - Conta restrita perde os dispositivos vinculados, então **a automação para inteira** durante a
    restrição.
  - Mudança de protocolo derruba o login até sair atualização: foi o caso do "Client outdated
    405", resolvido na v1.0.9 em 17/09/2026.
  - Com o celular sem uso por ~14 dias, o dispositivo vinculado desconecta.
  - A WuzAPI desiste de reconectar depois de 3 tentativas na inicialização. [OFICIAL]
- **Relatos** [COMUNIDADE]: avisos "account at risk" e restrições aparecem até com volume baixo;
  ninguém publica os limiares.
- **Específico do MuhBianco** [HIPÓTESE sobre o mecanismo, FATO sobre o histórico]:
  - O portfólio da Meta foi desabilitado em 06/06/2025 por exatamente esta classe de uso.
  - O dossiê de apelação afirma que a infraestrutura não oficial foi removida.
  - Há App Review em curso desde 09/10/2026.
  - Se a Meta correlacionar o número de ofertas com a identidade do dono, o dano pode chegar ao
    **canal oficial do produto principal**. Não há como medir essa probabilidade.
  - O dono decidiu seguir com WuzAPI. As mitigações abaixo reduzem a exposição, mas não a zeram.

### 9.3 Como operar a WuzAPI reduzindo exposição (obrigatório antes do 1º envio)
1. **Isolar a identidade.**
   - Chip e número sem vínculo com o Business Portfolio, o Facebook, o Meta Verified/Meta One ou o
     CNPJ do MuhBianco.
   - Marca dos grupos própria e diferente de "MuhBianco".
   - Nunca cadastrar esse número no WhatsApp Manager.
2. **A base sobrevive ao número.**
   - Um co-admin humano (número reserva) em **todos** os grupos desde o dia 1.
   - Links de convite guardados no painel.
   - Não está verificado o que acontece com um grupo cujo único admin é banido; não pagar para
     descobrir.
3. **Entrada só por link de convite**, com aprovação de entrada se a operação aguentar.
   - **Nunca** adicionar participante pela API (`updateparticipants add`): é o padrão clássico de
     spam.
   - Grupos com `announce` (só admins enviam).
4. **Ritmo humano.**
   - Intervalos com variação aleatória.
   - Teto por hora e por dia (§4.7).
   - Texto diferente por nicho.
   - Nunca a mesma mensagem nos grupos no mesmo minuto.
   - Sem menção em massa.
   - **Nenhuma mensagem 1:1 automatizada** saindo desse número: DM recebida não tem resposta
     automática.
5. **Sessão.**
   - Celular principal em uso bem antes de 14 dias (checagem semanal com alerta).
   - Webhooks `LoggedOut`, `TemporaryBan`, `ConnectFailure`, `ClientOutdated` e `Disconnected` viram
     alerta.
   - Reconexão externa com backoff, porque a WuzAPI desiste sozinha.
   - Re-pareamento pelo painel (QR/código por procuração, como pediu o dono).
6. **Versão.**
   - Imagem da WuzAPI fixada por tag/digest, nunca `latest`.
   - Atualização rápida e testada quando o WhatsApp mudar o protocolo.
   - Spec conferido na imagem em uso.
7. **Modo degradado.** Se a sessão cair ou o número for restrito, a agenda **não perde** as
   ofertas. O painel passa a mostrar texto e imagem prontos para postar à mão pelo celular do
   co-admin: adaptador `manual` da porta `Channel`.
8. **Conteúdo.** Filtros da §4.9 (falsificação, álcool, medicamentos, apostas, desconto
   enganoso).

### 9.4 Instância WuzAPI: `muconnect` compartilhada (decisão do dono, 10/10/2026)
A ferramenta usa a `muconnect`, que já é só interna (DNS removido). O que isso exige:
- **Rede.** A stack nova entra na overlay onde a `muconnect` atende (`http://wuzapi:8080`, mesma
  rede usada pela api-agents). A porta não ganha roteador Traefik.
- **Token admin com o menor uso possível.** O token admin da WuzAPI **lista todas as sessões, com
  número e token de cada uma**, inclusive as dos lojistas do modo expor.
  - A ferramenta só usa esse token em **duas operações**: criar a sessão do número de ofertas e
    ler o JID dela.
  - O uso diário (conectar, QR/código, status, enviar) é feito com o **token da própria sessão**.
  - O cliente admin da ferramenta só aceita a instância com o nome/prefixo da MegaPromoOz.
  - O cliente nunca devolve, loga ou mostra a lista de sessões.
  - O token fica no Env da stack (`portainer-stack-update.py`), nunca no banco nem na tela.
- **Webhook e HMAC por instância.** A sessão MegaPromoOz tem webhook próprio, apontando para o
  serviço web da stack nova pela rede interna, com chave HMAC própria. Mesmo padrão já usado na
  api-agents. Os eventos de um não chegam ao outro.
- **Ciclo de vida compartilhado.** Atualizar, reiniciar ou ter um incidente na `muconnect` atinge
  **os dois produtos**.
  - Upgrade da imagem passa a ser coordenado: conferir o `spec.yml`, testar a sessão de ofertas e
    a de um lojista, e só então seguir.
  - Os alertas de sessão dos dois lados disparam juntos numa queda da instância.
- **Correlação (R2).** O número de ofertas e os números dos clientes ficam no mesmo cliente
  não oficial, no mesmo IP. O dono aceitou esse aumento de exposição.

### 9.5 Formato da audiência
- **Um grupo por nicho**, limite de 1.024 membros. Abrir "Grupo 2" ao chegar a ~900.
- **Comunidade não serve para escala:** soma no máximo **2.000 membros entre todos os grupos**
  [OFICIAL].
- **Canal do WhatsApp como complemento** [RECOMENDAÇÃO]:
  - seguidores ilimitados, e ninguém vê o número de ninguém (melhor para a LGPD);
  - sem notificação ruidosa;
  - postagem por WuzAPI só se o envio a `@newsletter` passar no teste; senão, postagem manual a
    partir do painel.
- **Telegram:** adaptador oficial e barato para a Fase 5, ou antes como seguro se o WhatsApp
  restringir.

### 9.6 LGPD nos grupos
- **Controlador.** O operador é controlador do que trata. A exceção de uso pessoal (art. 4º, I)
  não se aplica a atividade econômica.
- **Base legal:** consentimento por entrada via link com aviso claro, ou legítimo interesse com
  teste de balanceamento registrado.
- **Na prática:**
  - descrição do grupo com link para aviso de privacidade (quem, quais dados, finalidade, prazo,
    contato, como sair);
  - canal de contato com o titular (obrigatório mesmo para pequeno porte, Res. CD/ANPD 2/2022);
  - não guardar membros (§7.4);
  - não registrar números em log.
- **Lei paulista "Não Me Ligue" (13.226/2008, alterada pela 17.334/2021).** Estendeu o bloqueio a
  mensagens por aplicativo, mas aparece como revogada no site da ALESP [OFICIAL, situação atual
  não confirmada]. Entrar só por link evita a questão.
