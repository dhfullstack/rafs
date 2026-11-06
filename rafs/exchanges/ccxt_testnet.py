"""FILLS em TESTNET — ordens reais na testnet da corretora, com dinheiro fictício.

Trava de segurança: o adaptador se recusa a operar se a URL privada não for de testnet/sandbox.
Não existe caminho neste arquivo para operar na conta real.
"""

from __future__ import annotations

import hashlib
import logging
import time

import ccxt

from .base import ExchangeCapabilities, ExchangeProvider, OrderRequest, OrderResult, TradingRules

log = logging.getLogger("rafs.testnet")
CLIENT_ID_MAX = 36  # limite da Binance para newClientOrderId


class NotSandboxError(RuntimeError):
    pass


def make_testnet_exchange(exchange_id: str, api_key: str, secret: str):
    if not api_key or not secret:
        raise ValueError("TESTNET_API_KEY e TESTNET_API_SECRET são obrigatórios no modo TESTNET")
    # relógio do PC adiantado faz a Binance recusar requisições assinadas (-1021): sincroniza com o servidor
    ex = getattr(ccxt, exchange_id)({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 15000,
                                     "options": {"adjustForTimeDifference": True, "recvWindow": 10000}})
    ex.set_sandbox_mode(True)
    return ex


def assert_sandbox(ex) -> None:
    api = ex.urls.get("api")
    private = api.get("private") if isinstance(api, dict) else api
    if not private or not any(w in str(private) for w in ("testnet", "sandbox", "demo")):
        raise NotSandboxError(f"URL privada não é de testnet: {private!r}. Abortando por segurança.")


def to_client_id(order_id: str) -> str:
    if len(order_id) <= CLIENT_ID_MAX:
        return order_id
    return "gb-" + hashlib.sha256(order_id.encode()).hexdigest()[:CLIENT_ID_MAX - 3]


class CCXTTestnetExchange(ExchangeProvider):
    def __init__(self, exchange, fee_rate: float, poll_attempts: int = 10, sleep=time.sleep):
        assert_sandbox(exchange)
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
        return ExchangeCapabilities(name=f"{self.ex.id}-testnet", spot=True, order_book=True, cancel_orders=True,
                                    live_execution=False)

    def get_trading_rules(self, symbol: str) -> TradingRules:
        m = self._load().get(symbol)
        if not m or not m.get("active", True):
            raise ValueError(f"mercado indisponível na testnet: {symbol}")
        return TradingRules(symbol, float((m["limits"].get("cost") or {}).get("min") or 0),
                            float(m["precision"].get("amount") or 0))

    def balances(self) -> dict[str, float]:
        bal = self.ex.fetch_balance()
        return {k: float(v or 0) for k, v in (bal.get("free") or {}).items()}

    def reconcile(self, books) -> list[str]:
        """Confere se a conta da testnet tem o ativo de todas as posições abertas registradas no banco."""
        need: dict[str, float] = {}
        for book in books:
            for p in book.positions.values():
                base = p.symbol.split("/")[0]
                need[base] = need.get(base, 0.0) + p.qty
        if not need:
            return []
        free = self.balances()
        return [f"{base}: banco espera {qty:g}, testnet tem {free.get(base, 0.0):g} (testnet foi resetada?)"
                for base, qty in need.items() if free.get(base, 0.0) < qty * 0.999]

    # ------------------------------------------------------------------ ordens
    def place_order(self, order: OrderRequest) -> OrderResult:
        cid = to_client_id(order.client_order_id)
        existing = self._find(order.symbol, cid)  # idempotência: já foi enviada antes de uma queda?
        if existing is None:
            rejected = self._precheck(order)
            if rejected:
                return rejected
            rules = self.get_trading_rules(order.symbol)
            amount = rules.floor_amount(self._sellable(order) if order.side == "sell" else order.amount)
            try:
                existing = self.ex.create_order(order.symbol, "market", order.side, amount, None,
                                                {"clientOrderId": cid})
            except (ccxt.NetworkError, ccxt.RequestTimeout):
                existing = self._find(order.symbol, cid)  # resultado incerto: consulta antes de qualquer retry
                if existing is None:
                    raise
            except ccxt.InsufficientFunds as e:
                return self._reject(order, f"saldo insuficiente na testnet: {e}")
            except ccxt.InvalidOrder as e:
                return self._reject(order, f"ordem inválida: {e}")
        final = self._wait_final(order.symbol, existing)
        return self._to_result(order, final)

    def _precheck(self, order: OrderRequest) -> OrderResult | None:
        if order.side == "buy":
            quote = order.symbol.split("/")[1]
            free = self.balances().get(quote, 0.0)
            if free < order.amount * order.ref_price:
                return self._reject(order, f"saldo {quote} insuficiente na testnet ({free:.2f})")
        return None

    def _sellable(self, order: OrderRequest) -> float:
        base = order.symbol.split("/")[0]
        free = self.balances().get(base, 0.0)
        return min(order.amount, free)

    def _find(self, symbol: str, cid: str):
        try:
            return self.ex.fetch_order(None, symbol, {"origClientOrderId": cid})
        except ccxt.OrderNotFound:
            return None

    def _wait_final(self, symbol: str, o: dict) -> dict:
        for _ in range(self.poll_attempts):
            if o.get("status") in ("closed", "canceled", "rejected", "expired"):
                return o
            self._sleep(1)
            o = self.ex.fetch_order(o["id"], symbol)
        raise RuntimeError(f"ordem {o.get('id')} sem estado final após {self.poll_attempts} consultas")

    def _to_result(self, order: OrderRequest, o: dict) -> OrderResult:
        filled = float(o.get("filled") or 0)
        if o.get("status") != "closed" or filled <= 0:
            return self._reject(order, f"status {o.get('status')} filled={filled}")
        price = float(o.get("average") or (float(o.get("cost") or 0) / filled))
        base, quote = order.symbol.split("/")
        fee, estimated = 0.0, False
        for f in o.get("fees") or ([o["fee"]] if o.get("fee") else []):
            cost = float(f.get("cost") or 0)
            cur = f.get("currency")
            if cur == quote:
                fee += cost
            elif cur == base:
                fee += cost * price
            elif cost > 0:
                fee += filled * price * self.fee_rate  # taxa em outro ativo (ex.: BNB): estima
                estimated = True
        if fee == 0:
            # a testnet costuma não cobrar taxa; registramos a taxa real da corretora para não inflar o resultado
            fee, estimated = filled * price * self.fee_rate, True
        return OrderResult(order.client_order_id, order.symbol, order.side, filled, price, fee, "filled",
                           simulated=True, note=f"testnet id={o.get('id')}" + (" taxa estimada" if estimated else ""))

    @staticmethod
    def _reject(order: OrderRequest, note: str) -> OrderResult:
        return OrderResult(order.client_order_id, order.symbol, order.side, 0.0, 0.0, 0.0, "rejected", True, note)
