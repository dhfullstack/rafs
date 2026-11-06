"""Séries do FRED (St. Louis Fed) pelo CSV público — sem chave, só leitura."""

from __future__ import annotations

import re
from datetime import date

import httpx

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES_RE = re.compile(r"^[A-Z0-9]{2,20}$")

Series = list[tuple[date, float]]


def parse_csv(text: str) -> Series:
    """`observation_date,ID` por linha; dias sem valor vêm como "." ou vazio e são ignorados."""
    out: Series = []
    for line in text.strip().splitlines()[1:]:
        day, _, value = line.partition(",")
        try:
            out.append((date.fromisoformat(day.strip()), float(value)))
        except ValueError:
            continue
    return out


class Fred:
    def __init__(self, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)

    def series(self, series_id: str) -> Series:
        if not SERIES_RE.match(series_id):
            raise ValueError(f"série FRED inválida: {series_id!r}")
        r = self._http.get(CSV_URL, params={"id": series_id})
        r.raise_for_status()
        data = parse_csv(r.text)
        if not data:
            raise ValueError(f"FRED {series_id}: nenhuma observação")
        return data
