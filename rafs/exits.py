"""Distâncias de stop e alvo — uma conta só para o motor, o Jev e o Grok.

Stop e alvo saem do ATR. Em candle curto (1–3 min) o ATR fica do tamanho do custo de ida e volta (~0,2%),
e cada trade vira taxa. O piso `min_stop_pct` escala stop e alvo juntos (a relação alvo/stop não muda):
o bot olha o mercado a cada candle curto, mas só aposta em movimentos que pagam a taxa.
"""

from __future__ import annotations


def exit_scale(atr_pct: float, stop_atr_mult: float, min_stop_pct: float = 0.0) -> float:
    """Multiplicador (≥ 1) das distâncias por ATR para o stop ficar pelo menos em `min_stop_pct` % do preço."""
    stop_pct = stop_atr_mult * atr_pct
    if min_stop_pct <= 0 or stop_pct <= 0:
        return 1.0
    return max(1.0, min_stop_pct / stop_pct)


def exit_pcts(atr_pct: float, stop_atr_mult: float, take_profit_atr_mult: float,
              min_stop_pct: float = 0.0) -> tuple[float, float]:
    """(stop %, alvo %) a partir do ATR em %, já com o piso."""
    k = exit_scale(atr_pct, stop_atr_mult, min_stop_pct)
    return stop_atr_mult * atr_pct * k, take_profit_atr_mult * atr_pct * k
