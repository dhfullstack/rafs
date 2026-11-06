"""FILLS em FUTURES — ordens na conta DEMO de futuros USDT-M da Binance (demo.binance.com), dinheiro fictício.

Compra (long) e venda a descoberto (short) a 1x, margem isolada. Os books dividem uma conta só em modo
one-way: a posição da corretora é a soma líquida dos books (long de um e short de outro se anulam).

Trava de segurança: o adaptador se recusa a operar se QUALQUER host de API não for `demo-*`.
Não existe caminho neste arquivo para operar na conta real.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import ccxt

from .base import ExchangeCapabilities, ExchangeProvider, OrderRequest, OrderResult, TradingRules
from .ccxt_testnet import NotSandboxError, to_client_id

log = logging.getLogger("rafs.futures")
LEVERAGE = 1


def perp(symbol: str) -> str:
    """BTC/USDT → BTC/USDT:USDT (perpétuo liquidado em USDT). Books e banco seguem com o par sem sufixo."""
    if ":" in symbol:
        return symbol
    return f"{symbol}:{symbol.split('/')[1]}"


def make_futures_exchange(api_key: str = "", secret: str = ""):
    """binanceusdm em modo demo. Sem chave serve só para dados públicos (candles e cotação)."""
    ex = ccxt.binanceusdm({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 15000,
                           "options": {"adjustForTimeDifference": bool(api_key), "recvWindow": 10000}})
    ex.enable_demo_trading(True)
    assert_demo(ex)
    return ex


def assert_demo(ex) -> None:
    api = ex.urls.get("api")
    urls = list(api.values()) if isinstance(api, dict) else [api]
    bad = [u for u in urls if not (urlparse(str(u)).hostname or "").startswith("demo-")]
    if not urls or bad:
        raise NotSandboxError(f"URL de API fora da conta demo: {bad or urls!r}. Abortando por segurança.")


class CCXTFuturesExchange(ExchangeProvider):
    def __init__(self, exchange, fee_rate: float, poll_attempts: int = 10, sleep=time.sleep):
        assert_demo(exchange)
        self.ex = exchange
        self.fee_rate = fee_rate
        self.poll_attempts = poll_attempts
        self._sleep = sleep
        self._markets: dict | None = None

    def _load(self) -> dict:
        if self._markets is None:
            self._markets = self.ex.load_markets()
        return self._markets

    def get_capabilities(self) -> ExchangeCapabilities:
        return ExchangeCapabilities(name="binanceusdm-demo", spot=False, order_book=True, cancel_orders=True,
                                    live_execution=False, short=True)

    def get_trading_rules(self, symbol: str) -> TradingRules:
        m = self._load().get(perp(symbol))
        if not m or not m.get("active", True):
            raise ValueError(f"perpétuo indisponível na demo: {symbol}")
        return TradingRules(symbol, float((m["limits"].get("cost") or {}).get("min") or 0),
                            float(m["precision"].get("amount") or 0))

    def prepare(self, symbols) -> list[str]:
        """One-way, margem isolada e 1x em cada par. "Já está assim" não é erro; falhar o 1x é (liquidação)."""
        notes = []

        def attempt(label, fn):
            try:
                fn()
            except ccxt.ExchangeError as e:
                if "No need to change" not in str(e):
                    notes.append(f"{label}: {e}")

        attempt("modo one-way", lambda: self.ex.set_position_mode(False))  # vale para a conta toda
        for sym in symbols:
            attempt(f"margem isolada {sym}", lambda s=perp(sym): self.ex.set_margin_mode("isolated", s))
            try:
                self.ex.set_leverage(LEVERAGE, perp(sym))
            except ccxt.BaseError as e:
                raise RuntimeError(f"não consegui pôr {sym} em {LEVERAGE}x na demo: {e}") from e
        return notes

    def balances(self) -> dict[str, float]:
        bal = self.ex.fetch_balance()
        return {k: float(v or 0) for k, v in (bal.get("free") or {}).items()}

    def net_positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.ex.fetch_positions():
            qty = float(p.get("contracts") or 0)
            if qty:
                sym = p["symbol"].split(":")[0]
                out[sym] = out.get(sym, 0.0) + (qty if p.get("side") == "long" else -qty)
        return out

    def reconcile(self, books) -> list[str]:
        """Confere a posição líquida da demo contra a soma dos books (long − short) em cada par."""
        need: dict[str, float] = {}
        for book in books:
            for p in book.positions.values():
                need[p.symbol] = need.get(p.symbol, 0.0) + (-p.qty if getattr(p, "side", "long") == "short" else p.qty)
        have = self.net_positions()
        issues = []
        for sym in sorted(set(need) | set(have)):
            want, got = need.get(sym, 0.0), have.get(sym, 0.0)
            tol = max(self.get_trading_rules(sym).amount_step, 1e-9) / 2
            if abs(want - got) > tol:
                issues.append(f"{sym}: banco espera líquido {want:+g}, demo tem {got:+g}")
        return issues

    # ------------------------------------------------------------------ ordens
    def place_order(self, order: OrderRequest) -> OrderResult:
        sym = perp(order.symbol)
        cid = to_client_id(order.client_order_id)
        existing = self._find(sym, cid)  # idempotência: já foi enviada antes de uma queda?
        if existing is None:
            if not order.reduce:
                free = self.balances().get("USDT", 0.0)
                if free < order.amount * order.ref_price / LEVERAGE:
                    return self._reject(order, f"margem USDT insuficiente na demo ({free:.2f})")
            amount = self.get_trading_rules(order.symbol).floor_amount(order.amount)
            if amount <= 0:
                return self._reject(order, "quantidade zero depois do arredondamento")
            # sem reduceOnly: a conta é líquida entre books, o fechamento de um pode abrir o lado do outro
            try:
                existing = self.ex.create_order(sym, "market", order.side, amount, None,
                                                {"clientOrderId": cid, "newOrderRespType": "RESULT"})
            except (ccxt.NetworkError, ccxt.RequestTimeout):
                existing = self._find(sym, cid)  # resultado incerto: consulta antes de qualquer retry
                if existing is None:
                    raise
            except ccxt.InsufficientFunds as e:
                return self._reject(order, f"margem insuficiente na demo: {e}")
            except ccxt.InvalidOrder as e:
                return self._reject(order, f"ordem inválida: {e}")
        final = self._wait_final(sym, existing)
        return self._to_result(order, sym, final)

    def _find(self, sym: str, cid: str):
        try:
            return self.ex.fetch_order(None, sym, {"origClientOrderId": cid})
        except ccxt.OrderNotFound:
            return None

    def _wait_final(self, sym: str, o: dict) -> dict:
        for _ in range(self.poll_attempts):
            if o.get("status") in ("closed", "canceled", "rejected", "expired"):
                return o
            self._sleep(1)
            o = self.ex.fetch_order(o["id"], sym)
        raise RuntimeError(f"ordem {o.get('id')} sem estado final após {self.poll_attempts} consultas")

    def _fee(self, sym: str, o: dict, filled: float, price: float) -> tuple[float, bool]:
        """Taxa real das execuções da ordem; outra moeda (BNB) ou falha na consulta → estima com FEE_RATE."""
        fee, other = 0.0, False
        try:
            fills = self.ex.fetch_my_trades(sym, params={"orderId": o["id"]})
        except ccxt.BaseError as e:
            log.warning("taxa da ordem %s não consultada: %s", o.get("id"), e)
            fills = []
        for t in fills:
            f = t.get("fee") or {}
            cost = float(f.get("cost") or 0)
            if f.get("currency") == "USDT":
                fee += cost
            elif cost > 0:
                other = True
        if fee <= 0 or other:
            return filled * price * self.fee_rate, True
        return fee, False

    def _avg_price(self, sym: str, o: dict, filled: float) -> float:
        """A resposta do create_order na demo vem com avgPrice 0; a consulta da ordem traz o preço real.
        Nunca devolve 0: sem preço depois das consultas é erro (o RECONCILE acusa a posição órfã)."""
        for _ in range(self.poll_attempts):
            price = float(o.get("average") or 0) or float(o.get("cost") or 0) / filled
            if price > 0:
                return price
            self._sleep(1)
            o = self.ex.fetch_order(o["id"], sym)
        raise RuntimeError(f"ordem {o.get('id')} executada mas a demo não informou o preço médio")

    def _to_result(self, order: OrderRequest, sym: str, o: dict) -> OrderResult:
        filled = float(o.get("filled") or 0)
        if o.get("status") != "closed" or filled <= 0:
            return self._reject(order, f"status {o.get('status')} filled={filled}")
        price = self._avg_price(sym, o, filled)
        fee, estimated = self._fee(sym, o, filled, price)
        return OrderResult(order.client_order_id, order.symbol, order.side, filled, price, fee, "filled",
                           simulated=True, note=f"demo futuros id={o.get('id')}" + (" taxa estimada" if estimated else ""))

    @staticmethod
    def _reject(order: OrderRequest, note: str) -> OrderResult:
        return OrderResult(order.client_order_id, order.symbol, order.side, 0.0, 0.0, 0.0, "rejected", True, note)
