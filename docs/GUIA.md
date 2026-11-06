# rafs — guia completo (PAPER / TESTNET / FUTURES demo)

Experimento para responder **uma** pergunta antes de construir qualquer coisa maior:

> O Jev, decidindo entradas e saídas, tem resultado líquido melhor que uma regra simples e que entradas aleatórias — com as mesmas taxas, slippage e regras de risco?

Três carteiras simuladas de R$ 200 rodam lado a lado sobre os mesmos dados:

| Carteira | Quem decide |
|---|---|
| `jev` | API do Jev (iajev.com) — só se `JEV_API_KEY` estiver configurada |
| `baseline` | Regra determinística: EMA12 > EMA26, RSI 45–70, momentum > 0, preço acima da VWAP |
| `random` | Moeda viciada reproduzível (controle) |

**Nada usa dinheiro real. Não existe modo LIVE neste MVP** — `MODE=LIVE` é recusado na inicialização.

| Modo | Dados | Execução |
|---|---|---|
| `PAPER` (padrão) | Binance real (público) | Simulada localmente (taxa + slippage fixos) |
| `TESTNET` | Binance Spot Testnet | **Ordens reais na testnet**, com dinheiro fictício |
| `FUTURES` | Binance Futures **demo** (USDT-M) | Ordens na conta demo, dinheiro fictício: **compra (long) e venda a descoberto (short)** a 1x |
| `BACKTEST` | Histórico da Binance real | Simulada, preenchimento na abertura do candle seguinte |

## Instalação (Windows)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Edite o `.env` (opcional: `JEV_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`,
`DASHBOARD_PASSWORD`).

## Comandos

```bash
.venv\Scripts\python -m rafs check                 # testa Binance (ou testnet), Jev e Telegram
.venv\Scripts\python -m rafs backtest --days 30 --sensitivity
.venv\Scripts\python -m rafs run                   # paper ao vivo (Ctrl+C para parar)
.venv\Scripts\python -m rafs run --dashboard --lan # idem + painel aberto para o iPhone na mesma Wi-Fi
.venv\Scripts\python -m rafs dashboard --lan       # só o painel (com o bot rodando em outro terminal)
.venv\Scripts\python -m rafs run --no-jev          # só baseline e aleatório
.venv\Scripts\python -m rafs report                # saldo, posições, comparação
.venv\Scripts\python -m rafs pause                 # pausa novas entradas
.venv\Scripts\python -m rafs resume                # libera (pede confirmação)
.venv\Scripts\python -m rafs kill                  # kill switch (arquivo data/KILL)
.venv\Scripts\python -m rafs kill --off
.venv\Scripts\python -m rafs dial                  # QUANT: dial de hoje (FRED + Binance), só imprime
.venv\Scripts\python -m rafs nightshift            # testa o dial e a regra crypto no histórico (grátis)
.venv\Scripts\python -m rafs desk-report           # relatório da mesa de hoje (o mesmo das 23:55 NY)
.venv\Scripts\python -m pytest -q                     # testes
```

`backtest --with-jev --jev-max-calls 50` inclui o Jev no backtest (gasta até 50 créditos).

## TESTNET (Binance Spot Testnet — grátis)

1. Entre em https://testnet.binance.vision → **Log In with GitHub** → **Generate HMAC_SHA256 Key**.
2. Cole a *API Key* e a *Secret Key* no `.env` (não no chat, não no código):
   ```
   TESTNET_API_KEY=...
   TESTNET_API_SECRET=...
   ```
   A secret só aparece uma vez. Essa chave **não** é da sua conta Binance real e não acessa dinheiro de verdade.
3. A conta da testnet vem com ~10.000 USDT e quase nada de BRL, então use pares em USDT só nela:
   `TESTNET_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT` (o PAPER continua em BRL pelo `SYMBOLS`). Nesse modo os limites
   200/50/15 valem em USDT, e o painel mostra os valores em USDT.
4. Deixe `MODE=PAPER` no `.env` e rode a testnet em **outro terminal** com `--mode`:
   ```bash
   .venv\Scripts\python -m rafs check --mode testnet
   .venv\Scripts\python -m rafs run --dashboard --lan         # terminal 1: PAPER (dados reais) + painel
   .venv\Scripts\python -m rafs run --mode testnet            # terminal 2: ordens na testnet
   ```
   Um painel só basta: o seletor no topo alterna entre PAPER e TESTNET.

Travas: o adaptador (`exchanges/ccxt_testnet.py`) se recusa a rodar se a URL privada não for de testnet; o banco é
separado (`data/rafs-testnet.sqlite3`); ids de ordem determinísticos evitam ordem duplicada após queda; na
inicialização o bot confere se o saldo da testnet cobre as posições do banco (a testnet é resetada de tempos em
tempos). A testnet costuma não cobrar taxa — o bot aplica a taxa real da Binance (0,1%) no resultado para não inflar.

**O que a testnet prova e o que não prova:** prova que o encanamento funciona (chave, assinatura, arredondamento,
mínimos, ordens, recuperação). **Não** prova lucro: o livro de ofertas da testnet é artificial e os preços podem
descolar do mercado real. Para medir desempenho da estratégia, o `PAPER` sobre dados reais é a referência.

## FUTURES (Binance Futures demo — compra e venda, dinheiro fictício)

No spot o bot só compra: em tendência de baixa ele espera. No `FUTURES` ele também **vende a descoberto**
(short): EMA12 < EMA26 vira candidato a short, o Grok lê com a tarefa de short, o Jev responde "o preço cai até
o alvo antes de subir até o stop?" e o baseline/random ganham o espelho da regra deles. O book `quant` da mesa
continua só comprado.

1. Entre em https://demo.binance.com → **API Management** → crie uma chave (HMAC). A conta demo vem com USDT
   fictício e **não** é a sua conta real.
2. Cole no `.env` (não no chat):
   ```
   FUTURES_API_KEY=...
   FUTURES_API_SECRET=...
   ```
3. Confira e rode:
   ```bash
   .venv\Scripts\python -m rafs check --mode futures
   .venv\Scripts\python -m rafs run --mode futures --dashboard --lan
   ```

Como funciona: perpétuos USDT-M (`BTC/USDT:USDT`…), **1x, margem isolada, modo one-way** (o bot configura na
partida e aborta se não conseguir 1x). Banca própria em USDT: `FUTURES_CAPITAL=1000`, `FUTURES_ENTRY=100`,
`FUTURES_MAX_EXPOSURE=300`, `FUTURES_DAILY_LOSS_LIMIT=50`, taxa `FUTURES_FEE_RATE=0.0005` (taker real da Binance
Futures). Short a 1x trava o valor da posição como margem; stop fica acima da entrada e alvo abaixo.

Travas: o adaptador (`exchanges/ccxt_futures.py`) se recusa a rodar se **qualquer** host de API não for `demo-*`;
banco separado (`data/rafs-futures.sqlite3`); ids de ordem determinísticos; na partida o bot confere a posição
líquida da demo contra a soma dos books.

Limitações: os books dividem **uma conta** em modo one-way, então a corretora mostra o líquido (long de um book e
short de outro se anulam; cada book tem seu P&L no banco). **Funding não entra no P&L.** A demo tem spread às
vezes largo e preço levemente diferente do real; o RISK bloqueia entrada com spread acima de 0,5%.
Tudo é **SIMULADO** — resultado da demo não é lucro real.

## Painel (iPhone / navegador)

O bot opera sozinho: decide, abre e fecha posições sem você tocar em nada. O painel serve para **acompanhar e
frear**, não para operar.

1. `.venv\Scripts\python -m rafs run --dashboard --lan`
2. Na primeira vez a senha é gerada e impressa no terminal (fica em `data/dashboard_password.txt`). Para escolher
   a sua, preencha `DASHBOARD_PASSWORD` no `.env`.
3. No iPhone, **na mesma Wi-Fi do PC**, abra no Safari o endereço `http://192.168.x.x:8787` que o terminal mostra.
   Se não abrir, libere a porta 8787 no Firewall do Windows (rede privada).
4. Compartilhar → **Adicionar à Tela de Início**. Vira um ícone "rafs" que abre em tela cheia, como um app.

Abas: **Visão** (patrimônio, caixa, P&L realizado/não realizado, retorno, drawdown, posições, status), **Ao vivo**
(cotações, sinais de cada carteira, ordens, trades), **Agentes** (os 8 agentes: status, última execução, tempo de
resposta, erros), **Desempenho** (curva, win rate, profit factor, expectância, taxas, veredito Jev × baseline ×
aleatório, histórico completo) e **Controles** (pausar, retomar, kill switch, limites e logs). Atualiza a cada 5 s.

Segurança: senha + sessão (cookie HttpOnly, 7 dias), CSRF, 5 tentativas de login a cada 5 min por IP, CSP estrita.
Retomar e desligar o kill switch pedem que você digite uma palavra de confirmação; toda ação vai para o log.
**Não existe** botão para sacar, ver chaves, mudar limites, trocar corretora ou ligar o LIVE — isso só no `.env`.
Todo valor aparece marcado como **SIMULADO**.

Limitações: funciona só enquanto o PC estiver ligado e só dentro da sua rede (sem `--lan` fica restrito ao próprio
PC). É HTTP sem criptografia — não exponha a porta na internet nem use em Wi-Fi pública.

## Discord

Só notificações (entradas, saídas, alertas), em paralelo ao Telegram:
Servidor → Configurações do canal → Integrações → Webhooks → **Novo webhook** → Copiar URL → cole em
`DISCORD_WEBHOOK_URL` no `.env`. O `check` testa o webhook sem postar nada. Um webhook só envia mensagens — não
recebe comandos, então não dá para pausar pelo Discord (use o painel, o Telegram ou a CLI).

## Telegram

Comandos: `/status /balance /positions /performance /pause /help`. Só o `TELEGRAM_CHAT_ID` configurado é atendido.
Não existe comando para retomar, sacar, mudar limites ou ver chaves — retomar é só pela CLI.

## Arquitetura (as 8 cadeiras do guia "The Hedge Fund Terminal")

| Cadeira | Onde | O que faz |
|---|---|---|
| CHIEF | `desk.py` + `deciders/grok_screen.py` | 06:00 NY prova as ferramentas (Grok, Jev, FRED, Binance, corretora); uma falha deixa só o sleeve dela DEGRADED. O Grok lê os números e as operações recentes e só escala ao Jev o que merece julgamento. 23:55 NY manda o relatório, montado do banco |
| QUANT | `dial.py` | Dial de risco do dia (6 verdes: liquidez, VIX, crédito HY, BTC>200d, SOL>50d, Nasdaq>200d) e alvos de momentum 12-1. Série atrasada conta como vermelho; nunca arredonda para cima; nunca opera |
| SCAN | `exchanges/ccxt_data.py` + `quant.py` | Candles e cotações dos pares fixos (descarta candle aberto, buracos e dado velho) e indicadores EMA, RSI, ATR, VWAP, regime |
| JUDGE | `deciders/jev.py` | O Jev julga só o que o Grok escalou: alvo antes do stop (entrada) ou SELL/HOLD (saída). Lê o dial, nunca o muda |
| STOCKS | — | Desligado: ações e Runner (TQQQ/SHY) precisam de Alpaca/QuantGPT |
| SIZE | `sizing.py` | Ticket × dial (o dial só reduz), reduz em volatilidade alta, rejeita abaixo do mínimo da corretora |
| FILLS | `exchanges/paper.py`, `exchanges/ccxt_testnet.py` + `engine.py` | Envio e confirmação; slippage acima de 25 bps e fill parcial são sinalizados, nunca absorvidos |
| RISK | `risk.py` + `desk.py` | Veto final: conta −4% no dia vira FLAT até amanhã, kill switch, pausa, exposição, 1 posição/ativo, perda diária, spread |

Cada agente grava status, última atividade, latência e último erro no banco (`agent:*`) durante o `run`; o
painel (`dashboard/`) só lê isso. Notificações em `notify.py` (Discord) e `telegram.py`.

Contratos exchange-agnostic em `exchanges/base.py` (`MarketDataProvider`, `ExchangeProvider`). Um adaptador real
futuro implementa a mesma interface; nenhum decider conhece a corretora.

**Saídas mecânicas iguais para todos:** stop em 2×ATR, alvo em 3×ATR, saída por tempo após 32 candles.
Se stop e alvo caem no mesmo candle, assume-se o stop (conservador).

**Backtest sem look-ahead:** decide no fechamento do candle t, executa na abertura de t+1 — o mesmo
`Engine.process_candle` do paper ao vivo.

**Recuperação após queda:** posições, caixa, ordens e decisões ficam em `data/rafs.sqlite3`. Ao reiniciar,
o estado é recarregado e candles já processados não são decididos de novo. A chave de idempotência do Jev é
derivada do candle, então um retry/restart não cobra duas vezes.

**Prompt injection:** o Grok e o Jev só recebem números calculados localmente e o símbolo da configuração (validado por
regex). Nenhum texto externo (nome de token, descrição, tweet) entra no estado.

## Fluxo Grok → Jev

```
QUANT (indicadores) → filtro de tendência (grátis) → Grok lê (barato) → Jev julga (caro, poucas/hora) → SIZE → RISK → FILLS
```

- Entrada: só pares com EMA12 > EMA26 chegam ao Grok. Ele lê os números e as últimas operações do ativo e responde
  `escalate` + `score` + resumo. Sem escalar, fica em HOLD com o motivo `grok: …` e o Jev não é chamado.
- Posição aberta: o Grok acompanha todo candle; o Jev só é consultado se o Grok vir a tese invalidada.
  Stop, alvo e tempo continuam automáticos.
- O Jev recebe só números (incluindo `grok_score`); o texto do Grok nunca entra no estado do Jev.
- API: `POST https://api.x.ai/v1/chat/completions` com `response_format` `json_schema` estrito
  ([docs](https://docs.x.ai)). Modelo padrão `grok-4.20-0309-non-reasoning` (US$ 1,25 entrada / 2,50 saída por
  milhão de tokens na tabela de 29/10/2026); ~US$ 0,001 por leitura. O custo real vem de `usage.cost_in_usd_ticks`
  e aparece no painel.
- `XAI_API_KEY` vazia: o Grok fica desligado e o Jev é consultado direto (comportamento anterior).
  `GROK_MAX_CALLS_PER_DAY` (padrão 400) limita o gasto; 429 pausa o Grok (1, 2, 4… até 30 min); chave inválida o desliga.
- O `score` de entrada é a chance estimada de bater o alvo antes do stop. `GROK_MIN_SCORE` (padrão 0 = desligado)
  manda ao Jev toda entrada com score acima do corte, mesmo sem `escalate`; quem decide a ordem continua sendo o Jev.
- Backtest não usa o Grok (cada candle histórico custaria uma chamada).
- `rafs check` confere a chave e se o modelo existe na conta.

## Jev — o que foi verificado

- API REST oficial: `POST https://iajev.com/api/v1/decisions/run`, `Authorization: Bearer`, `Idempotency-Key` obrigatório
  ([docs](https://iajev.com/docs/api)). Endpoint responde 401 sem chave.
- Uma chamada real em 2026-10-29 com o estado exato gerado pelo bot (SOL/BRL) retornou
  `HOLD` com probabilidade 1.0, latência 2,6 s, **1 crédito**. A resposta está como fixture em `tests/test_jev.py`.
- O cliente HTTP do bot foi testado com transporte simulado (retries, 409/402/401, validação). **Ainda não rodou com
  uma chave sua** — falta criar a chave em iajev.com e rodar `rafs check`.
- Custo: ~1 crédito por decisão. Com 15m e 3 ativos, até 288 chamadas/dia por bot; `JEV_MAX_CALLS_PER_DAY` limita (0 = ilimitado, padrão). Sem créditos (HTTP 402), o decider Jev se desliga sozinho.
- Filtro de tendência (`JEV_TREND_GATE=1`, padrão): sem posição aberta, o Jev só é consultado quando EMA12 > EMA26; fora disso fica em HOLD sem gastar chamada. Com posição aberta ele é consultado todo candle para decidir a saída. O painel mostra as chamadas poupadas na aba Agentes.
- Estilo de entrada (`JEV_ENTRY_STYLE`, padrão `trend`): com `fade` o Jev só avalia entrada quando o preço está a `FADE_MIN_DEV_PCT` % (padrão 1,0) ou mais da VWAP de 48 candles, do lado da volta à média (compra abaixo, short acima em FUTURES), e o Grok e o Jev recebem prompts de reversão (faca caindo e squeeze são os riscos). Backtest de 60 dias em 15m, entradas aleatórias, resultado SIMULADO: a favor da EMA 29% de acerto, fade de 1% 40%, aleatório 31%. Nenhum cobre as taxas sozinho; o filtro só dá ao Jev candidatas melhores. Grok e Jev também leem `trend24h_pct` (preço vs média de 96 candles), `mom4h_pct` e `range24h_pos` (0 = mínima de 24h, 1 = máxima).
- Prompt v2 (`JEV_PROMPT_VERSION=v2`, padrão desde 29/10/2026): a entrada pergunta "o preço bate o alvo (+3× ATR) antes do stop (−2× ATR) nos próximos N candles?". A probabilidade de TARGET é a chance de ganho; compra se ela passar do ponto de equilíbrio `(stop% + custos%) / (alvo% + stop%)` + `JEV_EDGE_MARGIN`. Cada decisão guarda a versão do prompt (`raw.prompt`), para não misturar resultados da v1 e da v2. A saída continua com a pergunta SELL/HOLD.
- Teto por hora (`JEV_MAX_CALLS_PER_HOUR`, 0 = sem teto): a chave do Jev passou a recusar com HTTP 429 depois de ~10 chamadas por hora (limite não publicado pelo iajev). Com o teto em 8, o bot para de consultar antes do bloqueio; a contagem vem do banco e sobrevive a restart.
  O teto vale para o conjunto das chaves: em 30/10 as 11 travaram juntas depois de ~89 chamadas no dia (cota da
  conta, não da chave) e só voltaram de madrugada. Com 4/h e `JEV_MAX_CALLS_PER_DAY=80` o Jev fica disponível o dia todo.
- Limite de requisições (HTTP 429): o Jev pausa sozinho (1, 2, 4… até 30 min) em vez de insistir; stop, alvo e saída por tempo continuam valendo.
  Quando entradas estão pausadas/kill switch, o Jev nem é consultado.
- `confidence`/probabilidade do Jev **não é probabilidade calibrada de lucro**. Na chamada real veio 1.0 — já
  sinal de excesso de confiança.

## Resultado do primeiro backtest (30 dias, 15m, BTC/ETH/SOL em BRL, 2026-09-29 → 2026-10-29)

Taxa 0,10%/lado (Binance), slippage 5 bps. **Simulado.**

| decider | trades | líquido R$ | win % | PF | taxas R$ | maxDD % |
|---|---|---|---|---|---|---|
| baseline | 267 | −9,82 | 34,5 | 0,42 | 7,41 | 5,10 |
| random | 395 | −13,64 | 28,1 | 0,33 | 10,97 | 6,98 |

Timeframes maiores (180 dias) também foram negativos:

| timeframe | baseline (trades / líquido R$ / PF) | random (trades / líquido R$ / PF) |
|---|---|---|
| 1h | 424 / −16,31 / 0,66 | 624 / −27,13 / 0,51 |
| 4h | 55 / −6,23 / 0,42 | 75 / −4,47 / 0,54 |

No 4h o aleatório perdeu menos que o baseline — a regra simples não tem vantagem demonstrada.

Mesmo com slippage zero, os dois perdem. As taxas são ~75% do prejuízo do baseline: em candles de 15 min o
movimento típico (ATR ≈ 0,2%) é do mesmo tamanho do custo de ida e volta (≈ 0,3%). Para ter chance, o Jev
precisa acertar muito mais do que isso — ou o timeframe precisa ser maior (teste `TIMEFRAME=1h` / `4h`).

Candle curto (ex.: `TIMEFRAME=2m`, montado do 1m porque a Binance não tem 2m): o ATR de 2 min fica do tamanho
da taxa, então use `MIN_STOP_PCT` (ex.: 0.8 → stop −0,8% / alvo +1,2%) e `MAX_HOLD_CANDLES` maior (120 = 4h).
Backtest 14d em futuros (SIMULADO, saída do baseline por cruzamento de EMA): 2m perdeu 25–50% contra 3,5% no 15m —
decidir mais vezes não cria vantagem, só mais taxa.
`STOP_COOLDOWN_CANDLES` (padrão 0): depois de um stop o book fica N candles sem entrar no mesmo ativo — ao vivo
em 2m o Jev invertia a mão logo após o stop e tomava outro. Backtest 30d em 15m (SIMULADO, baseline): sem nada
−84; piso 0,8% −65; piso 0,8% + pausa 2 −62. Ajuda pouco: a regra fixa continua perdendo.

## Mesa (guia "The Hedge Fund Terminal")

Réplica do fluxo do guia do savip (dropday.ai, 13/out/2026) com o que temos: Binance testnet no lugar da Alpaca,
FRED + Binance pública no lugar do QuantGPT, Discord no lugar do Telegram. Relógio de Nova York:

| Hora (NY) | Quem | O quê |
|---|---|---|
| 06:00 | CHIEF | Prova as ferramentas. Vermelha: tenta de novo a cada 30 min; o sleeve dela fica parado até passar |
| 06:00 | QUANT | Publica o dial. Sem dial válido (< 24 h): NO DIAL, NO DESK |
| o dia todo | SCAN → Grok → Jev → SIZE → RISK → FILLS | Books de 15m. Com `DESK_RULES=1`, só entra se dial crypto ≥ 0,50 e o par está nos alvos 12-1; ticket × dial |
| o dia todo | RISK | Book com patrimônio 4% abaixo da abertura do dia fecha tudo e fica FLAT até amanhã. Número que mexe > 10% gera aviso |
| 20:05 | book `quant` | Vende o que saiu dos alvos (ou tudo se dial < 0,50); compra os alvos com `MAX_EXPOSURE × dial × peso`, limitado ao ticket |
| 23:55 | CHIEF | Relatório: dial, checagens, candidatos barrados e por quê, fills com slippage, fechamentos com a regra, banca |

Grok e Jev recebem só a linha numérica `desk crypto_dial=… btc_vs_200d=…`. Cada um guarda 20% do orçamento do
dia para posições abertas: passou de 80%, não entra mais nada novo.

O que do guia **não** existe aqui: ações e o Runner (Alpaca/QuantGPT) e alavancagem 3x. O dial de stocks é
calculado só para referência. O sleeve de memecoins existe em PAPER (seção abaixo).

### Sleeve de memecoins (PAPER, banca SIMULADA)

Liga com `MEME_SLEEVE=1` (padrão). Roda numa thread própria dentro do `run`, sem tocar na Binance:

| Etapa | Quem | O quê |
|---|---|---|
| a cada 15 min | SCAN | GeckoTerminal (solana + bsc): pools em alta 1h/6h + maior volume 24h. Corte do guia: 15 min a 72 h de vida, liquidez > US$ 12k, volume 24h > US$ 40k, mcap US$ 60k–8M. Descarta vendas > compras em 5m **e** 15m e honeypot. Ranqueia por holders ganhos por hora; preço subindo mais rápido que os holders vira "empurrado" e vai para o fim |
| | CHIEF (Grok) | Lê os até 10 candidatos e separa até 7 que valem uma chamada do Jev, com resumo e riscos |
| | JUDGE (Jev) | Escolhe **um** ou `NONE`. Se escolheu, uma segunda chamada dá a chance de bater o alvo antes do stop |
| | SIZE | Kelly com essa chance (alvo +50%, stop −25%), teto 6% da banca, × dial meme, no máximo 2% da liquidez do pool, piso onde a taxa mínima deixa de pesar |
| | FILLS | Simulados: impacto pelo pool x·y=k (metade da liquidez de cada lado) + taxa 0,45% (mínimo US$ 0,95). Nada é enviado a corretora nenhuma |
| a cada 5 min | RISK | Sai se a liquidez cair abaixo de 25% da entrada, volume 6h < 20% da média do dia, stop, alvo ou 24 h. Banca −4% no dia de NY → fecha tudo e fica FLAT até amanhã |

Book de controle `memes_top`: compra sempre o #1 do SCAN com ticket fixo 6% × dial e as mesmas saídas, sem Grok e
sem Jev. Se depois de 30 trades fechados cada um `memes` não superar `memes_top` em líquido e profit factor, a IA
não está agregando. Dial meme abaixo de 0,50 ou sem dial: escaneia e registra, nada vai ao JUDGE.

**Proteção contra prompt injection:** nome, símbolo, descrição e links do token são texto de quem criou a moeda.
Grok e Jev recebem só rótulos `C1..Cn` e números (`liq=… mcap=… age_h=… holders_h1=…`); o Grok que devolve um rótulo
que não existe tem a resposta descartada. Os nomes aparecem só no painel e no Discord.

**Orçamento:** Jev e books de 15m usam a mesma chave. Os memes contam no teto por hora (`JEV_MAX_CALLS_PER_HOUR`),
usam no máximo metade dele e respeitam a mesma pausa depois de um 429. Grok e Jev têm teto diário próprio
(`MEME_GROK_MAX_CALLS_PER_DAY`, `MEME_JEV_MAX_CALLS_PER_DAY`).

Diferenças em relação ao guia: GeckoTerminal no lugar do FOMO (API grátis; na prática o 429 vem antes de 10 chamadas/min, então o bot faz 5/min e o SCAN lê
pools em alta e de maior volume em vez de todo pool novo); taxa do FOMO imitada, não cobrada; saídas por alvo,
stop e tempo acrescentadas porque o Kelly precisa de uma aposta com ganho e perda definidos. **Todo valor dessa
aba é simulado: não é lucro nem prejuízo real.**

### Validação (NIGHT SHIFT, 29/10/2026)

`rafs nightshift` recalcula o dial de cada dia só com dados já publicados naquele dia e mede o que veio depois.
A janela vai de 2023-12-04 a 2026-10-28 (1.060 dias), limitada pela série de crédito HY do FRED:

- **O dial crypto não separa dias bons de ruins.** BTC nos 5 dias seguintes rendeu +0,25% com dial ≥ 0,50 e +0,21%
  abaixo. A diferença foi −0,01% nos primeiros 70% e −0,20% nos últimos 30%. Os dias de dial 0 renderam mais (+1,04%).
- **A regra inteira (12-1 top 2 × dial) cortou a queda.** No pedaço que a regra nunca viu (últimos 30%): +5,0%,
  Sharpe 0,63, pior queda −4,4%, contra BTC parado −5,9%, Sharpe 0,06, pior queda −39,5%. Ficou investida em
  51% dos dias, com 104 ordens.
- **Veredito: TRADE-OFF.** O que protege é ficar em caixa quando o momentum 12-1 é negativo, não o dial.
  Três anos, um ciclo, simulação com custo de 10 bps por lado: não é promessa de lucro.

O resultado de US$ 75 mil do guia é promocional e não auditado; a arquitetura (LLM nunca envia ordem, RISK
determinístico, dial aritmético, validação fora da amostra) é o que vale copiar.

## Critério de parada (definido antes de ver resultados do Jev)

Após **≥ 100 trades fechados** na carteira `jev` em PAPER:
- expectativa líquida por trade ≤ 0, **ou**
- não supera `random` **e** `baseline` em líquido e profit factor

→ parar ou mudar a hipótese. Sem "só mais uma semana".

## Limitações conhecidas

- Sem LIVE. Memes só em PAPER, sem nenhum caminho para ordem real.
- Preço dos memes vem do GeckoTerminal com atraso de até ~1 min; o fill simulado não captura MEV nem sandwich.
- Validação do dial limitada a ~3 anos (série BAMLH0A0HYM2 do FRED só a partir de 2023-10).
- Sem memecoins/Solana/Pump.fun: latência do Jev (~2,5 s) é incompatível com sniping; fica para pesquisa futura.
- Survivorship bias: só BTC/ETH/SOL, que sobreviveram — resultados tendem a ser otimistas.
- Slippage é um valor fixo; não usa livro de ofertas. Ordens de R$ 15 têm impacto desprezível nesses pares.
- Vendas abaixo do mínimo da corretora são aceitas no paper (na Binance real poderiam travar).
- `ENTRY_BRL=15` em vez de 10: com R$ 10 o arredondamento da quantidade de BTC (passo 0,00001) cai abaixo do
  mínimo de R$ 10 e a ordem é rejeitada.
- Rodar 24/7 exige o PC ligado ou uma VPS (~R$ 25–50/mês) — que custa mais do que R$ 200 rendem.
- Impostos sobre cripto no Brasil não são calculados.
