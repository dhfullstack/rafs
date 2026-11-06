"""Monta o JSON do painel a partir do banco. Só leitura — nada aqui altera estado."""

from __future__ import annotations

import math
import time
from dataclasses import asdict
from pathlib import Path

from ..config import Settings
from ..desk import MEME_BOOKS, day_start_ms, next_times, sleeves_off
from ..dial import is_valid, ny_day
from ..metrics import max_drawdown, performance
from ..portfolio import utc_day
from ..storage import Storage

DISCLAIMER = "SIMULATED — no real money. Values are not real profit."
MODE_DB = {"PAPER": "rafs.sqlite3", "TESTNET": "rafs-testnet.sqlite3", "FUTURES": "rafs-futures.sqlite3"}
AGENTS = [  # as 8 cadeiras do guia "The Hedge Fund Terminal"
    ("CHIEF", "Checks every tool at 06:00 NY, reads the market (Grok), escalates to Jev and sends the 23:55 report"),
    ("QUANT", "Daily risk dial (FRED + Binance) and 12-1 momentum targets. Never trades"),
    ("SCAN", "Candles for the fixed pairs. Memes: GeckoTerminal (solana/bsc) with the guide's cut"),
    ("JUDGE", "Jev decides buy, hold or sell. Memes: picks one candidate or none. Reads the dial, never changes it"),
    ("STOCKS", "Stocks and the Runner (TQQQ/SHY) from the guide"),
    ("SIZE", "Ticket × dial and exchange minimums. Memes: Kelly, 6% cap, 2% of liquidity. Only shrinks"),
    ("FILLS", "Sends and confirms orders, tracks slippage and partials"),
    ("RISK", "Final veto: −4% on the day goes FLAT, exposure, daily loss, spread, kill switch"),
]
OFF_REASONS = {"STOCKS": "No Alpaca/QuantGPT here: this seat stays off.",
               "JUDGE": "No JEV_API_KEY: Jev is not deciding."}
MAX_CURVE_POINTS = 400


def available_modes(settings: Settings) -> list[str]:
    modes = [m for m, f in MODE_DB.items() if (settings.data_dir / f).exists()]
    if settings.mode in MODE_DB and settings.mode not in modes:
        modes.append(settings.mode)
    return modes or [settings.mode]


def db_for(settings: Settings, mode: str) -> Path:
    if mode not in MODE_DB:
        raise ValueError(f"invalid environment: {mode}")
    return settings.data_dir / MODE_DB[mode]


def clean(obj):
    """JSON não aceita inf/nan."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else round(obj, 6)
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def downsample(points: list, n: int = MAX_CURVE_POINTS) -> list:
    if len(points) <= n:
        return points
    step = len(points) / n
    out = [points[int(i * step)] for i in range(n)]
    out[-1] = points[-1]
    return out


def _prices(storage: Storage, symbols) -> dict[str, dict]:
    out = {}
    for sym in symbols:
        q = storage.get_json(f"quote:{sym}") or {}
        f = storage.get_json(f"features:{sym}") or {}
        price = (q["bid"] + q["ask"]) / 2 if q.get("bid") and q.get("ask") else f.get("close")
        out[sym] = {"symbol": sym, "price": price, "bid": q.get("bid"), "ask": q.get("ask"),
                    "spread_pct": q.get("spread_pct"), "quote_ts": q.get("ts"), "candle_ts": f.get("ts"),
                    "close": f.get("close"), "volume": f.get("volume"), "rel_volume": f.get("rel_volume"),
                    "rsi": f.get("rsi"), "atr_pct": f.get("atr_pct"), "momentum_pct": f.get("momentum_pct"),
                    "regime": f.get("regime")}
    return out


def _book_state(storage: Storage, name: str, capital: float, prices: dict[str, float], now_ms: int,
                meta: dict | None = None) -> dict:
    b = storage.load_book(name, capital)
    equity = b.equity(prices)
    curve = storage.equity_curve(name)
    values = [e for _, e in curve] + [equity]
    perf = performance(b.trades, b.initial_capital, values)
    peak = max(values) if values else equity
    positions = []
    for p in b.positions.values():
        px = prices.get(p.symbol, p.entry_price)
        positions.append({"book": name, "symbol": p.symbol, "label": _label(meta, p.symbol), "qty": p.qty,
                          "entry_price": p.entry_price, "side": p.side,
                          "price": px, "cost": p.cost_brl, "value": p.value(px),
                          "pnl": p.unrealized(px) - p.entry_fee,
                          "pnl_pct": p.pnl_pct(px), "stop": p.stop_price,
                          "target": p.take_profit_price, "entry_ts": p.entry_ts})
    today = utc_day(now_ms)
    return {
        "name": name, "initial": b.initial_capital, "equity": equity, "cash": b.cash, "exposure": b.exposure(),
        "realized": b.realized_total(), "realized_today": b.realized_on(today), "unrealized": b.unrealized(prices),
        "return_pct": (equity / b.initial_capital - 1) * 100 if b.initial_capital else 0.0,
        "drawdown_pct": (peak - equity) / peak * 100 if peak > 0 else 0.0,
        "max_drawdown_pct": max_drawdown(values)[1],
        "performance": asdict(perf), "positions": positions,
        "curve": downsample([[ts, e] for ts, e in curve] + [[now_ms, equity]]),
    }


def _label(meta: dict | None, symbol: str) -> str | None:
    return ((meta or {}).get(symbol) or {}).get("label")


def _memes(storage: Storage, settings: Settings, names: list[str], now_ms: int) -> dict | None:
    """Sleeve de memes: books em US$ simulados, último SCAN, leitura do Grok e escolha do Jev."""
    present = [n for n in MEME_BOOKS if n in names]
    if not settings.meme_sleeve and not present:
        return None
    meta = storage.get_json("memes:meta", {}) or {}
    prices = storage.get_json("memes:prices", {}) or {}
    books = [_book_state(storage, n, settings.meme_bank_usd, prices, now_ms, meta) for n in present]
    trades = []
    for n in present:
        for t in storage.load_book(n, settings.meme_bank_usd).trades:
            trades.append({**asdict(t), "label": _label(meta, t.symbol),
                           "return_pct": t.pnl / (t.qty * t.entry_price) * 100 if t.qty else 0.0})
    trades.sort(key=lambda t: t["exit_ts"], reverse=True)
    num = lambda k: float(storage.get(k, "0") or 0)  # noqa: E731
    beat = int(storage.get("memes:heartbeat", "0") or 0)
    day, today = utc_day(now_ms), ny_day(now_ms)
    return {
        "enabled": settings.meme_sleeve, "books": books, "trades": trades[:200],
        "last_scan": storage.get_json("memes:last_scan"), "heartbeat": beat or None,
        "running": beat > 0 and now_ms - beat < 120_000,
        "flat": [n for n in present if storage.get(f"desk:flat:{n}") == today],
        "fills_today": len(storage.get_json(f"memes:fills:{today}") or []),
        "grok": {"calls_today": num(f"grok_meme_calls:{day}"), "calls_total": num("grok_meme_calls_total"),
                 "usd_total": num("grok_meme_usd_total"), "max_calls_per_day": settings.meme_grok_max_calls_per_day,
                 "enabled": bool(settings.xai_api_key)},
        "jev": {"calls_today": storage.paid_calls_on("memes", day), "calls_total": storage.paid_calls_total("memes"),
                "credits": storage.credits_spent("memes"), "max_calls_per_day": settings.meme_jev_max_calls_per_day,
                "enabled": bool(settings.jev_api_key)},
        "config": {"bank": settings.meme_bank_usd, "nets": list(settings.meme_nets), "tp": settings.meme_take_profit_pct,
                   "sl": settings.meme_stop_pct, "hold_h": settings.meme_max_hold_hours,
                   "max_positions": settings.meme_max_positions},
    }


def _agents(storage: Storage, running: bool, has_jev: bool, stale_ms: int, now_ms: int) -> list[dict]:
    out = []
    for name, desc in AGENTS:
        a = storage.get_json(f"agent:{name}") or {}
        if name == "STOCKS" or (name == "JUDGE" and not has_jev):
            status = "off"
        elif not a:
            status = "idle"
        elif not running:
            status = "stopped"
        elif name == "SCAN" and now_ms - a.get("ts", 0) > stale_ms:
            status = "stale"
        else:
            status = "ok" if a.get("ok", True) else "error"
        memes = storage.get_json(f"agent:{name}:memes")
        out.append({"name": name, "description": desc, "status": status, "off_reason": OFF_REASONS.get(name), **a,
                    "memes": memes})
    return out


def build_state(storage: Storage, settings: Settings, mode: str, kill: bool, now_ms: int | None = None) -> dict:
    now_ms = now_ms or int(time.time() * 1000)
    symbols = settings.symbols_for(mode)
    market = _prices(storage, symbols)
    prices = {s: m["price"] for s, m in market.items() if m["price"]}
    all_names = storage.book_names()
    names = [n for n in all_names if n not in MEME_BOOKS]  # memes: outra moeda e outra escala, aba própria
    order = {"jev": 0, "baseline": 1, "random": 2, "quant": 3}
    names.sort(key=lambda n: order.get(n, 9))
    books = [_book_state(storage, n, settings.capital_brl, prices, now_ms) for n in names]

    heartbeat = int(storage.get("heartbeat", "0") or 0)
    stale_ms = max(3 * settings.poll_seconds, 120) * 1000
    running = heartbeat > 0 and now_ms - heartbeat < stale_ms
    db = storage.db
    trades = []
    for b in names:
        for t in storage.load_book(b, settings.capital_brl).trades:
            trades.append({**asdict(t), "return_pct": t.pnl / (t.qty * t.entry_price) * 100 if t.qty else 0.0})
    trades.sort(key=lambda t: t["exit_ts"], reverse=True)
    meta = storage.get_json("memes:meta", {}) or {}
    orders = [dict(zip(("id", "book", "symbol", "side", "amount", "price", "fee", "status", "note", "simulated", "ts"), r))
              for r in db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100")]
    decisions = [dict(zip(("decider", "symbol", "candle_ts", "action", "confidence", "reason", "credits", "ts"), r))
                 for r in db.execute("SELECT decider, symbol, candle_ts, action, confidence, reason, credits, created_at "
                                     "FROM decisions ORDER BY created_at DESC, candle_ts DESC LIMIT 150")]
    for r in orders + decisions:
        r["label"] = _label(meta, r["symbol"])
    events = [{"ts": ts, "level": lvl, "message": msg} for ts, lvl, msg in storage.recent_events(100)]
    has_jev = "jev" in names
    jev = None
    if has_jev:
        counts = storage.decision_counts("jev")
        jev = {"credits_spent": storage.credits_spent("jev"), "calls_today": storage.paid_calls_on("jev", utc_day(now_ms)),
               "calls_total": storage.paid_calls_total("jev"), "counts": counts,
               "skipped": storage.gated_count("jev"), "trend_gate": settings.jev_trend_gate,
               "max_calls_per_day": settings.jev_max_calls_per_day, "min_probability": settings.jev_min_probability,
               "prompt_version": settings.jev_prompt_version, "edge_margin": settings.jev_edge_margin,
               "cost_pct": settings.round_trip_cost_pct, "grok_screened": storage.grok_screened_count("jev")}
    grok = None
    if settings.xai_api_key or storage.get("grok_calls_total"):
        num = lambda k: float(storage.get(k, "0") or 0)  # noqa: E731
        grok = {"enabled": bool(settings.xai_api_key), "model": settings.grok_model,
                "calls_today": num(f"grok_calls:{utc_day(now_ms)}"), "calls_total": num("grok_calls_total"),
                "escalated_total": num("grok_escalated_total"), "usd_total": num("grok_usd_total"),
                "max_calls_per_day": settings.grok_max_calls_per_day}
    last_error = next((e for e in events if e["level"] == "ERROR"), None)
    today = ny_day(now_ms)
    dial = storage.get_json("desk:dial")
    desk = {"rules": settings.desk_rules, "quant_book": settings.quant_book, "today": today, "dial": dial,
            "dial_valid": is_valid(dial, now_ms), "proof": storage.get_json("desk:proof"),
            "flat": [n for n in names if storage.get(f"desk:flat:{n}") == today],
            "report": storage.get("desk:last_report"), "report_day": storage.get("desk:report_day"),
            "off": sleeves_off(settings), "next": next_times(now_ms / 1000), "memes_on": settings.meme_sleeve,
            "fills_today": len(storage.get_json(f"desk:fills:{today}") or []),
            "blocked_today": db.execute("SELECT COUNT(*) FROM decisions WHERE reason LIKE 'mesa:%' AND created_at>=?",
                                        (day_start_ms(today),)).fetchone()[0]}

    return clean({
        "now": now_ms, "disclaimer": DISCLAIMER, "mode": mode, "modes": available_modes(settings),
        "exchange": settings.exchange, "symbols": list(symbols), "quote": symbols[0].split("/")[1] if symbols else "BRL", "timeframe": settings.timeframe,
        "system": {"running": running, "heartbeat": heartbeat or None, "paused": storage.is_paused(), "kill": kill,
                   "circuit_open": storage.get("circuit_open", "0") == "1", "last_error": last_error},
        "limits": {"capital": settings.capital_brl, "entry": settings.entry_brl, "max_exposure": settings.max_exposure_brl,
                   "daily_loss": settings.daily_loss_limit_brl, "stop_atr": settings.stop_atr_mult,
                   "target_atr": settings.take_profit_atr_mult, "max_hold_candles": settings.max_hold_candles,
                   "fee_rate": settings.fee_rate, "slippage_bps": settings.slippage_bps,
                   "max_positions_per_symbol": settings.max_positions_per_symbol},
        "market": list(market.values()), "books": books, "trades": trades[:300], "orders": orders,
        "decisions": decisions, "events": events, "agents": _agents(storage, running, has_jev, stale_ms, now_ms),
        "jev": jev, "grok": grok, "desk": desk, "memes": _memes(storage, settings, all_names, now_ms),
    })

