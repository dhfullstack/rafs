"""Book "quant" — o sleeve crypto do guia: rotação de momentum 12-1 × dial, sem LLM.

Não decide por candle: a Desk compra os alvos do QUANT e o RISK vende o que saiu deles, às 20:05 de Nova York.
Ele existe como book separado para comparar, com o mesmo capital, a regra do guia com o Jev, o baseline e o aleatório.
"""

from __future__ import annotations

from ..models import Action, Decision, Features, Position
from .base import Decider


class QuantRotation(Decider):
    name = "quant"
    per_candle = False  # o motor pula este book no loop de candles (sem stop/alvo/tempo de 15m)

    def decide(self, f: Features, position: Position | None) -> Decision:
        return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, "quant: opera só às 20:05 NY")
