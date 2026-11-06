# Procedimento para um futuro modo LIVE

O MVP **não** executa ordens reais. `MODE=LIVE` é recusado em `rafs/config.py`.

Pré-requisitos antes de sequer escrever um adaptador real:

1. Critério de parada do README atendido **a favor** do Jev (≥ 100 trades em PAPER, expectativa líquida > 0,
   supera `baseline` e `random`).
2. Adaptador real implementando `ExchangeProvider` (`exchanges/base.py`) com:
   - chave de API **sem permissão de saque** e com whitelist de IP;
   - reconciliação de saldo/posições com a corretora a cada ciclo;
   - confirmação do estado real da ordem antes de registrar posição;
   - tratamento de mínimo de venda (posições "presas" abaixo do mínimo).
3. Testes do adaptador contra o testnet da corretora (Binance Spot Testnet) antes de dinheiro real.
4. Capital inicial menor que o planejado (ex.: R$ 50), limites do `.env` revisados.
5. Ativação manual, explícita, pelo dono — nunca automática.
