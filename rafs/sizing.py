"""SIZE — só reduz ou rejeita; nunca aumenta acima de ENTRY_BRL."""

from __future__ import annotations

from .config import Settings
from .exchanges.base import TradingRules
from .portfolio import Book

TARGET_ATR_PCT = 1.0  # acima disso, o tamanho cai proporcionalmente


def size_entry(s: Settings, book: Book, rules: TradingRules, price: float, atr_pct: float,
               dial: float = 1.0) -> tuple[float, str]:
    """Retorna (quantidade, motivo). Quantidade 0 significa rejeitado. `dial` (0..1) é o primeiro multiplicador."""
    dial = min(1.0, max(0.0, dial))  # o dial só reduz; confiança de modelo nunca aumenta o tamanho
    if dial <= 0:
        return 0.0, "dial 0: sleeve parado"
    notional = s.entry_brl * dial
    if atr_pct > TARGET_ATR_PCT:
        notional *= TARGET_ATR_PCT / atr_pct
    notional = min(notional, s.max_exposure_brl - book.exposure(), book.cash)
    if notional <= 0:
        return 0.0, "sem espaço de exposição ou saldo"
    qty = rules.floor_amount(notional / price)
    if qty * price < rules.min_cost:
        return 0.0, f"{qty * price:.2f} abaixo do mínimo da corretora ({rules.min_cost:.2f})"
    return qty, f"{qty * price:.2f}"
