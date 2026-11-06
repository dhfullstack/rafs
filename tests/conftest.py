import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rafs.config import Settings  # noqa: E402
from rafs.exchanges.base import TradingRules  # noqa: E402
from rafs.models import Candle  # noqa: E402

TF = 15 * 60 * 1000
T0 = 1_790_000_000_000 - (1_790_000_000_000 % TF)


def make_candles(closes, start=T0, spread=0.002, volume=10.0):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * (1 + spread)
        lo = min(o, c) * (1 - spread)
        out.append(Candle(start + i * TF, o, hi, lo, c, volume))
        prev = c
    return out


def trend(n, start=100.0, step=0.003, wiggle=0.004):
    return [start * (1 + step) ** i * (1 + wiggle * math.sin(i)) for i in range(n)]


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, symbols=("SOL/BRL",))


@pytest.fixture
def rules():
    return lambda symbol: TradingRules(symbol, min_cost=10.0, amount_step=0.001)
