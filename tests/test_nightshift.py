import math
from datetime import date, timedelta

from rafs import nightshift
from rafs.dial import CRYPTO, FRED_IDS


def days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def test_nightshift_runs_end_to_end_on_synthetic_history():
    end = date(2026, 6, 1)
    level = {"WALCL": 6_600_000.0, "WTREGEN": 800_000.0, "RRPONTSYD": 50.0, "VIXCLS": 18.0,
             "BAMLH0A0HYM2": 3.2, "NASDAQ100": 20_000.0}
    fred = {sid: [(d, level[sid] * (1 + 0.05 * math.sin(i / 40))) for i, d in enumerate(days(date(2024, 6, 1), end))]
            for sid in FRED_IDS}
    daily = {b: [(d, 100.0 * (1 + 0.3 * math.sin(i / 60 + k)) * 1.0005 ** i, 1000.0)
                 for i, d in enumerate(days(date(2023, 6, 1), end))] for k, b in enumerate(CRYPTO)}
    out = nightshift.run(fred, daily)
    assert out.startswith("NIGHT SHIFT ·") and "SIMULAÇÃO" in out
    assert any(v in out for v in ("VEREDITO: DATA VALIDATED", "VEREDITO: TRADE-OFF", "VEREDITO: NO EDGE"))


def test_nightshift_says_so_when_history_is_too_short():
    d0 = date(2026, 5, 1)
    fred = {sid: [(d0, 1.0)] for sid in FRED_IDS}
    daily = {b: [(d0, 1.0, 1.0), (d0 + timedelta(days=1), 1.0, 1.0)] for b in CRYPTO}
    assert "insuficiente" in nightshift.run(fred, daily)
