"""Carteira simulada (uma por decider) — mesmo capital, mesmos custos, mesmas regras de saída."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Position, Trade


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Book:
    name: str
    cash: float
    initial_capital: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)

    def exposure(self) -> float:
        return sum(p.cost_brl for p in self.positions.values())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.value(prices.get(p.symbol, p.entry_price)) for p in self.positions.values())

    def unrealized(self, prices: dict[str, float]) -> float:
        return sum(p.unrealized(prices.get(p.symbol, p.entry_price)) for p in self.positions.values())

    def realized_on(self, day: str) -> float:
        return sum(t.pnl for t in self.trades if utc_day(t.exit_ts) == day)

    def realized_total(self) -> float:
        return sum(t.pnl for t in self.trades)
