"""Fills a throwaway database with FICTIONAL data, only to show the dashboard (README screenshots).

Nothing here came from a real run or a backtest: trades, curves and agent telemetry are generated with a fixed
seed so every screenshot looks the same. The real results live in the README section "What the numbers said".

    python scripts/seed_demo.py showcase            # writes showcase/rafs.sqlite3 (overwrites)
    python scripts/seed_demo.py showcase --live     # same, then keeps the heartbeat fresh until Ctrl+C

Then open the dashboard against it (PowerShell):

    $env:DATA_DIR="showcase"; $env:MODE="PAPER"; $env:SYMBOLS="BTC/USDT,ETH/USDT,SOL/USDT"
    $env:CAPITAL_BRL="75000"; $env:ENTRY_BRL="12000"; $env:MAX_EXPOSURE_BRL="30000"; $env:DAILY_LOSS_LIMIT_BRL="3000"
    $env:MEME_SLEEVE="0"; $env:QUANT_BOOK="0"; $env:DASHBOARD_PASSWORD="demo"
    python -m rafs dashboard
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rafs.models import Position, Trade  # noqa: E402
from rafs.portfolio import utc_day  # noqa: E402
from rafs.storage import Storage  # noqa: E402

NY = ZoneInfo("America/New_York")
HOUR, MIN15 = 3_600_000, 900_000
INITIAL = 75_000.0
FEE = 0.001
PRICES = {"BTC/USDT": 101_840.0, "ETH/USDT": 3_412.6, "SOL/USDT": 187.42}
# book -> (wins, losses, net realized target in $ or None = computed for jev, avg win, avg loss)
BOOKS = {
    "jev": (98, 24, None, 190.0, 300.0),
    "baseline": (38, 58, -1_846.37, 160.0, 140.0),
    "random": (26, 54, -2_731.90, 150.0, 130.0),
}
JEV_EQUITY = 88_234.22
DAYS = 45


def price_paths(rng: random.Random, start: int, now: int) -> dict[str, list[tuple[int, float]]]:
    """Hourly random walk that ends exactly at today's price."""
    out = {}
    n = (now - start) // HOUR + 2
    for sym, last in PRICES.items():
        vol = 0.006 if sym.startswith("BTC") else 0.008 if sym.startswith("ETH") else 0.011
        logs = [0.0]
        for _ in range(n - 1):
            logs.append(logs[-1] + rng.gauss(0.00004, vol))
        shift = math.log(last) - logs[-1]
        out[sym] = [(now - (n - 1 - i) * HOUR, math.exp(v + shift)) for i, v in enumerate(logs)]
    return out


def price_at(path: list[tuple[int, float]], ts: int) -> float:
    i = max(0, min(len(path) - 1, (ts - path[0][0]) // HOUR))
    return path[i][1]


def make_trades(rng, book, wins, losses, net, avg_w, avg_l, start, now, paths, shift=0.0):
    n = wins + losses
    # losses come in streaks (bad weeks), not evenly spread: rank a noisy wave and the top scores lose
    phase = rng.uniform(0, 2 * math.pi)
    score = [0.55 * rng.random() + 0.45 * (math.sin(2 * math.pi * 3.2 * i / n + phase) + 1) / 2 for i in range(n)]
    cut = sorted(score, reverse=True)[losses - 1] if losses else 2.0
    outcomes = [sc < cut for sc in score]
    outcomes[-2:] = [True, True]  # the two latest trades closed today, both wins
    k = sum(not o for o in outcomes) - losses  # keep the exact count after forcing the last two
    for i in range(n - 3, -1, -1):
        if k <= 0:
            break
        if not outcomes[i]:
            outcomes[i], k = True, k - 1
    for i in range(n - 3, -1, -1):
        if k >= 0:
            break
        if outcomes[i]:
            outcomes[i], k = False, k + 1
    exits = sorted(rng.randint(start + 6 * HOUR, now - 6 * HOUR) for _ in range(n - 2)) + [now - 95 * 60_000, now - 35 * 60_000]
    raw = [rng.lognormvariate(math.log(avg_w), 0.55) if w else -rng.lognormvariate(math.log(avg_l), 0.45)
           for w in outcomes]
    W = sum(p for p in raw if p > 0)
    L = sum(p for p in raw if p < 0)
    f = (net - W) / L if L else 1.0  # scale only the losses so the net lands exactly on target
    assert 0.2 < f < 5, (book, f)
    pnls = [p if p > 0 else p * f for p in raw]
    trades = []
    for ts, pnl, win in zip(exits, pnls, outcomes):
        exit_ts = ts - ts % MIN15
        hold = rng.randint(2, 32) * MIN15
        entry_ts = exit_ts - hold
        sym = rng.choice(list(PRICES))
        entry = price_at(paths[sym], entry_ts) * (1 + rng.uniform(-0.002, 0.002))
        notional = rng.uniform(9_000, 14_000)
        qty = notional / entry
        fees = notional * FEE * 2
        exit_px = entry + (pnl + fees) / qty
        reason = ("alvo" if rng.random() < 0.8 else "decider:SELL") if win else ("stop" if rng.random() < 0.85 else "tempo")
        trades.append(Trade(book, sym, round(qty, 8), round(entry, 4), round(exit_px, 4), entry_ts, exit_ts,
                            round(fees, 4), round(pnl, 4), reason))
    return trades


def curve(rng, trades, start, now, step=HOUR):
    out, cum, eq_noise = [], 0.0, 0.0
    done = sorted(trades, key=lambda t: t.exit_ts)
    k = 0
    for ts in range(start, now, step):
        while k < len(done) and done[k].exit_ts <= ts:
            cum += done[k].pnl
            k += 1
        live = sum(t.pnl * (ts - t.entry_ts) / max(1, t.exit_ts - t.entry_ts) for t in done[k:k + 6]
                   if t.entry_ts <= ts < t.exit_ts)
        eq_noise = 0.85 * eq_noise + rng.gauss(0, 12)
        out.append((ts, round(INITIAL + cum + live + eq_noise, 2)))
    return out


def seed(path: Path, now: int) -> None:
    rng = random.Random(20261106)
    if path.exists():
        path.unlink()
    st = Storage(path)
    db = st.db
    start = now - DAYS * 24 * HOUR
    paths = price_paths(rng, start, now)

    # --- open positions (priced against the quotes written below)
    open_pos = {
        "jev": [Position("jev", "BTC/USDT", 0.1182, 100_912.5, 11.93, now - 3 * HOUR - 7 * MIN15, 99_204.0, 103_480.0),
                Position("jev", "SOL/USDT", 61.4, 185.96, 11.42, now - HOUR - 2 * MIN15, 182.1, 191.8)],
        "baseline": [Position("baseline", "ETH/USDT", 3.05, 3_428.4, 10.46, now - 5 * MIN15, 3_371.0, 3_514.0)],
        "random": [],
    }
    pos_pnl = {b: sum(p.unrealized(PRICES[p.symbol]) - p.entry_fee for p in ps) for b, ps in open_pos.items()}

    for book, (w, l, net, aw, al) in BOOKS.items():
        if net is None:
            net = JEV_EQUITY - INITIAL - pos_pnl[book]
        trades = make_trades(rng, book, w, l, net, aw, al, start, now, paths)
        realized = sum(t.pnl for t in trades)
        cash = INITIAL + realized - sum(p.cost_brl + p.entry_fee for p in open_pos[book])
        db.execute("INSERT INTO books VALUES(?,?,?)", (book, cash, INITIAL))
        for t in sorted(trades, key=lambda t: t.exit_ts):
            db.execute("INSERT INTO trades(book,symbol,exit_ts,data) VALUES(?,?,?,?)",
                       (book, t.symbol, t.exit_ts, json.dumps(asdict(t))))
        for p in open_pos[book]:
            db.execute("INSERT INTO positions VALUES(?,?,?)", (book, p.symbol, json.dumps(asdict(p))))
        for ts, eq in curve(rng, trades, start, now - 10 * 60_000):
            st.record_equity(book, ts, eq)
        # orders: the latest fills (entry + exit per trade)
        for i, t in enumerate(sorted(trades, key=lambda t: t.exit_ts)[-25:]):
            for side, px, ts in (("buy", t.entry_price, t.entry_ts), ("sell", t.exit_price, t.exit_ts)):
                db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (f"demo-{book}-{i}-{side}", book, t.symbol, side, t.qty, px, t.fees / 2, "filled",
                            "simulated fill · 5 bps slippage", 1, ts + 4_000))
        for p in open_pos[book]:
            db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (f"demo-{book}-open-{p.symbol}", book, p.symbol, "buy", p.qty, p.entry_price, p.entry_fee,
                        "filled", "simulated fill · 5 bps slippage", 1, p.entry_ts + 4_000))

    # --- decisions: every closed 15m candle of the last 2 days, per pair
    first = now - now % MIN15 - 2 * 24 * HOUR
    for ts in range(first, now - MIN15, MIN15):
        for sym in PRICES:
            r = rng.random()
            if r < 0.55:
                action, conf, reason, credits = "HOLD", None, "filtro: trend 24h below average, no call", 0.0
            elif r < 0.86:
                p = rng.uniform(0.30, 0.47)
                action, conf, reason, credits = "HOLD", p, f"P(target before stop) {p:.2f} below break-even 0.47", 1.0
            elif r < 0.95:
                p = rng.uniform(0.53, 0.71)
                action, conf, reason, credits = "BUY", p, f"P(target before stop) {p:.2f} clears break-even 0.47 + 0.05", 1.0
            else:
                action, conf, reason, credits = "SELL", rng.uniform(0.6, 0.8), "momentum faded under VWAP, exit early", 1.0
            db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("jev", sym, ts, utc_day(ts), action, conf, reason, "{}", credits, ts + MIN15 + 2_500))
            b_act = "BUY" if rng.random() < 0.06 else "HOLD"
            db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("baseline", sym, ts, utc_day(ts), b_act, None,
                        "EMA12 > EMA26, RSI 45-70, above VWAP" if b_act == "BUY" else "rule not met", "{}", 0.0,
                        ts + MIN15 + 1_200))

    # --- market snapshot
    for sym, px in PRICES.items():
        spread = px * 0.00008
        st.set_json(f"quote:{sym}", {"bid": px - spread / 2, "ask": px + spread / 2, "spread_pct": 0.008, "ts": now - 4_000})
        st.set_json(f"features:{sym}", {"ts": now - now % MIN15 - MIN15, "close": px, "volume": rng.uniform(80, 900),
                                        "rel_volume": round(rng.uniform(0.8, 1.9), 2), "rsi": round(rng.uniform(52, 64), 1),
                                        "atr_pct": round(rng.uniform(0.25, 0.45), 3),
                                        "momentum_pct": round(rng.uniform(0.2, 1.4), 2), "regime": "trend_up"})

    # --- desk: today's QUANT dial and CHIEF's tool check
    ny = datetime.fromtimestamp(now / 1000, NY)
    st.set_json("desk:dial", {
        "date": ny.date().isoformat(), "computed_at": now - 2 * HOUR,
        "inputs": {"net_liq_4w": 41.7, "vix": 15.8, "hy_4w": -0.06, "btc_vs_200d": 0.0912, "sol_vs_50d": 0.0641,
                   "qqq_vs_200d": 0.0733},
        "greens": {"liquidity": True, "volatility": True, "credit": True, "btc": True, "sol": True, "nasdaq": True},
        "dial": {"meme": 1.0, "stocks": 1.0, "crypto": 1.0},
        "crypto_targets": {"BTC": 0.5, "SOL": 0.5}, "momentum_12_1": {"BTC": 0.4127, "ETH": -0.0315, "SOL": 0.3389},
        "data_age_hours": {}, "degraded": [],
    })
    st.set_json("desk:proof", {"day": ny.date().isoformat(), "ready": True, "degraded": [], "checks": {
        "Binance market data": {"ok": True, "detail": "BTC/USDT, ETH/USDT, SOL/USDT candles + order book", "ms": 212},
        "FRED series": {"ok": True, "detail": "6 of 6 series downloaded", "ms": 1840},
        "Jev API": {"ok": True, "detail": "credits ok, idempotent call", "ms": 640},
        "Notifications": {"ok": True, "detail": "Telegram + Discord", "ms": 310},
    }})
    st.set("desk:report_day", (ny - timedelta(days=1)).date().isoformat())
    st.set("desk:last_report", "Desk report (simulated)\njev: +6 trades, 5 wins\nbaseline: +3 trades, 1 win\n"
                               "random: +2 trades, 0 wins\nDial crypto 1.00 · targets BTC + SOL")

    # --- events
    msgs = [("INFO", "QUANT: dial published, crypto 1.00 · targets BTC 0.50 + SOL 0.50"),
            ("INFO", "CHIEF: tool check READY (4/4)"),
            ("INFO", "jev: BUY SOL/USDT @ 185.96 · stop 182.10 · target 191.80"),
            ("INFO", "jev: BUY BTC/USDT @ 100,912.50 · stop 99,204.00 · target 103,480.00"),
            ("INFO", "baseline: BUY ETH/USDT @ 3,428.40"),
            ("RISK", "RISK: spread on SOL/USDT above 0.05% for one candle, entry skipped"),
            ("CONTROL", "Dashboard: new entries allowed."),
            ("INFO", "Session started in PAPER mode, 3 pairs, 15m")]
    for i, (lvl, msg) in enumerate(msgs):
        db.execute("INSERT INTO events(ts,level,message) VALUES(?,?,?)", (now - (i * 47 + 3) * 60_000, lvl, msg))
    db.commit()
    touch(st, now)
    st.close()


AGENT_ACTIVITY = {
    "CHIEF": ("Tool check READY · next report 23:55 NY", 1840.0, 31),
    "QUANT": ("Dial crypto 1.00 · targets BTC + SOL", 2210.0, 31),
    "SCAN": ("BTC/USDT, ETH/USDT, SOL/USDT · 15m candle closed", 186.0, 4318),
    "JUDGE": ("SOL/USDT: HOLD · P(target before stop) 0.41", 642.0, 1966),
    "SIZE": ("Ticket $12,000 × dial 1.00", 0.4, 214),
    "FILLS": ("SOL/USDT buy filled · 5 bps slippage", 38.0, 428),
    "RISK": ("All checks green · exposure $23.2k of $30k", 0.9, 4318),
}


def touch(st: Storage, now: int) -> None:
    st.set("heartbeat", str(now))
    for name, (activity, lat, runs) in AGENT_ACTIVITY.items():
        st.set_json(f"agent:{name}", {"ts": now - 20_000, "activity": activity, "ok": True, "latency_ms": lat,
                                      "last_error": None, "last_error_ts": None, "runs": runs})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir", nargs="?", default="showcase")
    ap.add_argument("--live", action="store_true", help="keep the heartbeat fresh until Ctrl+C")
    a = ap.parse_args()
    d = Path(a.data_dir)
    d.mkdir(parents=True, exist_ok=True)
    db = d / "rafs.sqlite3"
    seed(db, int(time.time() * 1000))
    print(f"demo data written to {db} (fictional, for screenshots only)")
    if a.live:
        st = Storage(db)
        try:
            while True:
                time.sleep(20)
                touch(st, int(time.time() * 1000))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
