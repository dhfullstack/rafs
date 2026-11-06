"""Adaptador PAPER — simula execução com taxa e slippage. Nunca toca em dinheiro real."""

from __future__ import annotations

from typing import Callable

from .base import ExchangeCapabilities, ExchangeProvider, OrderRequest, OrderResult, TradingRules


class PaperExchange(ExchangeProvider):
    def __init__(self, rules: Callable[[str], TradingRules], fee_rate: float, slippage_bps: float):
        self._rules = rules
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000
        self._seen: dict[str, OrderResult] = {}

    def get_capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(name="paper", spot=True, order_book=False, cancel_orders=False, live_execution=False)

    def get_trading_rules(self, symbol: str) -> TradingRules:
        return self._rules(symbol)

    def place_order(self, order: OrderRequest) -> OrderResult:
        if order.client_order_id in self._seen:  # idempotência
            return self._seen[order.client_order_id]
        rules = self._rules(order.symbol)
        amount = rules.floor_amount(order.amount)
        if order.side == "buy":
            price = order.ref_price * (1 + self.slippage)
        elif order.side == "sell":
            price = order.ref_price * (1 - self.slippage)
        else:
            raise ValueError(f"side inválido: {order.side}")
        notional = amount * price
        if amount <= 0 or (not order.reduce and notional < rules.min_cost):
            result = OrderResult(order.client_order_id, order.symbol, order.side, 0.0, 0.0, 0.0, "rejected", True,
                                 f"abaixo do mínimo ({notional:.2f} < {rules.min_cost})")
        else:
            result = OrderResult(order.client_order_id, order.symbol, order.side, amount, price,
                                 notional * self.fee_rate, "filled", True)
        self._seen[order.client_order_id] = result
        return result
