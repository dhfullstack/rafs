"""Textos de status/performance usados pela CLI e pelo Telegram. Tudo marcado como SIMULADO."""

from __future__ import annotations

from datetime import datetime, timezone

from .metrics import comparison_table, performance
from .storage import Storage

DISCLAIMER = "⚠️ PAPER/TESTNET — sem dinheiro real. Valores SIMULADOS, não são lucro real."


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def status_text(storage: Storage, settings, last_prices: dict[str, float], kill: bool) -> str:
    lines = [DISCLAIMER,
             f"Modo: {settings.mode} | {settings.timeframe} | {', '.join(settings.symbols)}",
             f"Entradas pausadas: {'SIM' if storage.is_paused() else 'não'} | Kill switch: {'ATIVO' if kill else 'off'}"]
    for sym in settings.symbols:
        ts = storage.get(f"last_ts:{sym}")
        if ts:
            lines.append(f"  {sym}: último candle {_ts(int(ts))} close {last_prices.get(sym, 0):.2f}")
    if "jev" in storage.book_names():
        lines.append(f"Jev: créditos gastos {storage.credits_spent('jev'):.0f}")
    return "\n".join(lines)


def balance_text(storage: Storage, capital: float, last_prices: dict[str, float]) -> str:
    lines = [DISCLAIMER]
    for name in storage.book_names():
        b = storage.load_book(name, capital)
        eq = b.equity(last_prices)
        lines.append(f"{name}: patrimônio R$ {eq:.2f} | caixa R$ {b.cash:.2f} | exposição R$ {b.exposure():.2f} | "
                     f"realizado R$ {b.realized_total():+.2f} | não realizado R$ {b.unrealized(last_prices):+.2f}")
    return "\n".join(lines)


def positions_text(storage: Storage, capital: float, last_prices: dict[str, float]) -> str:
    lines = [DISCLAIMER]
    for name in storage.book_names():
        b = storage.load_book(name, capital)
        if not b.positions:
            lines.append(f"{name}: sem posições")
        for p in b.positions.values():
            px = last_prices.get(p.symbol, p.entry_price)
            lines.append(f"{name}: {p.symbol}{' SHORT' if p.is_short else ''} {p.qty:g} @ {p.entry_price:.2f} "
                         f"→ {px:.2f} ({p.pnl_pct(px):+.2f}%) "
                         + (f"stop {p.stop_price:.2f} alvo {p.take_profit_price:.2f}" if p.take_profit_price > 0
                            else "saída pelo RISK das 20:05 NY"))
    return "\n".join(lines)


def performance_text(storage: Storage, capital: float) -> str:
    rows = {}
    for name in storage.book_names():
        b = storage.load_book(name, capital)
        curve = [e for _, e in storage.equity_curve(name)] or None
        rows[name] = performance(b.trades, b.initial_capital, curve)
    return f"{DISCLAIMER}\n{comparison_table(rows)}"
