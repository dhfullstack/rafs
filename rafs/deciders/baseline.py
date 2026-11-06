"""Controles do experimento: estratégia determinística simples e entradas aleatórias."""

from __future__ import annotations

import hashlib

from ..models import Action, Decision, Features, Position
from .base import Decider


class TrendBaseline(Decider):
    """Segue tendência: EMA12 > EMA26, RSI sem sobrecompra, momentum e preço acima da VWAP.
    Com `allow_short` (FUTURES) faz o espelho na queda: EMA12 < EMA26, RSI sem sobrevenda, momentum e VWAP negativos."""

    name = "baseline"

    def __init__(self, allow_short: bool = False):
        self.allow_short = allow_short

    def decide(self, f: Features, position: Position | None) -> Decision:
        if position is None:
            ok = f.ema_fast > f.ema_slow and 45 <= f.rsi <= 70 and f.momentum_pct > 0 and f.vwap_dev_pct > 0
            short = (self.allow_short and f.ema_fast < f.ema_slow and 30 <= f.rsi <= 55 and f.momentum_pct < 0
                     and f.vwap_dev_pct < 0)
            action = Action.BUY if ok else Action.SELL if short else Action.HOLD
            reason = (f"ema12>ema26={f.ema_fast > f.ema_slow} rsi={f.rsi:.1f} "
                      f"mom={f.momentum_pct:.2f}% vwap_dev={f.vwap_dev_pct:.2f}%")
        elif position.is_short:
            exit_ = f.ema_fast > f.ema_slow or f.rsi < 22
            action = Action.BUY if exit_ else Action.HOLD
            reason = f"short: ema12>ema26={f.ema_fast > f.ema_slow} rsi={f.rsi:.1f}"
        else:
            exit_ = f.ema_fast < f.ema_slow or f.rsi > 78
            action = Action.SELL if exit_ else Action.HOLD
            reason = f"ema12<ema26={f.ema_fast < f.ema_slow} rsi={f.rsi:.1f}"
        return Decision(self.name, f.symbol, f.ts, action, None, reason)


class RandomBaseline(Decider):
    """Moeda viciada reproduzível (mesma seed → mesmas decisões). Se o Jev não bater isso, não agrega."""

    name = "random"

    def __init__(self, seed: str = "rafs", p_buy: float = 0.08, p_sell: float = 0.08, allow_short: bool = False):
        self.seed, self.p_buy, self.p_sell, self.allow_short = seed, p_buy, p_sell, allow_short

    def _u(self, symbol: str, ts: int) -> float:
        h = hashlib.sha256(f"{self.seed}|{symbol}|{ts}".encode()).digest()
        return int.from_bytes(h[:8], "big") / 2**64

    def decide(self, f: Features, position: Position | None) -> Decision:
        u = self._u(f.symbol, f.ts)
        if position is None:  # short usa a outra ponta do sorteio: spot (sem short) decide igual a antes
            action = (Action.BUY if u < self.p_buy else
                      Action.SELL if self.allow_short and u > 1 - self.p_sell else Action.HOLD)
        else:
            action = (Action.BUY if position.is_short else Action.SELL) if u < self.p_sell else Action.HOLD
        return Decision(self.name, f.symbol, f.ts, action, None, f"u={u:.3f}")
