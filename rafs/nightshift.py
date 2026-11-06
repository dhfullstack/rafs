"""NIGHT SHIFT — o teste do guia antes de confiar no dial (só leitura: não opera, não grava nada).

Para cada dia do histórico, recalcula o dial das 06:00 de Nova York só com o que já estava publicado naquele dia
(sem olhar o futuro) e mede o que aconteceu depois:
  1. por faixa do dial: retorno de 5 dias de BTC (crypto) e SOL (meme), líquido de 10 bps por lado, pior ponto e
     variação de volume;
  2. varredura do corte (0.25 a 0.75): operar só com dial ≥ corte ajuda ou atrapalha?
  3. a regra do sleeve crypto inteira (rotação 12-1 top 2 × dial, executada às 20:05) contra comprar e segurar BTC:
     CAGR, Sharpe, pior queda, nos primeiros 70% e nos últimos 30% do período (o pedaço que a regra nunca viu).
Veredito: DATA VALIDATED, TRADE-OFF ou NO EDGE. Resultado de simulação, não promessa de lucro.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time as dtime, timedelta, timezone
from statistics import mean, pstdev

from .dial import CRYPTO, FRED_IDS, MOMENTUM_LONG_DAYS, NY, STAND_DOWN_BELOW, compute_dial
from .fred import Series

COST_BPS = 10.0  # por lado
HORIZON = 5  # dias
THRESHOLDS = (0.25, 0.33, 0.5, 0.66, 0.75)
SPLIT = 0.7
TRIM_DAYS = MOMENTUM_LONG_DAYS + 90  # o dial nunca olha mais para trás que isso

Daily = dict[str, list[tuple[date, float, float]]]  # base → (dia UTC de abertura do candle, fechamento, volume)


def fetch(fred, market_data, now_ms: int, years: int = 8) -> tuple[dict[str, Series], Daily]:
    fred_data = {sid: fred.series(sid) for sid in FRED_IDS}
    since = now_ms - years * 365 * 86_400_000
    daily: Daily = {}
    for base in CRYPTO:
        candles = market_data.fetch_history(f"{base}/USDT", "1d", since, now_ms)
        daily[base] = [(datetime.fromtimestamp(c.ts / 1000, timezone.utc).date(), c.close, c.volume)
                       for c in candles]
    return fred_data, daily


def window(fred: dict[str, Series], daily: Daily) -> tuple[date, date]:
    """Do primeiro dia em que todas as séries existem (+ histórico para 4 semanas e 12 meses) ao último candle."""
    start = max([fred[s][0][0] + timedelta(days=35) for s in FRED_IDS]
                + [daily[b][0][0] + timedelta(days=MOMENTUM_LONG_DAYS + 15) for b in CRYPTO])
    end = min(daily[b][-1][0] for b in CRYPTO)
    return start, end


def dial_history(fred: dict[str, Series], daily: Daily, start: date, end: date) -> list[dict]:
    closes = {b: [(d, c) for d, c, _ in daily[b]] for b in CRYPTO}
    lo = start - timedelta(days=TRIM_DAYS)
    fred = {k: [x for x in v if x[0] >= lo - timedelta(days=60)] for k, v in fred.items()}
    closes = {k: [x for x in v if x[0] >= lo] for k, v in closes.items()}
    out = []
    day = start
    while day <= end:
        d = compute_dial(fred, closes, datetime.combine(day, dtime(6, 0), NY), history=True)
        out.append({"day": day, "crypto": d["dial"]["crypto"], "meme": d["dial"]["meme"],
                    "targets": d["crypto_targets"], "degraded": d["degraded"]})
        day += timedelta(days=1)
    return out


def forward(daily: Daily, base: str) -> dict[date, tuple[float, float, float]]:
    """dia → (retorno 5d líquido, pior ponto em 5d, variação de volume 5d vs 5d anteriores)."""
    rows = daily[base]
    out = {}
    for i in range(5, len(rows) - HORIZON):
        d0, c0, _ = rows[i]
        nxt = rows[i + 1:i + 1 + HORIZON]
        ret = nxt[-1][1] / c0 - 1 - 2 * COST_BPS / 10_000
        worst = min(c / c0 - 1 for _, c, _ in nxt)
        before = mean(v for _, _, v in rows[i - 4:i + 1])
        vol = mean(v for _, _, v in nxt) / before - 1 if before else 0.0
        out[d0] = (ret, worst, vol)
    return out


def buckets(hist: list[dict], key: str, fwd: dict[date, tuple[float, float, float]]) -> list[tuple]:
    by: dict[float, list] = {}
    for h in hist:
        if h["day"] in fwd:
            by.setdefault(h[key], []).append(fwd[h["day"]])
    return [(level, len(v), mean(r for r, _, _ in v), sum(r > 0 for r, _, _ in v) / len(v),
             mean(w for _, w, _ in v), mean(x for _, _, x in v)) for level, v in sorted(by.items())]


def sweep(hist: list[dict], key: str, fwd: dict) -> list[tuple]:
    out = []
    for t in THRESHOLDS:
        on = [fwd[h["day"]][0] for h in hist if h["day"] in fwd and h[key] >= t]
        off = [fwd[h["day"]][0] for h in hist if h["day"] in fwd and h[key] < t]
        out.append((t, len(on), mean(on) if on else None, len(off), mean(off) if off else None))
    return out


def spread(hist: list[dict], key: str, fwd: dict) -> float | None:
    on = [fwd[h["day"]][0] for h in hist if h["day"] in fwd and h[key] >= STAND_DOWN_BELOW]
    off = [fwd[h["day"]][0] for h in hist if h["day"] in fwd and h[key] < STAND_DOWN_BELOW]
    return mean(on) - mean(off) if on and off else None


def rotation(hist: list[dict], daily: Daily) -> tuple[list[tuple[date, float]], int, float]:
    """Regra do sleeve crypto, como a Desk roda às 20:05: vende o que saiu dos alvos ou tudo se dial < 0.50;
    compra alvo novo com patrimônio × dial × peso; o que continua alvo não é reajustado."""
    px = {b: {d: c for d, c, _ in daily[b]} for b in CRYPTO}
    cash, hold = 1.0, {}
    curve, trades, invested_days = [], 0, 0
    fee = COST_BPS / 10_000
    prev_day = None
    for h in hist:
        day = h["day"]
        if any(day not in px[b] for b in CRYPTO):
            continue
        if prev_day is not None:
            for b in hold:
                hold[b] *= px[b][day] / px[b][prev_day]
        prev_day = day
        equity = cash + sum(hold.values())
        crypto, targets = h["crypto"], h["targets"]
        for b in list(hold):
            if crypto < STAND_DOWN_BELOW or b not in targets:
                cash += hold.pop(b) * (1 - fee)
                trades += 1
        if crypto >= STAND_DOWN_BELOW:
            for b, w in targets.items():
                if b not in hold:
                    amount = min(cash, equity * crypto * w)
                    if amount > 0:
                        cash -= amount
                        hold[b] = amount * (1 - fee)
                        trades += 1
        invested_days += bool(hold)
        curve.append((day, cash + sum(hold.values())))
    return curve, trades, invested_days / len(curve) if curve else 0.0


def buy_and_hold(hist: list[dict], daily: Daily, base: str = "BTC") -> list[tuple[date, float]]:
    px = {d: c for d, c, _ in daily[base]}
    days = [h["day"] for h in hist if h["day"] in px]
    return [(d, px[d] / px[days[0]]) for d in days] if days else []


def stats(curve: list[tuple[date, float]]) -> dict:
    if len(curve) < 3:
        return {"days": len(curve), "ret": None, "cagr": None, "sharpe": None, "max_dd": None}
    eq = [v for _, v in curve]
    rets = [b / a - 1 for a, b in zip(eq, eq[1:]) if a]
    years = (curve[-1][0] - curve[0][0]).days / 365
    peak, max_dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    sd = pstdev(rets)
    return {"days": len(curve), "ret": eq[-1] / eq[0] - 1,
            "cagr": (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else None,
            "sharpe": mean(rets) / sd * math.sqrt(365) if sd else None, "max_dd": max_dd}


def split(curve: list[tuple[date, float]]) -> tuple[list, list]:
    cut = int(len(curve) * SPLIT)
    return curve[:cut + 1], curve[cut:]


def verdict(dial_spreads: tuple[float | None, float | None], rot_late: dict, btc_late: dict) -> tuple[str, str]:
    a, b = dial_spreads
    dial_ok = a is not None and b is not None and a > 0 and b > 0
    better_dd = (rot_late["max_dd"] or -1) > (btc_late["max_dd"] or -1)
    better_sharpe = (rot_late["sharpe"] or -9) >= (btc_late["sharpe"] or -9)
    if dial_ok and better_sharpe:
        return "DATA VALIDATED", "dial alto foi melhor que dial baixo nos dois pedaços e a regra bateu o BTC parado no pedaço novo"
    if dial_ok and better_dd:
        return "TRADE-OFF", "o dial separa dias bons de ruins e corta a queda, mas rende menos que segurar BTC"
    if better_dd and not dial_ok:
        return "TRADE-OFF", "a regra corta a queda, mas o dial não separou dias bons de ruins nos dois pedaços"
    return "NO EDGE", "o dial não separou dias bons de ruins de forma estável; não confiar no número"


def pct(x: float | None, d: int = 2) -> str:
    return "  n/a" if x is None else f"{x * 100:+.{d}f}%"


def run(fred: dict[str, Series], daily: Daily) -> str:
    start, end = window(fred, daily)
    if start >= end:
        return "NIGHT SHIFT: histórico insuficiente para testar o dial."
    hist = dial_history(fred, daily, start, end)
    btc_fwd, sol_fwd = forward(daily, "BTC"), forward(daily, "SOL")
    first, second = hist[:int(len(hist) * SPLIT)], hist[int(len(hist) * SPLIT):]
    lines = [f"NIGHT SHIFT · {start} → {end} ({len(hist)} dias) · dial das 06:00 NY só com dados já publicados",
             f"custos {COST_BPS:g} bps por lado · retornos de {HORIZON} dias · SIMULAÇÃO, não é lucro",
             "janela limitada pela série de crédito HY do FRED (BAMLH0A0HYM2), que o FRED só serve de 2023-10 em diante"]
    for title, key, fwd, base in (("crypto", "crypto", btc_fwd, "BTC"), ("meme", "meme", sol_fwd, "SOL")):
        lines += ["", f"dial {title} → {base} nos {HORIZON} dias seguintes",
                  "  dial     dias   retorno   acerto   pior ponto   volume"]
        for level, n, r, hit, worst, vol in buckets(hist, key, fwd):
            lines.append(f"  {level:4.2f}  {n:6d}  {pct(r)}  {hit * 100:6.1f}%  {pct(worst):>10}  {pct(vol, 1):>7}")
        lines.append("  corte   dias ≥   retorno   dias <   retorno")
        for t, n_on, r_on, n_off, r_off in sweep(hist, key, fwd):
            lines.append(f"  {t:4.2f}  {n_on:7d}  {pct(r_on)}  {n_off:7d}  {pct(r_off)}")
    spreads = (spread(first, "crypto", btc_fwd), spread(second, "crypto", btc_fwd))
    lines += ["", f"dial crypto ≥ 0.50 menos < 0.50 (BTC {HORIZON}d): primeiros 70% {pct(spreads[0])} · "
                  f"últimos 30% {pct(spreads[1])}"]
    curve, trades, exposure = rotation(hist, daily)
    btc = buy_and_hold(hist, daily)
    lines += ["", "sleeve crypto do guia (12-1 top 2 × dial, 20:05 NY) vs comprar e segurar BTC",
              "                      retorno     CAGR   Sharpe   pior queda"]
    late = {}
    for label, c in (("regra", curve), ("BTC parado", btc)):
        for part, cc in (("tudo", c), ("70%", split(c)[0]), ("30%", split(c)[1])):
            st = stats(cc)
            if part == "30%":
                late[label] = st
            sharpe = "  n/a" if st["sharpe"] is None else f"{st['sharpe']:5.2f}"
            lines.append(f"  {label:<10} {part:>4}  {pct(st['ret'], 1):>9} {pct(st['cagr'], 1):>8}  {sharpe}  "
                         f"{pct(st['max_dd'], 1):>9}")
    lines.append(f"  regra: {trades} ordens, investida em {exposure * 100:.0f}% dos dias")
    name, why = verdict(spreads, late["regra"], late["BTC parado"])
    lines += ["", f"VEREDITO: {name} — {why}."]
    return "\n".join(lines)
