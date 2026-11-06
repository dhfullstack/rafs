from dataclasses import replace

import pytest

from rafs.config import ConfigError, Settings
from rafs.exchanges.base import OrderRequest, TradingRules
from rafs.exchanges.ccxt_data import DataQualityError, validate_candles
from rafs.exchanges.paper import PaperExchange
from rafs.models import Position, Trade
from rafs.portfolio import Book
from rafs.risk import MAX_CONSECUTIVE_ERRORS, RiskEngine
from rafs.sizing import size_entry
from tests.conftest import T0, TF, make_candles


def pos(symbol="SOL/BRL", qty=0.02, price=500.0):
    return Position("b", symbol, qty, price, 0.01, T0, price * 0.98, price * 1.03)


# ---------------- config
def test_live_mode_is_refused():
    with pytest.raises(ConfigError):
        Settings.from_env({"MODE": "LIVE"})


def test_defaults_are_paper():
    s = Settings.from_env({})
    assert s.mode == "PAPER"
    assert s.capital_brl == 200 and s.max_exposure_brl == 50


def test_exposure_cannot_exceed_capital():
    with pytest.raises(ConfigError):
        Settings.from_env({"MAX_EXPOSURE_BRL": "500"})


# ---------------- risk
def test_risk_allows_clean_entry(settings):
    v = RiskEngine(settings).check_entry(Book("b", 200, 200), "SOL/BRL", 15, T0, 0.05, paused=False)
    assert v.allowed, v.reasons


def test_risk_blocks_kill_switch(settings):
    settings.kill_switch_path.write_text("x")
    v = RiskEngine(settings).check_entry(Book("b", 200, 200), "SOL/BRL", 15, T0, 0.05, paused=False)
    assert not v.allowed and "kill switch ativo" in v.reasons


def test_risk_blocks_pause_exposure_duplicate_and_spread(settings):
    book = Book("b", 200, 200, positions={"SOL/BRL": pos(qty=0.08, price=500)})  # R$ 40 expostos
    v = RiskEngine(settings).check_entry(book, "SOL/BRL", 15, T0, 0.9, paused=True)
    text = " | ".join(v.reasons)
    assert "pausadas" in text and "já existe posição" in text and "exposição" in text and "spread" in text


def test_risk_blocks_entry_above_max(settings):
    v = RiskEngine(settings).check_entry(Book("b", 200, 200), "SOL/BRL", 30, T0, None, paused=False)
    assert not v.allowed


def test_risk_daily_loss_limit(settings):
    book = Book("b", 190, 200)
    book.trades.append(Trade("b", "ETH/BRL", 1, 20, 9.5, T0, T0 + TF, 0.0, -10.5, "stop"))
    v = RiskEngine(settings).check_entry(book, "SOL/BRL", 15, T0 + 2 * TF, None, paused=False)
    assert not v.allowed and any("limite diário" in r for r in v.reasons)
    # no dia seguinte libera
    v2 = RiskEngine(settings).check_entry(book, "SOL/BRL", 15, T0 + 86_400_000, None, paused=False)
    assert v2.allowed


def test_circuit_breaker(settings):
    r = RiskEngine(settings)
    for _ in range(MAX_CONSECUTIVE_ERRORS):
        r.record_error()
    assert not r.check_entry(Book("b", 200, 200), "SOL/BRL", 15, T0, None, paused=False).allowed
    r.record_success()
    assert r.check_entry(Book("b", 200, 200), "SOL/BRL", 15, T0, None, paused=False).allowed


# ---------------- size
def test_size_rejects_below_exchange_minimum(settings):
    # caso real BTC/BRL: passo 0.00001 BTC a ~R$ 429 mil → R$ 10 vira 0.00002 = R$ 8.59 < mínimo
    s = replace(settings, entry_brl=10)
    btc = TradingRules("BTC/BRL", 10.0, 0.00001)
    qty, why = size_entry(s, Book("b", 200, 200), btc, 429_347.0, atr_pct=0.3)
    assert qty == 0 and "mínimo" in why


def test_size_never_exceeds_entry_and_shrinks_with_volatility(settings, rules):
    calm, _ = size_entry(settings, Book("b", 200, 200), rules("SOL/BRL"), 500.0, atr_pct=0.5)
    wild, _ = size_entry(settings, Book("b", 200, 200), rules("SOL/BRL"), 500.0, atr_pct=1.4)
    assert calm * 500 <= settings.entry_brl
    assert 0 < wild < calm


def test_size_respects_remaining_exposure(settings, rules):
    book = Book("b", 200, 200, positions={"ETH/BRL": pos("ETH/BRL", qty=0.08, price=500)})  # R$ 40
    qty, why = size_entry(settings, book, rules("SOL/BRL"), 500.0, atr_pct=0.5)
    assert qty * 500 <= 10.0 + 1e-9


# ---------------- paper exchange
def test_paper_fill_applies_slippage_and_fee(rules):
    ex = PaperExchange(rules, fee_rate=0.001, slippage_bps=10)
    res = ex.place_order(OrderRequest("o1", "SOL/BRL", "buy", 0.03, 500.0))
    assert res.status == "filled" and res.simulated
    assert res.price == pytest.approx(500.5)
    assert res.fee == pytest.approx(0.03 * 500.5 * 0.001)
    sell = ex.place_order(OrderRequest("o2", "SOL/BRL", "sell", 0.03, 500.0))
    assert sell.price == pytest.approx(499.5)


def test_paper_is_idempotent(rules):
    ex = PaperExchange(rules, 0.001, 5)
    a = ex.place_order(OrderRequest("same", "SOL/BRL", "buy", 0.03, 500.0))
    b = ex.place_order(OrderRequest("same", "SOL/BRL", "buy", 0.05, 600.0))
    assert a is b


def test_paper_rejects_below_minimum(rules):
    res = PaperExchange(rules, 0.001, 5).place_order(OrderRequest("o", "SOL/BRL", "buy", 0.01, 500.0))
    assert res.status == "rejected"


# ---------------- data quality
def test_validate_candles_dedupes_and_orders():
    c = make_candles([1.0, 2.0, 3.0])
    out = validate_candles([c[2], c[0], c[1], c[1]], TF)
    assert [x.ts for x in out] == [c[0].ts, c[1].ts, c[2].ts]


def test_validate_candles_rejects_gaps_and_stale():
    c = make_candles([1.0, 2.0, 3.0])
    with pytest.raises(DataQualityError):
        validate_candles([c[0], c[2]], TF)
    with pytest.raises(DataQualityError):
        validate_candles(c, TF, now_ms=c[-1].ts + 10 * TF)
