"""BACKTEST — reusa o mesmo Engine do PAPER sobre histórico.

Sem look-ahead: a decisão usa candles fechados até t e é executada na ABERTURA de t+1.
Survivorship bias: só testamos BTC/ETH/SOL, que sobreviveram — resultados tendem a ser otimistas.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .config import Settings
from .deciders.base import Decider
from .engine import FEATURE_WINDOW, Engine, FillRefs
from .exchanges.base import TradingRules
from .exchanges.paper import PaperExchange
from .metrics import Performance, performance
from .models import Candle
from .risk import RiskEngine
from .storage import Storage


def run_backtest(settings: Settings, deciders: list[Decider], histories: dict[str, list[Candle]],
                 rules: Callable[[str], TradingRules], storage: Storage | None = None) -> Engine:
    storage = storage or Storage(":memory:")
    exchange = PaperExchange(rules, settings.fee_rate, settings.slippage_bps)
    engine = Engine(settings, storage, deciders, exchange, RiskEngine(settings, kill_switch_check=lambda: False))
    events = sorted(
        (candles[i].ts, symbol, i)
        for symbol, candles in histories.items()
        for i in range(FEATURE_WINDOW - 1, len(candles) - 1)
    )
    for _, symbol, i in events:
        candles = histories[symbol]
        nxt = candles[i + 1].open
        engine.process_candle(symbol, candles[i - FEATURE_WINDOW + 1: i + 1], FillRefs(nxt, nxt, None))
    return engine


def summarize(engine: Engine) -> dict[str, Performance]:
    out = {}
    for name, book in engine.books.items():
        curve = [e for _, e in engine.storage.equity_curve(name)] or None
        out[name] = performance(book.trades, book.initial_capital, curve)
    return out


def split_summary(engine: Engine, split_ts: int) -> tuple[dict[str, Performance], dict[str, Performance]]:
    """Desempenho por período (pela data de entrada): primeiros 70% vs últimos 30%."""
    first, second = {}, {}
    for name, book in engine.books.items():
        first[name] = performance([t for t in book.trades if t.entry_ts < split_ts], book.initial_capital)
        second[name] = performance([t for t in book.trades if t.entry_ts >= split_ts], book.initial_capital)
    return first, second


def cost_sensitivity(settings: Settings, make_deciders: Callable[[], list[Decider]],
                     histories: dict[str, list[Candle]], rules, slippages=(0, 5, 10, 25)) -> dict[float, dict[str, Performance]]:
    return {
        bps: summarize(run_backtest(replace(settings, slippage_bps=bps), make_deciders(), histories, rules))
        for bps in slippages
    }
