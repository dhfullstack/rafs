"""Candle curto (2m montado de 1m), piso de stop/alvo e o corte por score do Grok."""

import json
from dataclasses import replace

import httpx
import pytest

from rafs.config import ConfigError, Settings
from rafs.deciders.grok_screen import GrokScreener, Ledger
from rafs.engine import FEATURE_WINDOW, FillRefs
from rafs.exchanges.ccxt_data import CCXTMarketData, aggregate_candles, native_timeframe
from rafs.exits import exit_pcts, exit_scale
from rafs.models import Candle, Position
from rafs.storage import Storage
from tests.conftest import make_candles, trend
from tests.test_engine import Scripted, make_engine
from tests.test_grok import features, grok_body, grok_client
from tests.test_jev import v2_decider

M1 = 60_000
BINANCE_TFS = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1M": "1M"}


# ------------------------------------------------------------------ piso de stop/alvo
def test_exit_pcts_without_floor_is_plain_atr():
    assert exit_pcts(0.1, 2.0, 3.0) == pytest.approx((0.2, 0.3))
    assert exit_scale(0.1, 2.0, 0.0) == 1.0


def test_floor_scales_stop_and_target_together():
    sl, tp = exit_pcts(0.1, 2.0, 3.0, min_stop_pct=0.6)
    assert sl == pytest.approx(0.6) and tp == pytest.approx(0.9)  # alvo/stop continua 1,5
    assert exit_pcts(0.5, 2.0, 3.0, min_stop_pct=0.6) == pytest.approx((1.0, 1.5))  # ATR já acima do piso: nada muda


def test_engine_applies_floor_to_new_positions(settings, rules):
    candles = make_candles(trend(FEATURE_WINDOW, step=0.0002, wiggle=0.0002), spread=0.0003)
    px = candles[-1].close
    plain = make_engine(settings, rules, Storage(":memory:"), [Scripted()])
    plain.process_candle("SOL/BRL", candles, FillRefs(px, px))
    floored = make_engine(replace(settings, min_stop_pct=0.8), rules, Storage(":memory:"), [Scripted()])
    floored.process_candle("SOL/BRL", candles, FillRefs(px, px))
    a, b = plain.books["scripted"].positions["SOL/BRL"], floored.books["scripted"].positions["SOL/BRL"]
    assert (a.entry_price - a.stop_price) / a.entry_price * 100 < 0.8
    stop_pct = (b.entry_price - b.stop_price) / b.entry_price * 100
    assert stop_pct == pytest.approx(0.8, rel=0.02)
    assert (b.take_profit_price - b.entry_price) == pytest.approx(1.5 * (b.entry_price - b.stop_price))


# ------------------------------------------------------------------ candle de 2m montado de 1m
def one_minute(n, start):
    return [Candle(start + i * M1, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0 + i) for i in range(n)]


def test_native_timeframe_builds_2m_from_1m():
    assert native_timeframe("2m", BINANCE_TFS) == ("1m", 2)
    assert native_timeframe("15m", BINANCE_TFS) == ("15m", 1)
    assert native_timeframe("10m", BINANCE_TFS) == ("5m", 2)
    assert native_timeframe("2m", None) == ("2m", 1)  # corretora sem lista: pede direto


def test_aggregate_candles_aligns_and_drops_incomplete_groups():
    start = 1_789_999_980_000  # minuto ímpar: o primeiro 1m não fecha um 2m e é descartado
    assert start % (2 * M1) == M1
    out = aggregate_candles(one_minute(6, start), 2 * M1, 2)
    assert [c.ts % (2 * M1) for c in out] == [0, 0]  # alinhado ao relógio
    first = out[0]
    assert first.ts == start + M1
    assert (first.open, first.high, first.low, first.close, first.volume) == (101, 103, 100, 102.5, 2.0 + 3.0)
    assert len(out) == 2  # último 1m sozinho não vira candle


class FakeEx:
    timeframes = BINANCE_TFS

    def __init__(self, now):
        self.now, self.asked = now, []

    def fetch_ohlcv(self, sym, tf, limit=None, since=None):
        self.asked.append((tf, limit))
        start = self.now - self.now % M1 - limit * M1  # inclui o 1m ainda aberto no fim
        return [[c.ts, c.open, c.high, c.low, c.close, c.volume] for c in one_minute(limit + 1, start)]

    def milliseconds(self):
        return self.now


def test_fetch_closed_candles_2m_uses_1m_and_returns_only_closed():
    md = CCXTMarketData("binance")
    now = 1_790_000_000_000 - 1_790_000_000_000 % (2 * M1) + 90_000  # 1,5 min dentro do 2m atual
    md.exchange = FakeEx(now)
    out = md.fetch_closed_candles("BTC/USDT", "2m", 20)
    assert md.exchange.asked[0][0] == "1m"
    assert len(out) == 20 and all(b.ts - a.ts == 2 * M1 for a, b in zip(out, out[1:]))
    assert out[-1].ts + 2 * M1 <= now  # o 2m em formação fica de fora


# ------------------------------------------------------------------ Grok: score como chance e corte
def screener(handler, sent, **kw):
    storage = Storage(":memory:")

    def spy(r):
        sent.append(json.loads(r.content))
        return handler(r)
    return GrokScreener(grok_client(spy), "2m", 2 * M1, Ledger(storage.get, storage.set), lambda sym: [], **kw)


def test_min_score_sends_fair_entry_to_jev_even_without_grok_escalate():
    sent = []
    g = screener(lambda r: httpx.Response(200, json=grok_body(escalate=False, score=0.5)), sent, min_score=0.45)
    v = g.review(features(), None)
    assert v.escalate and v.raw["by_score"] is True
    low = screener(lambda r: httpx.Response(200, json=grok_body(escalate=False, score=0.3)), [], min_score=0.45)
    assert not low.review(features(), None).escalate
    off = screener(lambda r: httpx.Response(200, json=grok_body(escalate=False, score=0.9)), [])  # 0 = desligado
    assert not off.review(features(), None).escalate


def test_min_score_never_forces_exit_review():
    f = features()
    pos = Position("jev", f.symbol, 1.0, f.close, 0.0, f.ts - 2 * M1, stop_price=f.close * 0.99,
                   take_profit_price=f.close * 1.02)
    g = screener(lambda r: httpx.Response(200, json=grok_body(escalate=False, score=0.9)), [], min_score=0.45)
    assert not g.review(f, pos).escalate  # na saída o score é urgência: só o escalate do Grok vale


def test_grok_and_jev_read_the_floored_stop_and_target():
    f = features()
    sent = []
    g = screener(lambda r: httpx.Response(200, json=grok_body()), sent, min_stop_pct=5.0)
    g.review(f, None)
    user = sent[0]["messages"][1]["content"]
    assert "stop-loss -5.00%" in user and "Take-profit +7.50%" in user
    assert "probability" in user  # o score pedido é chance de alvo antes do stop
    jev = v2_decider(lambda r: None)
    jev.min_stop_pct = 5.0
    tp, sl, _ = jev.entry_threshold(f)
    assert (sl, tp) == pytest.approx((5.0, 7.5))


# ------------------------------------------------------------------ config
def test_config_reads_and_validates_new_knobs():
    s = Settings.from_env({"MIN_STOP_PCT": "0.6", "GROK_MIN_SCORE": "0.45", "TIMEFRAME": "2m"})
    assert (s.min_stop_pct, s.grok_min_score, s.timeframe) == (0.6, 0.45, "2m")
    d = Settings.from_env({})
    assert d.min_stop_pct == 0.0 and d.grok_min_score == 0.0  # padrão: comportamento antigo
    with pytest.raises(ConfigError):
        Settings.from_env({"MIN_STOP_PCT": "9"})
    with pytest.raises(ConfigError):
        Settings.from_env({"GROK_MIN_SCORE": "1.5"})


def test_demo_futures_can_read_candles_from_the_real_market():
    md = CCXTMarketData("binance", demo_futures=True, real_candles=True)
    assert md.candles_ex is md.real and md.real.id == "binanceusdm" and md.exchange is not md.real
    md.real = FakeEx(1_790_000_000_000 - 1_790_000_000_000 % (2 * M1) + 90_000)
    md.fetch_closed_candles("BTC/USDT", "2m", 5)
    assert md.real.asked  # candles do real; cotação e ordens seguem na demo (md.exchange)
    assert CCXTMarketData("binance", demo_futures=True).candles_ex is not None


def test_candle_closing_seconds_ago_waits_for_the_clock_guard():
    md = CCXTMarketData("binance")
    edge = 1_790_000_000_000 - 1_790_000_000_000 % (2 * M1)
    md.exchange = FakeEx(edge + 2_000)  # 2s depois do fechamento pelo relógio local
    last = md.fetch_closed_candles("BTC/USDT", "2m", 5)[-1]
    assert last.ts + 2 * M1 < edge  # o 2m que "acabou de fechar" espera a folga do relógio


# ------------------------------------------------------------------ pausa pós-stop
def test_book_waits_n_candles_after_a_stop_before_entering_again(settings, rules):
    e = make_engine(replace(settings, stop_cooldown_candles=2), rules, Storage(":memory:"), [Scripted()])
    closes = trend(FEATURE_WINDOW)
    c0 = make_candles(closes)
    e.process_candle("SOL/BRL", c0, FillRefs(c0[-1].close, c0[-1].close))
    stop = e.books["scripted"].positions["SOL/BRL"].stop_price
    path = closes + [stop * 0.9]
    for step, expect_open in enumerate([False, False, False, True]):  # candle do stop, +2 de pausa, depois entra
        cs = make_candles(path, spread=0.0)
        e.process_candle("SOL/BRL", cs, FillRefs(cs[-1].close, cs[-1].close))
        assert ("SOL/BRL" in e.books["scripted"].positions) is expect_open, step
        path.append(path[-1] * 1.001)
    assert e.books["scripted"].trades[-1].exit_reason == "stop"
