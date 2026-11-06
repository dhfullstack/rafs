"""QUANT — o dial de risco da mesa (guia "The Hedge Fund Terminal", cadeira 2).

Toda manhã publica um número por sleeve: o quanto a mesa pode empurrar hoje. Nunca opera, nunca escolhe ativo,
nunca dimensiona posição. Cada número sai de dados baixados hoje; série atrasada além do normal é DEGRADED e
conta como vermelho, nunca verde. O dial nunca é arredondado para cima.

Fontes (no guia vêm do QuantGPT; aqui, das mesmas séries públicas):
- FRED (CSV público, sem chave): WALCL, WTREGEN, RRPONTSYD, VIXCLS, BAMLH0A0HYM2 e NASDAQ100
  (o índice Nasdaq-100 no lugar do QQQ, que o replica).
- Binance spot pública: fechamentos diários de BTC, ETH e SOL em USDT.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .fred import Series

NY = ZoneInfo("America/New_York")
FRED_IDS = ("WALCL", "WTREGEN", "RRPONTSYD", "VIXCLS", "BAMLH0A0HYM2", "NASDAQ100")
CRYPTO = ("BTC", "ETH", "SOL")
# idade máxima aceita (atraso normal de publicação + fim de semana); acima disso a série é DEGRADED
MAX_AGE_HOURS = {"WALCL": 240, "WTREGEN": 240, "RRPONTSYD": 120, "VIXCLS": 120, "BAMLH0A0HYM2": 120,
                 "NASDAQ100": 120, "BTC": 48, "ETH": 48, "SOL": 48}
# só no histórico (turno da noite): dias até a observação ficar pública, para não olhar o futuro
PUBLISH_LAG_DAYS = {"WALCL": 2, "WTREGEN": 2, "RRPONTSYD": 1, "VIXCLS": 2, "BAMLH0A0HYM2": 2, "NASDAQ100": 2}
VIX_GREEN_BELOW = 22.0
CHANGE_DAYS = 28  # "4 week change"
STAND_DOWN_BELOW = 0.5
SLEEVES = {
    "meme": ("liquidity", "volatility", "btc", "sol"),
    "stocks": ("liquidity", "volatility", "credit", "nasdaq"),
    "crypto": ("liquidity", "credit", "btc"),
}
MOMENTUM_LONG_DAYS, MOMENTUM_SKIP_DAYS, MOMENTUM_TOP = 365, 30, 2  # Crypto 12-1 Month Momentum


def value_at(series: Series, day: date) -> float | None:
    """Último valor publicado até `day` (inclusive)."""
    i = bisect_right(series, day, key=lambda x: x[0])
    return series[i - 1][1] if i else None


def change_over(series: Series, days: int = CHANGE_DAYS) -> float | None:
    if not series:
        return None
    last_day, last = series[-1]
    then = value_at(series, last_day - timedelta(days=days))
    return None if then is None else last - then


def net_liquidity(walcl: Series, wtregen: Series, rrp: Series) -> Series:
    """WALCL − WTREGEN − RRPONTSYD em bilhões (WALCL e WTREGEN vêm em milhões; RRP já em bilhões)."""
    out: Series = []
    for day, w in walcl:
        t, r = value_at(wtregen, day), value_at(rrp, day)
        if t is not None and r is not None:
            out.append((day, w / 1000 - t / 1000 - r))
    return out


def vs_average(series: Series, n: int) -> float | None:
    if len(series) < n:
        return None
    avg = sum(v for _, v in series[-n:]) / n
    return series[-1][1] / avg - 1


def floor2(x: float) -> float:
    """NEVER round a dial up. 0.67 is not 0.75 — e 2/3 vira 0.66."""
    return math.floor(x * 100 + 1e-9) / 100


def momentum_12_1(closes: Series) -> float | None:
    if not closes:
        return None
    last_day = closes[-1][0]
    if closes[0][0] > last_day - timedelta(days=MOMENTUM_LONG_DAYS):
        return None  # sem 12 meses de história
    long_ago = value_at(closes, last_day - timedelta(days=MOMENTUM_LONG_DAYS))
    recent = value_at(closes, last_day - timedelta(days=MOMENTUM_SKIP_DAYS))
    return None if not long_ago or recent is None else recent / long_ago - 1


def crypto_targets(scores: dict[str, float | None]) -> dict[str, float]:
    """Top 2 com score positivo, peso igual. Nenhum positivo: caixa."""
    ranked = sorted(((s, b) for b, s in scores.items() if s is not None and s > 0), reverse=True)[:MOMENTUM_TOP]
    return {b: round(1 / len(ranked), 4) for _, b in ranked}


def compute_dial(fred: dict[str, Series], daily: dict[str, Series], asof: datetime, history: bool = False) -> dict:
    """`fred`/`daily`: séries (data, valor); `daily` usa a data de abertura do candle diário (UTC).

    `history=True` (turno da noite) corta cada série no que já estava publicado em `asof`.
    """
    asof_utc = asof.astimezone(timezone.utc)
    ny_day = asof.astimezone(NY).date()

    def cut(series: Series | None, limit: date) -> Series:
        return series[:bisect_right(series, limit, key=lambda x: x[0])] if series else []

    data: dict[str, Series] = {}
    ages: dict[str, float | None] = {}
    for sid in FRED_IDS:
        limit = ny_day - timedelta(days=PUBLISH_LAG_DAYS[sid]) if history else asof_utc.date()
        data[sid] = cut(fred.get(sid), limit)
        ages[sid] = ((asof_utc - datetime.combine(data[sid][-1][0], datetime.min.time(), timezone.utc))
                     .total_seconds() / 3600) if data[sid] else None
    for base in CRYPTO:
        data[base] = cut(daily.get(base), asof_utc.date() - timedelta(days=1))  # só candles diários fechados
        closed_at = (datetime.combine(data[base][-1][0], datetime.min.time(), timezone.utc) + timedelta(days=1)
                     if data[base] else None)
        ages[base] = (asof_utc - closed_at).total_seconds() / 3600 if closed_at else None
    degraded = sorted(k for k, age in ages.items() if age is None or age > MAX_AGE_HOURS[k])
    fresh = lambda *keys: not any(k in degraded for k in keys)  # noqa: E731

    net_liq_4w = change_over(net_liquidity(data["WALCL"], data["WTREGEN"], data["RRPONTSYD"]))
    vix = data["VIXCLS"][-1][1] if data["VIXCLS"] else None
    hy_4w = change_over(data["BAMLH0A0HYM2"])
    btc = vs_average(data["BTC"], 200)
    sol = vs_average(data["SOL"], 50)
    qqq = vs_average(data["NASDAQ100"], 200)
    greens = {
        "liquidity": fresh("WALCL", "WTREGEN", "RRPONTSYD") and net_liq_4w is not None and net_liq_4w > 0,
        "volatility": fresh("VIXCLS") and vix is not None and vix < VIX_GREEN_BELOW,
        "credit": fresh("BAMLH0A0HYM2") and hy_4w is not None and hy_4w <= 0,
        "btc": fresh("BTC") and btc is not None and btc > 0,
        "sol": fresh("SOL") and sol is not None and sol > 0,
        "nasdaq": fresh("NASDAQ100") and qqq is not None and qqq > 0,
    }
    scores = {b: (momentum_12_1(data[b]) if fresh(b) else None) for b in CRYPTO}
    rnd = lambda v, n=4: None if v is None else round(v, n)  # noqa: E731
    return {
        "date": ny_day.isoformat(),
        "computed_at": int(asof_utc.timestamp() * 1000),
        "inputs": {"net_liq_4w": rnd(net_liq_4w, 1), "vix": rnd(vix, 2), "hy_4w": rnd(hy_4w, 3),
                   "btc_vs_200d": rnd(btc), "sol_vs_50d": rnd(sol), "qqq_vs_200d": rnd(qqq)},
        "greens": greens,
        "dial": {sleeve: floor2(sum(greens[k] for k in keys) / len(keys)) for sleeve, keys in SLEEVES.items()},
        "crypto_targets": crypto_targets(scores),
        "momentum_12_1": {b: rnd(s) for b, s in scores.items()},
        "data_age_hours": {k: (None if a is None else round(a)) for k, a in ages.items()},
        "degraded": degraded,
    }


def fetch_inputs(fred, market_data) -> tuple[dict[str, Series], dict[str, Series], list[str]]:
    """Baixa as séries de hoje. Fonte que falha fica fora (vira DEGRADED → vermelho), nunca inventada."""
    fred_data: dict[str, Series] = {}
    daily: dict[str, Series] = {}
    errors: list[str] = []
    for sid in FRED_IDS:
        try:
            fred_data[sid] = fred.series(sid)
        except Exception as e:  # noqa: BLE001
            errors.append(f"FRED {sid}: {type(e).__name__}")
    for base in CRYPTO:
        try:
            candles = market_data.fetch_closed_candles(f"{base}/USDT", "1d", 400)
            daily[base] = [(datetime.fromtimestamp(c.ts / 1000, timezone.utc).date(), c.close) for c in candles]
        except Exception as e:  # noqa: BLE001
            errors.append(f"Binance {base}/USDT diário: {type(e).__name__}")
    return fred_data, daily, errors


def ny_day(ms: int) -> str:
    """Dia da mesa (calendário de Nova York, como no guia)."""
    return datetime.fromtimestamp(ms / 1000, NY).date().isoformat()


def dial_age_hours(dial: dict | None, now_ms: int) -> float | None:
    return None if not dial else (now_ms - int(dial.get("computed_at", 0))) / 3_600_000


def is_valid(dial: dict | None, now_ms: int) -> bool:
    """A dial older than 24h is no dial."""
    age = dial_age_hours(dial, now_ms)
    return age is not None and 0 <= age < 24
