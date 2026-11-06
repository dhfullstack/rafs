"""Contratos exchange-agnostic. Agentes falam com estas interfaces, nunca com APIs de corretora."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import Candle


@dataclass(frozen=True)
class ExchangeCapabilities:
    name: str
    spot: bool
    order_book: bool
    cancel_orders: bool
    live_execution: bool  # False para PAPER
    short: bool = False  # vende a descoberto (FUTURES demo)


@dataclass(frozen=True)
class TradingRules:
    symbol: str
    min_cost: float  # notional mínimo na moeda de cotação
    amount_step: float  # incremento mínimo de quantidade

    def floor_amount(self, amount: float) -> float:
        if self.amount_step <= 0:
            return amount
        steps = math.floor(amount / self.amount_step + 1e-9)
        return round(steps * self.amount_step, 12)


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    ts: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else float("inf")


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str  # determinístico → evita ordens duplicadas após restart
    symbol: str
    side: str  # "buy" | "sell"
    amount: float
    ref_price: float  # preço de referência (ask/bid atual ou abertura do próximo candle)
    reduce: bool = False  # fecha posição (venda de long, recompra de short): sem mínimo de abertura


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    symbol: str
    side: str
    amount: float
    price: float  # preço médio executado (inclui slippage)
    fee: float  # na moeda de cotação
    status: str  # "filled" | "rejected"
    simulated: bool
    note: str = ""


class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_closed_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def get_trading_rules(self, symbol: str) -> TradingRules: ...


class ExchangeProvider(ABC):
    @abstractmethod
    def get_capabilities(self) -> ExchangeCapabilities: ...

    @abstractmethod
    def get_trading_rules(self, symbol: str) -> TradingRules: ...

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult: ...
