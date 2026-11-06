import pytest

from rafs.quant import atr, compute_features, ema, rsi
from tests.conftest import make_candles, trend


def test_ema_constant_series_is_constant():
    assert ema([5.0] * 10, 3) == pytest.approx([5.0] * 10)


def test_ema_known_values():
    # k = 0.5 para período 3
    assert ema([1, 2, 3], 3) == pytest.approx([1, 1.5, 2.25])


def test_rsi_extremes():
    assert rsi([float(i) for i in range(1, 30)]) == 100.0
    assert rsi([float(i) for i in range(30, 1, -1)]) == pytest.approx(0.0)


def test_rsi_needs_enough_data():
    with pytest.raises(ValueError):
        rsi([1.0] * 5)


def test_atr_constant_range():
    candles = make_candles([100.0] * 30, spread=0.01)
    assert atr(candles) == pytest.approx(2.0, rel=1e-6)  # 101 - 99


def test_features_require_minimum_history():
    with pytest.raises(ValueError):
        compute_features("SOL/BRL", make_candles(trend(30)))


def test_features_uptrend_regime():
    f = compute_features("SOL/BRL", make_candles(trend(120)))
    assert f.ema_fast > f.ema_slow
    assert f.momentum_pct > 0
    assert f.regime == "trend_up"

