"""CRAWLER — dados públicos via CCXT (sem chave de API, sem permissão de trade)."""

from __future__ import annotations

import time

import ccxt

from ..models import Candle
from .base import MarketDataProvider, Quote, TradingRules
from .ccxt_futures import make_futures_exchange, perp


class DataQualityError(RuntimeError):
    pass


def timeframe_ms(timeframe: str) -> int:
    return int(ccxt.Exchange.parse_timeframe(timeframe) * 1000)


def native_timeframe(timeframe: str, supported) -> tuple[str, int]:
    """(timeframe nativo, quantos candles nativos formam um pedido). A Binance não tem 2m: vira 2 × 1m."""
    if not supported or timeframe in supported:
        return timeframe, 1
    want = timeframe_ms(timeframe)
    bases = []
    for tf in supported:
        try:
            bases.append((timeframe_ms(tf), tf))
        except Exception:  # noqa: BLE001 — chave estranha na lista da corretora
            continue
    for ms, tf in sorted(bases, reverse=True):
        if ms < want and want % ms == 0:
            return tf, want // ms
    raise ValueError(f"timeframe {timeframe} indisponível e sem base para montar")


def aggregate_candles(candles: list[Candle], tf_ms: int, per: int) -> list[Candle]:
    """Junta candles nativos em candles de `tf_ms` alinhados ao relógio. Grupo incompleto é descartado."""
    if per == 1:
        return candles
    groups: dict[int, list[Candle]] = {}
    for c in sorted({c.ts: c for c in candles}.values(), key=lambda c: c.ts):
        groups.setdefault(c.ts - c.ts % tf_ms, []).append(c)
    out = []
    for start, g in sorted(groups.items()):
        if len(g) == per:
            out.append(Candle(start, g[0].open, max(c.high for c in g), min(c.low for c in g), g[-1].close,
                              sum(c.volume for c in g)))
    return out


def validate_candles(candles: list[Candle], tf_ms: int, now_ms: int | None = None) -> list[Candle]:
    """Remove duplicados e bloqueia dados inválidos, com buracos ou desatualizados."""
    if not candles:
        raise DataQualityError("nenhum candle recebido")
    dedup: dict[int, Candle] = {c.ts: c for c in candles}
    ordered = [dedup[ts] for ts in sorted(dedup)]
    for c in ordered:
        if min(c.open, c.high, c.low, c.close) <= 0 or c.volume < 0:
            raise DataQualityError(f"candle com valores inválidos em {c.ts}")
        if c.high < max(c.open, c.close) or c.low > min(c.open, c.close):
            raise DataQualityError(f"candle inconsistente (high/low) em {c.ts}")
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.ts - prev.ts != tf_ms:
            raise DataQualityError(f"buraco nos dados entre {prev.ts} e {cur.ts}")
    if now_ms is not None:
        # último candle fechado deve ter aberto há no máximo 2 timeframes
        if now_ms - ordered[-1].ts > 2 * tf_ms + 60_000:
            raise DataQualityError("dados desatualizados")
    return ordered


CLOSE_GUARD_MS = 5_000  # relógio local pode estar alguns segundos à frente da corretora


class CCXTMarketData(MarketDataProvider):
    def __init__(self, exchange_id: str = "binance", sandbox: bool = False, demo_futures: bool = False,
                 real_candles: bool = False):
        """`demo_futures`: perpétuos USDT-M da demo da Binance (os mesmos preços em que as ordens do FUTURES executam).
        `real_candles`: com a demo, os candles vêm dos perpétuos reais (só leitura pública). O preço da demo segue o
        real (~3 bps), mas o volume da demo é de robôs de teste e torna o volume relativo inútil em candle curto."""
        if exchange_id not in ccxt.exchanges:
            raise ValueError(f"exchange desconhecida no CCXT: {exchange_id}")
        self.futures = demo_futures
        if demo_futures:
            self.exchange = make_futures_exchange()  # sem chave: só leitura pública
        else:
            self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "timeout": 15000})
            if sandbox:
                self.exchange.set_sandbox_mode(True)
        self.real = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 15000}) if demo_futures and real_candles else None
        self._markets: dict | None = None

    @property
    def candles_ex(self):
        return self.real or self.exchange  # real: perpétuos reais, sem chave

    def _sym(self, symbol: str) -> str:
        return perp(symbol) if self.futures else symbol

    @property
    def name(self) -> str:
        return self.exchange.id

    def _load(self) -> dict:
        if self._markets is None:
            self._markets = self.exchange.load_markets()
        return self._markets

    def _native(self, timeframe: str) -> tuple[str, int]:
        return native_timeframe(timeframe, getattr(self.candles_ex, "timeframes", None))

    def fetch_closed_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        tf = timeframe_ms(timeframe)
        base, per = self._native(timeframe)
        raw = self.candles_ex.fetch_ohlcv(self._sym(symbol), base, limit=(limit + 1) * per + per)
        now = self.candles_ex.milliseconds()
        candles = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)) for r in raw]
        candles = aggregate_candles(candles, tf, per)
        closed = [c for c in candles if c.ts + tf + CLOSE_GUARD_MS <= now]  # descarta o candle ainda aberto
        return validate_candles(closed[-limit:], tf, now)

    def fetch_history(self, symbol: str, timeframe: str, since_ms: int, until_ms: int | None = None) -> list[Candle]:
        """Histórico paginado para backtest."""
        want = timeframe_ms(timeframe)
        timeframe, per = self._native(timeframe)
        tf = timeframe_ms(timeframe)
        until_ms = until_ms or self.candles_ex.milliseconds()
        out: list[Candle] = []
        cursor = since_ms - since_ms % want  # alinhado: o primeiro candle montado sai completo
        while cursor < until_ms:
            raw = self.candles_ex.fetch_ohlcv(self._sym(symbol), timeframe, since=cursor, limit=1000)
            if not raw:
                break
            for r in raw:
                if r[0] + tf <= until_ms:
                    out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)))
            nxt = int(raw[-1][0]) + tf
            if nxt <= cursor:
                break
            cursor = nxt
            time.sleep(self.candles_ex.rateLimit / 1000)
        dedup = {c.ts: c for c in out}
        return aggregate_candles([dedup[t] for t in sorted(dedup)], want, per)

    def get_quote(self, symbol: str) -> Quote:
        if self.futures:  # o ticker de futuros não traz bid/ask; o book ticker traz
            t = self.exchange.fetch_bids_asks([self._sym(symbol)]).get(self._sym(symbol)) or {}
        else:
            t = self.exchange.fetch_ticker(symbol)
        bid, ask = t.get("bid"), t.get("ask")
        if not bid or not ask or bid <= 0 or ask < bid:
            raise DataQualityError(f"cotação inválida para {symbol}: bid={bid} ask={ask}")
        return Quote(symbol, float(bid), float(ask), int(t.get("timestamp") or self.exchange.milliseconds()))

    def get_trading_rules(self, symbol: str) -> TradingRules:
        m = self._load().get(self._sym(symbol))
        if not m or not m.get("active", True):
            raise ValueError(f"mercado indisponível: {symbol}")
        min_cost = float((m.get("limits", {}).get("cost") or {}).get("min") or 0)
        step = float((m.get("precision") or {}).get("amount") or 0)
        return TradingRules(symbol, min_cost, step)
