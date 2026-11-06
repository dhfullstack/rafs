import ccxt
import pytest

from rafs.config import ConfigError, Settings
from rafs.engine import FEATURE_WINDOW, Engine, FillRefs
from rafs.exchanges.base import OrderRequest
from rafs.exchanges.ccxt_testnet import CCXTTestnetExchange, NotSandboxError, to_client_id
from rafs.risk import RiskEngine
from rafs.storage import Storage
from tests.conftest import make_candles, trend
from tests.test_engine import Scripted

TESTNET_URL = "https://testnet.binance.vision/api/v3"


class FakeCCXT:
    """Imita o pedaço do ccxt.binance usado pelo adaptador (sandbox)."""

    id = "binance"

    def __init__(self, private_url=TESTNET_URL, balance=None, fee=None, fail_create=None, status_seq=None):
        self.urls = {"api": {"public": TESTNET_URL, "private": private_url}}
        self.free = dict(balance or {"BRL": 1000.0, "SOL": 0.0})
        self.fee = fee
        self.fail_create = fail_create
        self.status_seq = list(status_seq or [])
        self.orders: dict[str, dict] = {}  # clientOrderId -> ordem
        self.create_calls = 0

    def load_markets(self):
        return {"SOL/BRL": {"active": True, "limits": {"cost": {"min": 10.0}}, "precision": {"amount": 0.001}}}

    def fetch_balance(self):
        return {"free": dict(self.free)}

    def fetch_order(self, oid, symbol, params=None):
        if oid is None:
            cid = (params or {}).get("origClientOrderId")
            if cid not in self.orders:
                raise ccxt.OrderNotFound("not found")
            return self.orders[cid]
        o = next(o for o in self.orders.values() if o["id"] == oid)
        if self.status_seq:
            o["status"] = self.status_seq.pop(0)
        return o

    def create_order(self, symbol, type_, side, amount, price, params):
        self.create_calls += 1
        cid = params["clientOrderId"]
        px = 100.0
        o = {"id": f"x{len(self.orders) + 1}", "clientOrderId": cid, "status": "closed" if not self.status_seq else "open",
             "filled": amount, "average": px, "cost": amount * px,
             "fees": [self.fee] if self.fee else [{"cost": 0.0, "currency": "BRL"}]}
        base, quote = symbol.split("/")
        if side == "buy":
            if self.free.get(quote, 0) < amount * px:
                raise ccxt.InsufficientFunds("insufficient")
            self.free[quote] -= amount * px
            self.free[base] = self.free.get(base, 0) + amount
        else:
            self.free[base] -= amount
            self.free[quote] = self.free.get(quote, 0) + amount * px
        self.orders[cid] = o
        if self.fail_create:
            err, self.fail_create = self.fail_create, None
            raise err
        return o


def ex_for(fake, **kw):
    return CCXTTestnetExchange(fake, fee_rate=0.001, sleep=lambda s: None, **kw)


def buy(qty=0.15, oid="jev-SOLBRL-1-buy"):
    return OrderRequest(oid, "SOL/BRL", "buy", qty, 100.0)


def test_refuses_mainnet_url():
    with pytest.raises(NotSandboxError):
        ex_for(FakeCCXT(private_url="https://api.binance.com/api/v3"))


def test_live_mode_still_refused(tmp_path):
    with pytest.raises(ConfigError):
        Settings(mode="LIVE", data_dir=tmp_path).validate()
    with pytest.raises(ConfigError):
        Settings(mode="TESTNET", data_dir=tmp_path).validate()  # sem chaves
    s = Settings(mode="TESTNET", testnet_api_key="k", testnet_api_secret="s", data_dir=tmp_path)
    s.validate()
    assert s.db_path.name == "rafs-testnet.sqlite3"
    assert "testnet_api_key" not in repr(s) and "testnet_api_secret" not in repr(s)


def test_client_id_respects_binance_limit():
    long = "baseline-SOLBRL-1790000000000-sell-extra-long-suffix"
    cid = to_client_id(long)
    assert len(cid) <= 36 and cid == to_client_id(long)
    assert to_client_id("short-id") == "short-id"


def test_buy_fills_and_estimates_fee_when_testnet_charges_zero():
    fake = FakeCCXT()
    r = ex_for(fake).place_order(buy())
    assert r.status == "filled" and r.simulated
    assert r.amount == pytest.approx(0.15) and r.price == 100.0
    assert r.fee == pytest.approx(0.15 * 100 * 0.001)  # taxa real da Binance aplicada mesmo com testnet grátis
    assert "taxa estimada" in r.note and "testnet id=" in r.note


def test_fee_in_base_asset_converted_to_quote():
    fake = FakeCCXT(fee={"cost": 0.00015, "currency": "SOL"})
    r = ex_for(fake).place_order(buy())
    assert r.fee == pytest.approx(0.00015 * 100)
    assert "taxa estimada" not in r.note


def test_same_order_id_is_not_sent_twice():
    fake = FakeCCXT()
    ex = ex_for(fake)
    r1 = ex.place_order(buy())
    r2 = ex_for(fake).place_order(buy())  # restart: novo adaptador, mesma ordem
    assert fake.create_calls == 1
    assert r1.amount == r2.amount and r1.price == r2.price


def test_network_error_after_send_recovers_existing_order():
    fake = FakeCCXT(fail_create=ccxt.RequestTimeout("timeout"))
    r = ex_for(fake).place_order(buy())
    assert r.status == "filled" and fake.create_calls == 1


def test_insufficient_balance_is_rejected_not_raised():
    fake = FakeCCXT(balance={"BRL": 5.0})
    r = ex_for(fake).place_order(buy())
    assert r.status == "rejected" and "insuficiente" in r.note
    assert fake.create_calls == 0


def test_sell_capped_at_free_balance():
    fake = FakeCCXT(balance={"BRL": 0.0, "SOL": 0.1})
    r = ex_for(fake).place_order(OrderRequest("jev-SOLBRL-2-sell", "SOL/BRL", "sell", 0.15, 100.0))
    assert r.status == "filled" and r.amount == pytest.approx(0.1)


def test_waits_for_final_status():
    fake = FakeCCXT(status_seq=["open", "open", "closed"])
    r = ex_for(fake).place_order(buy())
    assert r.status == "filled"


def test_reconcile_flags_missing_base_asset(settings, rules, tmp_path):
    fake = FakeCCXT()
    ex = ex_for(fake)
    storage = Storage(tmp_path / "t.sqlite3")
    e = Engine(settings, storage, [Scripted()], ex, RiskEngine(settings, kill_switch_check=lambda: False))
    candles = make_candles(trend(FEATURE_WINDOW))
    e.process_candle("SOL/BRL", candles, FillRefs(100.0, 100.0))
    assert "SOL/BRL" in e.books["scripted"].positions
    assert ex.reconcile(e.books.values()) == []
    fake.free["SOL"] = 0.0  # testnet resetada
    issues = ex.reconcile(e.books.values())
    assert len(issues) == 1 and issues[0].startswith("SOL")
