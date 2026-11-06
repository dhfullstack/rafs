"""FUTURES (demo da Binance, 1x): short no motor, nos deciders, no adaptador e na configuração."""

import json
from dataclasses import replace

import ccxt
import httpx
import pytest

from rafs.config import ConfigError, Settings
from rafs.dashboard.state import _book_state
from rafs.deciders.baseline import RandomBaseline, TrendBaseline
from rafs.deciders.grok_screen import SYSTEM, SYSTEM_FUTURES, GrokScreener, Ledger
from rafs.deciders.jev import JevDecider
from rafs.engine import FEATURE_WINDOW, FillRefs
from rafs.exchanges.base import OrderRequest
from rafs.exchanges.ccxt_data import CCXTMarketData
from rafs.exchanges.ccxt_futures import CCXTFuturesExchange, assert_demo, make_futures_exchange, perp
from rafs.exchanges.ccxt_testnet import NotSandboxError, to_client_id
from rafs.exchanges.paper import PaperExchange
from rafs.models import Action, Position
from rafs.portfolio import Book
from rafs.quant import compute_features
from rafs.storage import Storage
from tests.conftest import T0, TF, make_candles, trend
from tests.test_engine import Scripted, make_engine
from tests.test_grok import grok_body, grok_client
from tests.test_jev import client_with, v2_body

SYM = "SOL/BRL"


def down():
    return trend(FEATURE_WINDOW, step=-0.003)


def open_short(settings, rules, storage=None):
    fut = replace(settings, mode="FUTURES")
    closes = down()
    e = make_engine(fut, rules, storage or Storage(":memory:"), [Scripted(Action.SELL, Action.BUY)])
    e.process_candle(SYM, make_candles(closes), FillRefs(closes[-1] * 1.001, closes[-1]))
    return e, closes


# ---------------------------------------------------------------------- motor
def test_short_open_and_buy_back_books_cash_and_pnl(settings, rules):
    e, closes = open_short(settings, rules)
    book = e.books["scripted"]
    pos = book.positions[SYM]
    assert pos.is_short and pos.take_profit_price < pos.entry_price < pos.stop_price
    assert book.cash == pytest.approx(settings.capital_brl - pos.qty * pos.entry_price - pos.entry_fee)

    px = (pos.entry_price + pos.take_profit_price) / 2  # caiu, sem tocar o alvo
    candles = make_candles(closes + [px], spread=0.0)
    e.process_candle(SYM, candles, FillRefs(px, px * 0.999))
    assert not book.positions
    t = book.trades[0]
    assert t.side == "short" and t.exit_reason.startswith("decider") and t.pnl > 0
    assert t.pnl == pytest.approx(t.qty * (t.entry_price - t.exit_price) - t.fees)
    assert book.cash == pytest.approx(settings.capital_brl + t.pnl)


def test_spot_mode_never_opens_a_short(settings, rules):
    closes = down()
    e = make_engine(settings, rules, Storage(":memory:"), [Scripted(Action.SELL, Action.BUY)])
    e.process_candle(SYM, make_candles(closes), FillRefs(closes[-1], closes[-1]))
    assert not e.books["scripted"].positions and e.books["scripted"].cash == settings.capital_brl


def test_short_stop_is_above_and_hit_by_the_high(settings, rules):
    e, closes = open_short(settings, rules)
    pos = e.books["scripted"].positions[SYM]
    spike = make_candles(closes + [pos.stop_price * 1.05], spread=0.0)
    e.process_candle(SYM, spike, FillRefs(spike[-1].close, spike[-1].close))
    t = e.books["scripted"].trades[0]
    assert t.exit_reason == "stop" and t.pnl < 0 and t.exit_price >= pos.stop_price


def test_short_target_is_below_and_hit_by_the_low(settings, rules):
    e, closes = open_short(settings, rules)
    pos = e.books["scripted"].positions[SYM]
    dump = make_candles(closes + [pos.take_profit_price * 0.95], spread=0.0)
    e.process_candle(SYM, dump, FillRefs(dump[-1].close, dump[-1].close))
    t = e.books["scripted"].trades[0]
    assert t.exit_reason == "alvo" and t.pnl > 0


def test_short_survives_restart(settings, rules, tmp_path):
    db = tmp_path / "f.sqlite3"
    open_short(settings, rules, Storage(db))
    e2 = make_engine(replace(settings, mode="FUTURES"), rules, Storage(db), [Scripted()])
    assert e2.books["scripted"].positions[SYM].side == "short"


def test_short_value_equity_and_dashboard():
    pos = Position("b", "BTC/USDT", 2.0, 100.0, 0.1, T0, 110.0, 90.0, side="short")
    assert pos.value(90.0) == pytest.approx(220.0) and pos.unrealized(90.0) == pytest.approx(20.0)
    assert pos.pnl_pct(90.0) == pytest.approx(10.0) and pos.pnl_pct(105.0) == pytest.approx(-5.0)
    book = Book("b", 800.0, 1000.0, {"BTC/USDT": pos})
    assert book.equity({"BTC/USDT": 90.0}) == pytest.approx(1020.0)
    assert book.unrealized({"BTC/USDT": 105.0}) == pytest.approx(-10.0)


def test_dashboard_shows_short_side_value_and_pnl(settings, rules):
    e, _ = open_short(settings, rules)
    pos = e.books["scripted"].positions[SYM]
    px = pos.entry_price * 0.98
    st = _book_state(e.storage, "scripted", settings.capital_brl, {SYM: px}, T0)
    p = st["positions"][0]
    assert p["side"] == "short" and p["pnl_pct"] == pytest.approx(2.0)  # caiu 2%: short ganha 2%
    assert p["value"] == pytest.approx(pos.qty * (2 * pos.entry_price - px))
    assert p["pnl"] == pytest.approx(pos.qty * (pos.entry_price - px) - pos.entry_fee)


def test_paper_close_has_no_min_cost_but_open_does(rules):
    ex = PaperExchange(rules, 0.001, 0)
    assert ex.place_order(OrderRequest("a", SYM, "buy", 0.05, 100.0, reduce=True)).status == "filled"
    assert ex.place_order(OrderRequest("b", SYM, "sell", 0.05, 100.0)).status == "rejected"


# ---------------------------------------------------------------------- deciders
def feats(**kw):
    return replace(compute_features(SYM, make_candles(trend(120))), **kw)


BEAR = dict(ema_fast=99.0, ema_slow=100.0, rsi=42.0, momentum_pct=-0.8, vwap_dev_pct=-0.4)


def test_baseline_shorts_a_clean_downtrend_only_when_allowed():
    f = feats(**BEAR)
    assert TrendBaseline(allow_short=True).decide(f, None).action is Action.SELL
    assert TrendBaseline().decide(f, None).action is Action.HOLD
    assert TrendBaseline(allow_short=True).decide(feats(**{**BEAR, "rsi": 25.0}), None).action is Action.HOLD
    short = Position("baseline", SYM, 1, 100, 0, T0, 103, 94, side="short")
    assert TrendBaseline(allow_short=True).decide(f, short).action is Action.HOLD
    assert TrendBaseline(allow_short=True).decide(feats(ema_fast=101.0, ema_slow=100.0), short).action is Action.BUY


def test_random_uses_the_other_tail_for_shorts():
    f = feats()
    assert RandomBaseline(p_buy=0.0, p_sell=1.0, allow_short=True).decide(f, None).action is Action.SELL
    assert RandomBaseline(p_buy=0.0, p_sell=1.0).decide(f, None).action is Action.HOLD
    short = Position("random", SYM, 1, 100, 0, T0, 103, 94, side="short")
    assert RandomBaseline(p_sell=1.0, allow_short=True).decide(f, short).action is Action.BUY


def jev(handler, **kw):
    return JevDecider(client_with(handler), "15m", 2.0, 0.6, 0, lambda day: 0, take_profit_atr_mult=3.0,
                      cost_pct=0.3, edge_margin=0.03, max_hold_candles=32, allow_short=True, **kw)


def capture(seen, body):
    def handler(r):
        seen.append((json.loads(r.content), r.headers["Idempotency-Key"]))
        return httpx.Response(200, json=body)
    return handler


def test_jev_v2_short_entry_in_a_downtrend():
    seen = []
    d = jev(capture(seen, v2_body(0.9)), prompt_version="v2").decide(feats(**BEAR), None)
    body, key = seen[0]
    q = body["decision"]["question"]
    assert body["decision"]["choices"] == ["TARGET", "STOP"] and key.endswith("-es-v2")
    assert "open a short" in q and "perpetual futures" in q and "Spot" not in q
    assert d.action is Action.SELL and "short" in d.reason


def test_jev_short_exit_asks_buy_back_and_spot_gate_still_skips():
    seen = []
    body = {"decision": "BUY", "confidence": 0.5, "results": [{"value": "BUY", "probability": 0.55},
                                                              {"value": "HOLD", "probability": 0.45}]}
    short = Position("jev", SYM, 1, 100, 0, T0, 103, 94, side="short")
    d = jev(capture(seen, body)).decide(feats(**BEAR), short)
    assert seen[0][0]["decision"]["choices"] == ["BUY", "HOLD"] and seen[0][1].endswith("-xs")
    assert d.action is Action.BUY  # saída não exige p mínima

    calls = []
    spot = JevDecider(client_with(lambda r: calls.append(1)), "15m", 2.0, 0.6, 0, lambda day: 0)
    assert spot.decide(feats(**BEAR), None).action is Action.HOLD and not calls


def test_grok_reads_shorts_with_futures_system():
    assert SYSTEM_FUTURES != SYSTEM and "long and short" in SYSTEM_FUTURES
    sent = []

    def handler(r):
        sent.append(json.loads(r.content)["messages"])
        return httpx.Response(200, json=grok_body(escalate=False))

    storage = Storage(":memory:")
    g = GrokScreener(grok_client(handler), "15m", TF, Ledger(storage.get, storage.set), lambda sym: [],
                     clock=lambda: 1000.0, allow_short=True)
    g.review(feats(**BEAR), None, short=True)
    short = Position("jev", SYM, 1, 100, 0, T0, 103, 94, side="short")
    g.review(feats(**BEAR), short)
    assert sent[0][0]["content"] == SYSTEM_FUTURES
    assert "ENTRY CANDIDATE, SHORT" in sent[0][1]["content"]
    assert "OPEN SHORT" in sent[1][1]["content"] and "side=short" in sent[1][1]["content"]


# ---------------------------------------------------------------------- adaptador
DEMO = "https://demo-fapi.binance.com/fapi/v1"


class FakeUSDM:
    def __init__(self, free=1000.0, fee=None, positions=()):
        self.urls = {"api": {"fapiPublic": DEMO, "fapiPrivate": DEMO}}
        self.free, self.fee, self.positions = free, fee, list(positions)
        self.orders, self.created, self.calls = {}, [], []

    def load_markets(self):
        m = {"active": True, "limits": {"cost": {"min": 5.0}}, "precision": {"amount": 0.001}}
        return {"BTC/USDT:USDT": m, "SOL/USDT:USDT": m}

    def fetch_balance(self):
        return {"free": {"USDT": self.free}}

    def fetch_order(self, oid, sym, params=None):
        cid = (params or {}).get("origClientOrderId")
        o = self.orders.get(cid) if cid else next(o for o in self.orders.values() if o["id"] == oid)
        if o is None:
            raise ccxt.OrderNotFound("no")
        return o

    def create_order(self, sym, kind, side, amount, price, params):
        self.created.append((sym, side, amount))
        o = {"id": str(len(self.created)), "status": "closed", "filled": amount, "average": 100.0}
        self.orders[params["clientOrderId"]] = o
        return o

    def fetch_my_trades(self, sym, params=None):
        return [{"fee": self.fee}] if self.fee else []

    def fetch_positions(self):
        return self.positions

    def set_position_mode(self, hedged):
        self.calls.append(("mode", hedged))
        raise ccxt.ExchangeError("No need to change position side.")

    def set_margin_mode(self, mode, sym):
        self.calls.append(("margin", mode, sym))

    def set_leverage(self, lev, sym):
        self.calls.append(("lev", lev, sym))


def test_assert_demo_rejects_any_real_host():
    assert_demo(FakeUSDM())
    bad = FakeUSDM()
    bad.urls["api"]["fapiPrivate"] = "https://fapi.binance.com/fapi/v1"
    with pytest.raises(NotSandboxError):
        assert_demo(bad)
    with pytest.raises(NotSandboxError):
        CCXTFuturesExchange(bad, 0.0005)


def test_real_ccxt_futures_client_points_only_to_demo():
    ex = make_futures_exchange()
    assert all(str(u).startswith("https://demo-") for u in ex.urls["api"].values())
    md = CCXTMarketData("binance", demo_futures=True)
    assert md._sym("BTC/USDT") == "BTC/USDT:USDT" and perp("ETH/USDT:USDT") == "ETH/USDT:USDT"


def test_order_is_idempotent_and_uses_real_usdt_fee():
    fake = FakeUSDM(fee={"currency": "USDT", "cost": 0.07})
    ex = CCXTFuturesExchange(fake, 0.0005, sleep=lambda s: None)
    order = OrderRequest("jev-SOLUSDT-1-short", "SOL/USDT", "sell", 1.2345, 100.0)
    r1 = ex.place_order(order)
    r2 = ex.place_order(order)  # restart: acha pelo clientOrderId, não manda de novo
    assert fake.created == [("SOL/USDT:USDT", "sell", 1.234)] and to_client_id(order.client_order_id) in fake.orders
    assert r1.status == r2.status == "filled" and r1.symbol == "SOL/USDT" and r1.fee == pytest.approx(0.07)
    assert r1.simulated and "demo futuros" in r1.note and "estimada" not in r1.note


def test_zero_average_in_create_response_is_looked_up():
    fake = FakeUSDM()
    real = fake.create_order

    def zero_avg(*a):
        o = real(*a)
        stored = dict(o)
        o.update(average=0.0, price=0.0, cost=0.0)  # o que a demo devolve com newOrderRespType=RESULT
        fake.orders = {k: (stored if v is o else v) for k, v in fake.orders.items()}
        return o
    fake.create_order = zero_avg
    r = CCXTFuturesExchange(fake, 0.0005, sleep=lambda s: None).place_order(
        OrderRequest("z", "SOL/USDT", "sell", 1.0, 100.0))
    assert r.status == "filled" and r.price == 100.0


def test_fill_without_price_is_an_error_not_a_zero():
    fake = FakeUSDM()
    real = fake.create_order

    def never(*a):
        o = real(*a)
        o.update(average=None, cost=0.0)
        return o
    fake.create_order = never
    with pytest.raises(RuntimeError, match="preço médio"):
        CCXTFuturesExchange(fake, 0.0005, poll_attempts=2, sleep=lambda s: None).place_order(
            OrderRequest("n", "SOL/USDT", "sell", 1.0, 100.0))


def test_fee_in_other_currency_is_estimated():
    ex = CCXTFuturesExchange(FakeUSDM(fee={"currency": "BNB", "cost": 0.0001}), 0.0005, sleep=lambda s: None)
    r = ex.place_order(OrderRequest("x", "SOL/USDT", "buy", 1.0, 100.0))
    assert r.fee == pytest.approx(100.0 * 0.0005) and "taxa estimada" in r.note


def test_margin_precheck_blocks_opens_not_closes():
    ex = CCXTFuturesExchange(FakeUSDM(free=50.0), 0.0005, sleep=lambda s: None)
    assert ex.place_order(OrderRequest("o", "SOL/USDT", "sell", 1.0, 100.0)).status == "rejected"
    assert ex.place_order(OrderRequest("c", "SOL/USDT", "buy", 1.0, 100.0, reduce=True)).status == "filled"


def test_reconcile_compares_net_long_minus_short():
    pos = [{"symbol": "SOL/USDT:USDT", "contracts": 0.5, "side": "short"}]
    ex = CCXTFuturesExchange(FakeUSDM(positions=pos), 0.0005)
    a = Book("baseline", 900, 1000, {"SOL/USDT": Position("baseline", "SOL/USDT", 1.0, 100, 0, T0, 95, 106)})
    b = Book("jev", 850, 1000, {"SOL/USDT": Position("jev", "SOL/USDT", 1.5, 100, 0, T0, 103, 94, side="short")})
    assert ex.reconcile([a, b]) == []
    assert ex.reconcile([a]) == ["SOL/USDT: banco espera líquido +1, demo tem -0.5"]


def test_prepare_sets_isolated_1x_and_fails_loudly_on_leverage():
    fake = FakeUSDM()
    assert CCXTFuturesExchange(fake, 0.0005).prepare(["BTC/USDT"]) == []
    assert ("margin", "isolated", "BTC/USDT:USDT") in fake.calls and ("lev", 1, "BTC/USDT:USDT") in fake.calls

    def boom(lev, sym):
        raise ccxt.ExchangeError("leverage not allowed")
    fake.set_leverage = boom
    with pytest.raises(RuntimeError, match="1x"):
        CCXTFuturesExchange(fake, 0.0005).prepare(["BTC/USDT"])


# ---------------------------------------------------------------------- configuração
def test_futures_settings_have_own_bank_fee_db_and_need_demo_keys(tmp_path):
    keys = {"MODE": "FUTURES", "FUTURES_API_KEY": "k", "FUTURES_API_SECRET": "s"}
    s = Settings.from_env({**keys, "DATA_DIR": str(tmp_path)})
    assert (s.capital_brl, s.entry_brl, s.max_exposure_brl, s.daily_loss_limit_brl) == (1000, 100, 300, 50)
    assert s.fee_rate == 0.0005 and s.allow_short and s.symbols == ("BTC/USDT", "ETH/USDT", "SOL/USDT")
    assert s.db_path.name == "rafs-futures.sqlite3"
    with pytest.raises(ConfigError, match="DEMO"):
        Settings.from_env({"MODE": "FUTURES"})
    Settings.from_env({**keys, "FUTURES_CAPITAL": "2000", "FUTURES_ENTRY": "150"}).validate()
    assert Settings.from_env({**keys, "FUTURES_CAPITAL": "2000"}).capital_brl == 2000
    with pytest.raises(ConfigError, match="USDT"):
        Settings.from_env({**keys, "FUTURES_SYMBOLS": "BTC/BRL"}).validate()


def test_spot_modes_ignore_futures_sizing():
    s = Settings.from_env({"MODE": "PAPER", "FUTURES_CAPITAL": "5000"})
    assert s.capital_brl == 200 and not s.allow_short and s.fee_rate == 0.001


# ---------------------------------------------------------------------- fade (volta à VWAP48)
def test_jev_fade_skips_near_vwap_and_takes_the_stretched_side():
    calls = []
    near = jev(lambda r: calls.append(1), entry_style="fade").decide(feats(vwap_dev_pct=0.6), None)
    assert near.action is Action.HOLD and near.reason.startswith("filtro:") and not calls

    seen = []
    d = jev(capture(seen, v2_body(0.9)), prompt_version="v2", entry_style="fade").decide(
        feats(vwap_dev_pct=-1.4, ema_fast=99.0, ema_slow=100.0), None)  # queda esticada: compra mesmo com EMA de baixa
    body, key = seen[0]
    q = body["decision"]["question"]
    assert "If we buy now" in q and "1.40% below its 48-candle VWAP" in q and len(q) <= 300
    assert key.endswith("-e-v2-f") and d.action is Action.BUY
    assert "trend24h_pct=" in body["state"]["message"] and "range24h_pos=" in body["state"]["message"]

    seen.clear()
    d = jev(capture(seen, v2_body(0.9)), prompt_version="v2", entry_style="fade").decide(
        feats(vwap_dev_pct=1.6), None)  # alta esticada: short mesmo com EMA de alta
    q = seen[0][0]["decision"]["question"]
    assert "open a short" in q and "above its 48-candle VWAP" in q and d.action is Action.SELL


def test_jev_fade_in_spot_never_shorts_and_exit_question_keeps_the_thesis():
    calls = []
    spot = JevDecider(client_with(lambda r: calls.append(1)), "15m", 2.0, 0.6, 0, lambda day: 0, entry_style="fade")
    assert spot.decide(feats(vwap_dev_pct=2.0), None).action is Action.HOLD and not calls

    seen = []
    body = {"decision": "HOLD", "confidence": 0.5, "results": [{"value": "BUY", "probability": 0.4},
                                                               {"value": "HOLD", "probability": 0.6}]}
    short = Position("jev", SYM, 1, 100, 0, T0, 103, 94, side="short")
    jev(capture(seen, body), entry_style="fade").decide(feats(vwap_dev_pct=0.1), short)
    q = seen[0][0]["decision"]["question"]
    assert "reversion failed" in q and len(q) <= 300 and seen[0][1].endswith("-xs-f")


def test_grok_fade_prompt_names_the_stretch_breakeven_and_24h_context():
    from rafs.deciders.grok_screen import EXIT_FADE_SHORT_TASK
    sent = []

    def handler(r):
        sent.append(json.loads(r.content)["messages"])
        return httpx.Response(200, json=grok_body(escalate=False))

    storage = Storage(":memory:")
    g = GrokScreener(grok_client(handler), "15m", TF, Ledger(storage.get, storage.set), lambda sym: [],
                     clock=lambda: 1000.0, allow_short=True, min_stop_pct=0.8, entry_style="fade")
    g.review(feats(vwap_dev_pct=-1.25), None)
    g.review(feats(vwap_dev_pct=1.5), None, short=True)
    g.review(feats(), Position("jev", SYM, 1, 100, 0, T0, 103, 94, side="short"))
    long_, short_, exit_ = (m[1]["content"] for m in sent)
    assert "LONG FADE" in long_ and "1.25% below the 48-candle VWAP" in long_ and "breakeven win rate" in long_
    assert "{" not in long_.split("DATA")[0]  # tudo formatado
    assert "SHORT FADE" in short_ and "1.50% above" in short_ and "-" in short_ and "trend24h_pct=" in short_
    assert exit_.startswith(EXIT_FADE_SHORT_TASK)


def test_entry_style_config_is_validated():
    assert Settings.from_env({"JEV_ENTRY_STYLE": "fade"}).jev_entry_style == "fade"
    with pytest.raises(ConfigError):
        Settings.from_env({"JEV_ENTRY_STYLE": "yolo"})
    with pytest.raises(ConfigError):
        Settings.from_env({"FADE_MIN_DEV_PCT": "0"})


def test_24h_context_features_are_bounded():
    f = compute_features(SYM, make_candles(trend(120)))
    assert 0.0 <= f.range24h_pos <= 1.0 and f.trend24h_pct > 0 and f.mom4h_pct > 0
