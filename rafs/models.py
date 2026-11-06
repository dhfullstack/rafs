"""Tipos internos compartilhados. Nenhum agente depende de tipos de corretora."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Candle:
    ts: int  # abertura do candle, epoch ms (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Features:
    """Snapshot numérico reproduzível usado por todos os deciders."""

    symbol: str
    ts: int
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    atr_pct: float
    vwap_dev_pct: float
    momentum_pct: float
    volatility_pct: float
    rel_volume: float
    regime: str  # "trend_up" | "trend_down" | "range"
    # contexto de prazo maior (24h em 15m): o que um trader olha antes de entrar num candle curto
    trend24h_pct: float = 0.0  # preço vs média das últimas 96 velas (%): acima = tendência de alta no dia
    mom4h_pct: float = 0.0  # variação das últimas 16 velas (%)
    range24h_pos: float = 0.5  # onde o preço está no range máx/mín das últimas 96 velas (0 = fundo, 1 = topo)


@dataclass(frozen=True)
class Decision:
    decider: str
    symbol: str
    candle_ts: int
    action: Action
    confidence: float | None  # NÃO é probabilidade calibrada de lucro
    reason: str
    raw: dict = field(default_factory=dict)
    cost_credits: float = 0.0


@dataclass
class Position:
    book: str
    symbol: str
    qty: float
    entry_price: float
    entry_fee: float
    entry_ts: int  # ts do candle que gerou a decisão de entrada
    stop_price: float
    take_profit_price: float
    side: str = "long"  # "short" só em FUTURES; padrão mantém posições antigas do banco válidas

    @property
    def cost_brl(self) -> float:
        return self.qty * self.entry_price  # short a 1x: é a margem travada

    @property
    def is_short(self) -> bool:
        return self.side == "short"

    def value(self, price: float) -> float:
        """Quanto volta ao caixa fechando a `price`, sem taxa. Short a 1x: margem + ganho (ou − perda) do short."""
        return self.qty * (2 * self.entry_price - price) if self.is_short else self.qty * price

    def unrealized(self, price: float) -> float:
        return self.qty * ((self.entry_price - price) if self.is_short else (price - self.entry_price))

    def pnl_pct(self, price: float) -> float:
        return (price / self.entry_price - 1) * (-100 if self.is_short else 100)


@dataclass(frozen=True)
class Trade:
    book: str
    symbol: str
    qty: float
    entry_price: float
    exit_price: float
    entry_ts: int
    exit_ts: int
    fees: float
    pnl: float  # líquido de taxas e slippage
    exit_reason: str
    side: str = "long"
