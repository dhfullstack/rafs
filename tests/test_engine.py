import httpx

from rafs.backtest import run_backtest, summarize
from rafs.deciders.base import Decider
from rafs.deciders.baseline import RandomBaseline, TrendBaseline
from rafs.engine import FEATURE_WINDOW, Engine, FillRefs
from rafs.exchanges.paper import PaperExchange
from rafs.models import Action, Decision
from rafs.risk import RiskEngine
from rafs.storage import Storage
from rafs.telegram import Telegram
from tests.conftest import make_candles, trend


class Scripted(Decider):
    """Decider de teste: compra no primeiro candle, depois segura."""

    name = "scripted"

    def __init__(self, action_flat=Action.BUY, action_pos=Action.HOLD):
        self.calls = 0
        self.flat, self.inpos = action_flat, action_pos

    def decide(self, f, position):
        self.calls += 1
        return Decision(self.name, f.symbol, f.ts, self.flat if position is None else self.inpos, None, "test")


def make_engine(settings, rules, storage, deciders, kill=lambda: False):
    ex = PaperExchange(rules, settings.fee_rate, settings.slippage_bps)
    return Engine(settings, storage, deciders, ex, RiskEngine(settings, kill_switch_check=kill))


def test_entry_is_recorded_and_survives_restart(settings, rules, tmp_path):
    candles = make_candles(trend(FEATURE_WINDOW))
    db = tmp_path / "t.sqlite3"
    d = Scripted()
    e = make_engine(settings, rules, Storage(db), [d])
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    book = e.books["scripted"]
    assert "SOL/BRL" in book.positions
    assert book.cash < settings.capital_brl
    cash = book.cash

    # "queda" e restart: estado vem do banco e o mesmo candle não é decidido de novo
    d2 = Scripted()
    e2 = make_engine(settings, rules, Storage(db), [d2])
    assert e2.books["scripted"].positions["SOL/BRL"].qty == book.positions["SOL/BRL"].qty
    assert abs(e2.books["scripted"].cash - cash) < 1e-9
    e2.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    assert d2.calls == 0


def test_stop_loss_closes_position_with_net_loss(settings, rules):
    closes = trend(FEATURE_WINDOW)
    candles = make_candles(closes)
    e = make_engine(settings, rules, Storage(":memory:"), [Scripted()])
    e.process_candle("SOL/BRL", candles, FillRefs(closes[-1], closes[-1]))
    pos = e.books["scripted"].positions["SOL/BRL"]
    crash = make_candles(closes + [pos.stop_price * 0.9], spread=0.0)
    e.process_candle("SOL/BRL", crash, FillRefs(crash[-1].close, crash[-1].close))
    book = e.books["scripted"]
    assert not book.positions
    t = book.trades[0]
    assert t.exit_reason == "stop" and t.pnl < 0
    # caixa final = inicial + pnl líquido (taxas incluídas)
    assert abs(book.cash - (settings.capital_brl + t.pnl)) < 1e-9


def test_kill_switch_blocks_entries_but_not_exits(settings, rules):
    closes = trend(FEATURE_WINDOW)
    candles = make_candles(closes)
    state = {"kill": False}
    d = Scripted()
    e = make_engine(settings, rules, Storage(":memory:"), [d], kill=lambda: state["kill"])
    e.process_candle("SOL/BRL", candles, FillRefs(closes[-1], closes[-1]))
    state["kill"] = True
    pos = e.books["scripted"].positions["SOL/BRL"]
    crash = make_candles(closes + [pos.stop_price * 0.9], spread=0.0)
    e.process_candle("SOL/BRL", crash, FillRefs(crash[-1].close, crash[-1].close))
    assert not e.books["scripted"].positions  # stop executou
    nxt = make_candles(closes + [pos.stop_price * 0.9, pos.stop_price * 0.91], spread=0.0)
    calls_before = d.calls
    e.process_candle("SOL/BRL", nxt, FillRefs(nxt[-1].close, nxt[-1].close))
    assert not e.books["scripted"].positions
    assert d.calls == calls_before  # nem consulta o decider (não gasta créditos)


def test_paused_blocks_entries(settings, rules):
    candles = make_candles(trend(FEATURE_WINDOW))
    st = Storage(":memory:")
    st.set_paused(True)
    e = make_engine(settings, rules, st, [Scripted()])
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    assert not e.books["scripted"].positions


def test_books_are_isolated(settings, rules):
    candles = make_candles(trend(FEATURE_WINDOW))
    e = make_engine(settings, rules, Storage(":memory:"), [Scripted(), RandomBaseline(p_buy=0.0)])
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    assert e.books["scripted"].positions and not e.books["random"].positions
    assert e.books["random"].cash == settings.capital_brl


def test_backtest_fills_at_next_open_no_lookahead(settings, rules):
    closes = trend(FEATURE_WINDOW + 1)
    candles = make_candles(closes)
    # último candle abre com gap: a compra decidida no fechamento anterior deve sair nesse open
    gap_open = closes[-2] * 1.05
    last = candles[-1]
    candles[-1] = type(last)(last.ts, gap_open, max(gap_open, last.high), min(gap_open, last.low), last.close, last.volume)
    eng = run_backtest(settings, [Scripted()], {"SOL/BRL": candles}, rules)
    pos = eng.books["scripted"].positions["SOL/BRL"]
    assert abs(pos.entry_price - gap_open * (1 + settings.slippage_bps / 10_000)) < 1e-9


def test_backtest_runs_all_controls_and_reports(settings, rules):
    import math
    import random
    rng = random.Random(7)
    closes = [100 * (1 + 0.05 * math.sin(i / 15)) * (1 + 0.001 * i) * (1 + rng.gauss(0, 0.004)) for i in range(600)]
    eng = run_backtest(settings, [TrendBaseline(), RandomBaseline()], {"SOL/BRL": make_candles(closes)}, rules)
    perf = summarize(eng)
    assert set(perf) == {"baseline", "random"}
    for name, p in perf.items():
        book = eng.books[name]
        # reconciliação: caixa + custo das posições abertas (com taxa) = capital + P&L realizado
        locked = sum(pp.cost_brl + pp.entry_fee for pp in book.positions.values())
        assert abs(book.cash + locked - settings.capital_brl - p.net_pnl) < 1e-6
        assert p.net_pnl == book.realized_total()
    assert perf["baseline"].trades > 0


# ---------------- telegram
def tg(handler, storage):
    return Telegram("TOKEN", "42", {"/status": lambda: "ok status"}, on_pause=lambda: storage.set_paused(True),
                    get_offset=lambda: int(storage.get("o", "0")), set_offset=lambda v: storage.set("o", str(v)),
                    transport=httpx.MockTransport(handler))


def test_telegram_ignores_other_chats_and_pauses():
    st = Storage(":memory:")
    sent = []

    def handler(req):
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"result": [
                {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/pause"}},
                {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/status"}},
            ]})
        sent.append(req.content)
        return httpx.Response(200, json={"ok": True})

    bot = tg(handler, st)
    assert bot.poll_commands() == 1
    assert not st.is_paused()  # /pause veio de um chat não autorizado
    assert st.get("o") == "3"
    assert bot.handle("/pause").startswith("⏸️") and st.is_paused()


def test_telegram_has_no_resume_or_sensitive_commands():
    st = Storage(":memory:")
    bot = tg(lambda r: httpx.Response(200, json={}), st)
    st.set_paused(True)
    for cmd in ("/resume", "/withdraw", "/setlimit", "/keys"):
        assert bot.handle(cmd).startswith("Comando não reconhecido")
    assert st.is_paused()
