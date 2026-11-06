"""QUANT — indicadores determinísticos em Python puro (sem look-ahead: usam só candles fechados)."""

from __future__ import annotations

import math
from statistics import fmean, pstdev

from .models import Candle, Features

MIN_CANDLES = 60


def ema(values: list[float], period: int) -> list[float]:
    if period <= 0 or not values:
        raise ValueError("período e valores devem ser não vazios")
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    """RSI de Wilder do último candle."""
    if len(closes) < period + 1:
        raise ValueError("candles insuficientes para RSI")
    gains, losses = [], []
    for prev, cur in zip(closes, closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: list[Candle], period: int = 14) -> float:
    """ATR de Wilder do último candle."""
    if len(candles) < period + 1:
        raise ValueError("candles insuficientes para ATR")
    trs = []
    for prev, cur in zip(candles, candles[1:]):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    value = fmean(trs[:period])
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def vwap(candles: list[Candle]) -> float:
    vol = sum(c.volume for c in candles)
    if vol <= 0:
        return candles[-1].close
    return sum((c.high + c.low + c.close) / 3.0 * c.volume for c in candles) / vol


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]


def compute_features(symbol: str, candles: list[Candle]) -> Features:
    """Calcula o snapshot de features a partir de candles FECHADOS, do mais antigo ao mais novo."""
    if len(candles) < MIN_CANDLES:
        raise ValueError(f"precisa de >= {MIN_CANDLES} candles, recebeu {len(candles)}")
    closes = [c.close for c in candles]
    last = candles[-1]
    ema_fast = ema(closes, 12)[-1]
    ema_slow = ema(closes, 26)[-1]
    atr_v = atr(candles, 14)
    window = candles[-48:]
    vw = vwap(window)
    rets = log_returns(closes[-49:])
    vol_pct = pstdev(rets) * 100 if len(rets) > 1 else 0.0
    momentum = (closes[-1] / closes[-13] - 1) * 100
    avg_vol = fmean(c.volume for c in candles[-21:-1])
    rel_vol = last.volume / avg_vol if avg_vol > 0 else 0.0

    day = candles[-96:]
    sma_day = fmean(c.close for c in day)
    hi, lo = max(c.high for c in day), min(c.low for c in day)
    back = closes[-17] if len(closes) >= 17 else closes[0]

    spread_pct = (ema_fast - ema_slow) / ema_slow * 100
    if spread_pct > 0.15 and momentum > 0:
        regime = "trend_up"
    elif spread_pct < -0.15 and momentum < 0:
        regime = "trend_down"
    else:
        regime = "range"

    return Features(
        symbol=symbol,
        ts=last.ts,
        close=last.close,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi=rsi(closes, 14),
        atr=atr_v,
        atr_pct=atr_v / last.close * 100,
        vwap_dev_pct=(last.close / vw - 1) * 100,
        momentum_pct=momentum,
        volatility_pct=vol_pct,
        rel_volume=rel_vol,
        regime=regime,
        trend24h_pct=(last.close / sma_day - 1) * 100,
        mom4h_pct=(last.close / back - 1) * 100,
        range24h_pos=(last.close - lo) / (hi - lo) if hi > lo else 0.5,
    )
