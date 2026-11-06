from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from rafs.deciders.rotation import QuantRotation
from rafs.desk import ACCOUNT_STOP_PCT, Desk, desk_context, report_text
from rafs.dial import NY, compute_dial, crypto_targets, floor2, net_liquidity, ny_day
from rafs.engine import FEATURE_WINDOW, Engine, FillRefs
from rafs.exchanges.base import Quote, TradingRules
from rafs.exchanges.paper import PaperExchange
from rafs.risk import RiskEngine
from rafs.sizing import size_entry
from rafs.storage import Storage
from tests.conftest import make_candles, trend
from tests.test_engine import Scripted

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=NY)  # quinta, meio do dia em Nova York
NOW_MS = int(NOW.timestamp() * 1000)
PAIRS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def a_dial(crypto=0.66, targets=None, now_ms=NOW_MS):
    return {"date": ny_day(now_ms), "computed_at": now_ms - 60_000, "inputs": {"btc_vs_200d": 0.0512},
            "greens": {"liquidity": True, "credit": True, "btc": False},
            "dial": {"crypto": crypto, "meme": 0.5, "stocks": 0.5},
            "crypto_targets": {"BTC": 0.5, "SOL": 0.5} if targets is None else targets,
            "momentum_12_1": {"BTC": 0.3, "ETH": -0.12, "SOL": 0.2}, "degraded": []}


class FakeMD:
    def __init__(self, prices):
        self.prices = prices

    def get_quote(self, symbol):
        p = self.prices[symbol]
        return Quote(symbol, p * 0.9995, p * 1.0005, NOW_MS)


def desk_for(settings, storage, deciders, rules, tools=None, sleeves=None, gate=True, prices=None, notes=None):
    ex = PaperExchange(rules, settings.fee_rate, settings.slippage_bps)
    engine = Engine(settings, storage, deciders, ex, RiskEngine(settings, kill_switch_check=lambda: False),
                    notify=(notes.append if notes is not None else None))
    md = FakeMD(prices or {"BTC/USDT": 100.0, "ETH/USDT": 50.0, "SOL/USDT": 20.0, "SOL/BRL": 100.0})
    desk = Desk(settings, storage, engine, md, lambda ny: a_dial(), tools or {}, sleeves or {}, gate_all_books=gate,
                clock=lambda: NOW_MS / 1000)
    return engine, desk


# ---------------------------------------------------------------- QUANT (dial)
def test_floor2_never_rounds_up():
    assert floor2(2 / 3) == 0.66
    assert floor2(0.67) == 0.67
    assert floor2(1.0) == 1.0 and floor2(0.0) == 0.0


def test_net_liquidity_in_billions():
    d = date(2026, 10, 21)
    # WALCL e WTREGEN em milhões, RRP em bilhões
    assert net_liquidity([(d, 6_600_000.0)], [(d, 850_000.0)], [(d, 12.5)]) == [(d, pytest.approx(5737.5))]


def test_crypto_targets_top2_positive_equal_weight():
    assert crypto_targets({"BTC": 0.3, "ETH": -0.1, "SOL": 0.5}) == {"SOL": 0.5, "BTC": 0.5}
    assert crypto_targets({"BTC": 0.1, "ETH": None, "SOL": -0.2}) == {"BTC": 1.0}
    assert crypto_targets({"BTC": -0.3, "ETH": -0.1, "SOL": None}) == {}  # caixa


def rising(last: date, n=420, start=100.0):
    return [(last - timedelta(days=n - 1 - i), start * 1.002 ** i) for i in range(n)]


def test_dial_counts_stale_series_as_red():
    asof = datetime(2026, 10, 29, 6, 0, tzinfo=NY)
    fresh = compute_dial({}, {"BTC": rising(date(2026, 10, 28))}, asof)
    assert fresh["greens"]["btc"] is True and "BTC" not in fresh["degraded"]
    assert fresh["crypto_targets"] == {"BTC": 1.0}
    assert fresh["dial"]["crypto"] == 0.33  # 1 de 3 verdes (sem FRED), nunca arredondado para cima
    assert {"WALCL", "VIXCLS", "ETH", "SOL"} <= set(fresh["degraded"])

    stale = compute_dial({}, {"BTC": rising(date(2026, 10, 20))}, asof)  # 9 dias sem candle novo
    assert "BTC" in stale["degraded"] and stale["greens"]["btc"] is False
    assert stale["crypto_targets"] == {} and stale["dial"]["crypto"] == 0.0


# ---------------------------------------------------------------- mesa: NO DIAL, NO DESK + SIZE × dial
def test_entry_block_follows_dial_and_targets(settings, rules, tmp_path):
    s = replace(settings, symbols=PAIRS)
    storage = Storage(s.db_path)
    _, desk = desk_for(s, storage, [Scripted()], rules)
    assert "NO DIAL" in desk.entry_block("scripted", "BTC/USDT")
    assert desk.size_multiplier() == 0.0
    assert desk_context(storage, NOW_MS) == "desk crypto_dial=none (no dial published today)"

    storage.set_json("desk:dial", a_dial(crypto=0.33))
    assert "mesa parada" in desk.entry_block("scripted", "BTC/USDT")

    storage.set_json("desk:dial", a_dial(crypto=0.66))
    assert desk.entry_block("scripted", "BTC/USDT") is None
    assert "ETH fora dos alvos" in desk.entry_block("scripted", "ETH/USDT")
    assert desk.size_multiplier() == 0.66
    assert desk_context(storage, NOW_MS) == "desk crypto_dial=0.66 btc_vs_200d=+0.0512"

    storage.set_json("desk:dial", a_dial(now_ms=NOW_MS - 25 * 3_600_000))  # "a dial older than 24h is no dial"
    assert "NO DIAL" in desk.entry_block("scripted", "BTC/USDT")


def test_desk_rules_off_only_risk_blocks(settings, rules):
    storage = Storage(settings.db_path)
    _, desk = desk_for(settings, storage, [Scripted()], rules, gate=False)
    assert desk.entry_block("scripted", "SOL/BRL") is None and desk.size_multiplier() == 1.0


def test_size_entry_dial_only_reduces(settings, rules):
    from rafs.portfolio import Book
    book = Book("x", 200.0, 200.0)
    full, _ = size_entry(settings, book, rules("SOL/BRL"), 1.0, 0.1, dial=1.0)
    half, _ = size_entry(settings, book, rules("SOL/BRL"), 1.0, 0.1, dial=0.75)
    capped, _ = size_entry(settings, book, rules("SOL/BRL"), 1.0, 0.1, dial=3.0)
    assert half == pytest.approx(full * 0.75) and capped == full
    assert size_entry(settings, book, rules("SOL/BRL"), 1.0, 0.1, dial=0.0) == (0.0, "dial 0: sleeve parado")


def test_engine_records_desk_block_without_calling_decider(settings, tmp_path):
    rules = lambda symbol: TradingRules(symbol, min_cost=5.0, amount_step=0.001)  # noqa: E731
    storage = Storage(settings.db_path)
    d = Scripted()
    e, desk = desk_for(settings, storage, [d], rules)
    candles = make_candles(trend(FEATURE_WINDOW + 1))
    e.process_candle("SOL/BRL", candles[:-1], FillRefs(candles[-2].close, candles[-2].close))
    assert d.calls == 0 and not e.books["scripted"].positions
    rec = storage.get_decision("scripted", "SOL/BRL", candles[-2].ts)
    assert rec[0] == "HOLD" and rec[1].startswith("mesa: sem dial do QUANT")

    storage.set_json("desk:dial", a_dial(crypto=0.75, targets={"SOL": 1.0}))
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    assert d.calls == 1
    pos = e.books["scripted"].positions["SOL/BRL"]
    assert pos.qty * pos.entry_price <= settings.entry_brl * 0.75 * 1.01  # ticket × dial


# ---------------------------------------------------------------- CHIEF: prova das ferramentas
def test_prove_tools_degrades_only_the_sleeve_that_needs_it(settings, rules):
    storage = Storage(settings.db_path)
    notes = []

    def broken():
        raise RuntimeError("429")
    _, desk = desk_for(settings, storage, [Scripted()], rules, notes=notes,
                       tools={"Jev": broken, "Corretora": lambda: "ok"},
                       sleeves={"jev": ("Jev", "Corretora"), "scripted": ("Corretora",)})
    proof = desk.prove_tools(NOW_MS, ny_day(NOW_MS))
    assert proof["ready"] is False and proof["degraded"] == ["jev"]
    assert "DEGRADED" in desk.book_block("jev") and desk.book_block("scripted") is None
    assert len(notes) == 1
    desk.prove_tools(NOW_MS + 1000, ny_day(NOW_MS))
    assert len(notes) == 1  # mesmo resultado no mesmo dia: não repete a mensagem


# ---------------------------------------------------------------- RISK: conta −4% no dia → FLAT
def test_account_down_4pct_goes_flat(settings, rules):
    storage = Storage(settings.db_path)
    notes = []
    e, desk = desk_for(settings, storage, [Scripted()], rules, notes=notes)
    candles = make_candles(trend(FEATURE_WINDOW))
    desk.gate_all_books = False
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    book = e.books["scripted"]
    assert book.positions
    day = ny_day(NOW_MS)
    equity = book.equity(e.last_prices)
    storage.set(f"desk:open:{day}:scripted", repr(equity / (1 - (ACCOUNT_STOP_PCT + 0.5) / 100)))
    desk._account_risk(NOW_MS, NOW, day)
    assert not book.positions
    assert storage.get("desk:flat:scripted") == day
    assert "FLAT" in desk.entry_block("scripted", "SOL/BRL")
    assert any("FLAT até amanhã" in n for n in notes)


# ---------------------------------------------------------------- sleeve crypto: rotação 12-1 × dial às 20:05
def test_quant_rotation_buys_targets_sells_leavers_and_stands_down(settings, rules):
    s = replace(settings, symbols=PAIRS)
    storage = Storage(s.db_path)
    e, desk = desk_for(s, storage, [QuantRotation()], rules)
    book = e.books["quant"]
    day = ny_day(NOW_MS)

    storage.set_json("desk:dial", a_dial(crypto=0.66, targets={"BTC": 0.5, "SOL": 0.5}))
    desk.rebalance_quant(NOW_MS, day)
    assert set(book.positions) == {"BTC/USDT", "SOL/USDT"}
    assert all(p.take_profit_price == 0 for p in book.positions.values())  # saída só pelo RISK das 20:05
    assert book.exposure() <= s.max_exposure_brl

    storage.set_json("desk:dial", a_dial(crypto=0.66, targets={"BTC": 1.0}))
    done = desk.rebalance_quant(NOW_MS + 86_400_000, day)
    assert set(book.positions) == {"BTC/USDT"} and any("SOL saiu dos alvos" in x for x in done)

    storage.set_json("desk:dial", a_dial(crypto=0.33, targets={"BTC": 1.0}))
    done = desk.rebalance_quant(NOW_MS + 2 * 86_400_000, day)
    assert not book.positions and "dial < 0.50: caixa" in done


def test_quant_book_is_skipped_by_the_candle_loop(settings, rules):
    storage = Storage(settings.db_path)
    q = QuantRotation()
    e, _ = desk_for(settings, storage, [q], rules)
    candles = make_candles(trend(FEATURE_WINDOW))
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    assert storage.get_decision("quant", "SOL/BRL", candles[-1].ts) is None


# ---------------------------------------------------------------- relatório das 23:55
def test_report_is_built_from_the_database(settings, rules):
    storage = Storage(settings.db_path)
    storage.set_json("desk:dial", a_dial())
    storage.set_json(f"desk:dial:{ny_day(NOW_MS)}", a_dial())
    text = report_text(storage, settings, {}, ny_day(NOW_MS), NOW_MS)
    assert text.startswith(f"DESK {ny_day(NOW_MS)}") and "SIMULADOS" in text
    assert "crypto 0.66" in text and "BTC 0.50 + SOL 0.50" in text
    assert "CHIEF    sem checagem hoje" in text
