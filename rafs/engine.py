"""Motor da mesa — SCAN → QUANT → (CHIEF/Grok → JUDGE/Jev) → SIZE → RISK → FILLS. Código determinístico.

O mesmo `process_candle` roda no BACKTEST e no PAPER, então não há divergência de lógica entre os dois.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .dial import ny_day
from .deciders.base import Decider
from .exchanges.base import ExchangeProvider, OrderRequest, TradingRules
from .exchanges.ccxt_data import timeframe_ms
from .exits import exit_scale
from .models import Action, Candle, Decision, Position, Trade
from .portfolio import Book
from .quant import compute_features
from .risk import RiskEngine
from .sizing import size_entry
from .storage import Storage

log = logging.getLogger("rafs")
FEATURE_WINDOW = 120
MAX_SLIPPAGE_BPS = 25.0  # acima disso o fill é sinalizado, nunca absorvido


@dataclass(frozen=True)
class FillRefs:
    """Preços de referência para executar decisões tomadas no fechamento do candle."""

    buy: float
    sell: float
    spread_pct: float | None = None


class Engine:
    def __init__(self, settings: Settings, storage: Storage, deciders: list[Decider], exchange: ExchangeProvider,
                 risk: RiskEngine | None = None, notify: Callable[[str], None] | None = None,
                 telemetry: bool = False):
        self.s = settings
        self.storage = storage
        self.deciders = deciders
        self.exchange = exchange
        self.risk = risk or RiskEngine(settings)
        self.notify = notify or (lambda msg: None)
        self.telemetry = telemetry  # ao vivo: grava o estado dos agentes para o painel (desligado no backtest)
        self.tf_ms = timeframe_ms(settings.timeframe)
        self.books: dict[str, Book] = {d.name: storage.load_book(d.name, settings.capital_brl) for d in deciders}
        self.last_prices: dict[str, float] = {}
        self.desk = None  # Desk (dial, alvos, FLAT, DEGRADED) — ligada só ao vivo
        for book in self.books.values():
            for p in book.positions.values():
                self.last_prices.setdefault(p.symbol, p.entry_price)

    def agent(self, name: str, activity: str, ok: bool = True, latency_ms: float | None = None) -> None:
        if self.telemetry:
            self.storage.set_agent(name, activity, ok, latency_ms)

    # ------------------------------------------------------------------ núcleo
    def process_candle(self, symbol: str, candles: list[Candle], refs: FillRefs) -> None:
        """`candles` = histórico FECHADO até o candle atual (inclusive)."""
        last = candles[-1]
        self.last_prices[symbol] = last.close
        t0 = time.perf_counter()
        features = compute_features(symbol, candles[-FEATURE_WINDOW:])
        self.agent("SCAN", f"{symbol}: RSI {features.rsi:.0f}, ATR {features.atr_pct:.2f}%, {features.regime}",
                   latency_ms=(time.perf_counter() - t0) * 1000)
        if self.telemetry:
            self.storage.set_json(f"features:{symbol}", {
                "ts": features.ts, "close": features.close, "rsi": features.rsi, "atr_pct": features.atr_pct,
                "momentum_pct": features.momentum_pct, "rel_volume": features.rel_volume, "regime": features.regime,
                "volume": last.volume})
        rules = self.exchange.get_trading_rules(symbol)
        entries_blocked = self.risk.kill_switch_active() or self.storage.is_paused()
        if entries_blocked:
            self.agent("RISK", "novas entradas bloqueadas (kill switch ou pausa); saídas seguem ativas")

        for decider in self.deciders:
            if not getattr(decider, "per_candle", True):
                continue  # book da mesa (quant): opera no relógio de NY, sem saída mecânica por candle
            book = self.books[decider.name]
            pos = book.positions.get(symbol)

            if pos is not None and last.ts > pos.entry_ts:
                reason, ref = self._mechanical_exit(pos, last)
                if reason:
                    self._close(book, pos, ref, last.ts, reason)
                    if reason == "stop" and self.s.stop_cooldown_candles > 0:  # evita o vai e volta logo após o stop
                        self.storage.set(f"stop_until:{book.name}:{symbol}",
                                         str(last.ts + (self.s.stop_cooldown_candles + 1) * self.tf_ms))
                    continue

            if pos is None and entries_blocked:
                continue  # não gasta créditos do Jev se não pode entrar
            if self.storage.get_decision(decider.name, symbol, last.ts):
                continue  # candle já processado (restart) — não decide nem cobra de novo
            if pos is None and last.ts < int(self.storage.get(f"stop_until:{book.name}:{symbol}", "0") or 0):
                self.storage.record_decision(Decision(decider.name, symbol, last.ts, Action.HOLD, None,
                                                      "pausa pós-stop: sem nova entrada neste ativo por enquanto"))
                continue  # não gasta Grok/Jev
            if pos is None and self.desk is not None:
                block = self.desk.entry_block(decider.name, symbol)
                if block:  # NO DIAL, NO DESK: nada chega ao SIZE, nem gasta Grok/Jev
                    self.storage.record_decision(Decision(decider.name, symbol, last.ts, Action.HOLD, None,
                                                          f"mesa: {block}"))
                    continue
            t0 = time.perf_counter()
            decision = decider.decide(features, pos)
            if decider.name == "jev":
                grok = decision.raw.get("grok")
                if grok:
                    self.agent("CHIEF", f"Grok leu {symbol}: " + (
                        f"{'escalou ao Jev' if grok['escalate'] else 'não escalou'} (score {grok['score']:.2f}) "
                        f"— {grok['summary']}" if "summary" in grok else f"erro {grok.get('error')}"),
                        ok="error" not in grok, latency_ms=grok.get("latency_ms"))
                if not decision.reason.startswith("grok:"):
                    self.agent("JUDGE", f"{symbol}: {decision.reason}",
                               ok=not decision.reason.startswith(("erro", "desativado")),
                               latency_ms=(time.perf_counter() - t0) * 1000)
            self.storage.record_decision(decision)
            log.info("[%s] %s %s — %s", decider.name, symbol, decision.action.value, decision.reason)

            # long: SELL fecha. short: BUY recompra. sem posição: BUY abre long, SELL abre short (só FUTURES)
            if pos is not None:
                if decision.action is (Action.BUY if pos.is_short else Action.SELL):
                    self._close(book, pos, refs.buy if pos.is_short else refs.sell, last.ts,
                                f"decider ({decision.reason})")
            elif decision.action is Action.BUY or (decision.action is Action.SELL and self.s.allow_short):
                self._try_open(book, symbol, features.atr, features.atr_pct, refs, last.ts, rules,
                               dial=self.desk.size_multiplier() if self.desk is not None else 1.0,
                               side="long" if decision.action is Action.BUY else "short")

        for book in self.books.values():
            self.storage.record_equity(book.name, last.ts, book.equity(self.last_prices))

    def _mechanical_exit(self, pos: Position, c: Candle) -> tuple[str | None, float]:
        """Stop, alvo e tempo — iguais para todos os deciders. Se stop e alvo caem no mesmo candle, assume stop.
        Short espelhado: stop acima da entrada (máxima do candle), alvo abaixo (mínima)."""
        if pos.is_short:
            if pos.stop_price > 0 and c.high >= pos.stop_price:
                return "stop", max(pos.stop_price, c.open)
            if pos.take_profit_price > 0 and c.low <= pos.take_profit_price:
                return "alvo", min(pos.take_profit_price, c.open)
        elif c.low <= pos.stop_price:
            return "stop", min(pos.stop_price, c.open)
        elif pos.take_profit_price > 0 and c.high >= pos.take_profit_price:  # 0 = sem alvo (book da mesa)
            return "alvo", max(pos.take_profit_price, c.open)
        held = (c.ts - pos.entry_ts) // self.tf_ms
        if held >= self.s.max_hold_candles:
            return "tempo", c.close
        return None, 0.0

    def _try_open(self, book: Book, symbol: str, atr: float, atr_pct: float, refs: FillRefs, ts: int,
                  rules: TradingRules, dial: float = 1.0, side: str = "long") -> None:
        ref = refs.sell if side == "short" else refs.buy
        qty, why = size_entry(self.s, book, rules, ref, atr_pct, dial)
        if qty <= 0:
            msg = f"[{book.name}] SIZE rejeitado {symbol}{' short' if side == 'short' else ''}: {why}"
            log.info(msg)
            self.storage.log_event("SIZE", msg)
            self.agent("SIZE", msg)
            return
        self.agent("SIZE", f"[{book.name}] {symbol}{' short' if side == 'short' else ''} {qty:g} ≈ {qty * ref:.2f}"
                   + (f" (× dial {dial:.2f})" if dial < 1 else ""))
        k = exit_scale(atr_pct, self.s.stop_atr_mult, self.s.min_stop_pct)  # piso: candle curto não vira só taxa
        self._execute_open(book, symbol, qty, refs, ts, self.s.stop_atr_mult * atr * k,
                           self.s.take_profit_atr_mult * atr * k, side)

    def open_notional(self, book: Book, symbol: str, notional: float, refs: FillRefs, ts: int,
                      why: str) -> tuple[bool, str]:
        """Entrada da mesa (book quant): valor já decidido; sem stop/alvo por candle (o RISK das 20:05 decide a saída)."""
        rules = self.exchange.get_trading_rules(symbol)
        qty = rules.floor_amount(min(notional, book.cash) / refs.buy)
        if qty <= 0 or qty * refs.buy < rules.min_cost:
            msg = f"[{book.name}] SIZE rejeitado {symbol}: {qty * refs.buy:.2f} abaixo do mínimo da corretora"
            self.storage.log_event("SIZE", msg)
            self.agent("SIZE", msg)
            return False, msg
        self.agent("SIZE", f"[{book.name}] {symbol} {qty:g} ≈ {qty * refs.buy:.2f} ({why})")
        return self._execute_open(book, symbol, qty, refs, ts, None, None)

    def close_position(self, book: Book, pos: Position, ref_price: float, ts: int, reason: str) -> None:
        self._close(book, pos, ref_price, ts, reason)

    def _record_fill(self, book: str, side: str, symbol: str, ref: float, requested: float, res) -> None:
        """FILLS: parcial é reportado como parcial; slippage acima do máximo é sinalizado, nunca absorvido."""
        bps = (((res.price / ref - 1) if side == "buy" else (1 - res.price / ref)) * 10_000) if ref else 0.0
        partial = res.amount < requested * 0.999
        if self.telemetry:
            key = f"desk:fills:{ny_day(int(time.time() * 1000))}"
            fills = self.storage.get_json(key, []) or []
            fills.append({"book": book, "side": side, "symbol": symbol, "slippage_bps": round(bps, 2),
                          "partial": partial, "requested": requested, "filled": res.amount, "price": res.price})
            self.storage.set_json(key, fills[-500:])
        if bps > MAX_SLIPPAGE_BPS or partial:
            msg = (f"⚠️ FILLS [{book}] {side} {symbol}: slippage {bps:+.1f} bps"
                   + (f", PARCIAL {res.amount:g}/{requested:g}" if partial else ""))
            self.storage.log_event("FILLS", msg)
            self.notify(msg)

    def _execute_open(self, book: Book, symbol: str, qty: float, refs: FillRefs, ts: int,
                      stop_dist: float | None, target_dist: float | None, side: str = "long") -> tuple[bool, str]:
        """Abre long (compra no ask) ou short (vende no bid). Short a 1x trava o valor da posição como margem."""
        short = side == "short"
        ref = refs.sell if short else refs.buy
        verdict = self.risk.check_entry(book, symbol, qty * ref, ts, refs.spread_pct,
                                        self.storage.is_paused(), self.last_prices)
        if not verdict.allowed:
            msg = f"[{book.name}] RISK bloqueou {symbol}: {'; '.join(verdict.reasons)}"
            log.info(msg)
            self.storage.log_event("RISK", msg)
            self.agent("RISK", msg)
            return False, msg
        self.agent("RISK", f"[{book.name}] {symbol} aprovado (exposição atual {book.exposure():.2f})")
        oid = f"{book.name}-{symbol.replace('/', '')}-{ts}-{'short' if short else 'buy'}"
        if self.storage.order_exists(oid):
            return False, "ordem já enviada"
        t0 = time.perf_counter()
        res = self.exchange.place_order(OrderRequest(oid, symbol, "sell" if short else "buy", qty, ref))
        self.agent("FILLS", f"[{book.name}] {'venda a descoberto' if short else 'compra'} {symbol}: "
                   f"{res.status} {res.note}".strip(),
                   ok=res.status == "filled", latency_ms=(time.perf_counter() - t0) * 1000)
        if res.status != "filled":
            self.storage.record_rejected_order(book.name, res)
            log.info("[%s] ordem rejeitada %s: %s", book.name, symbol, res.note)
            return False, f"ordem rejeitada: {res.note}"
        if res.price <= 0:  # nunca grava posição sem preço: o RECONCILE acusa a diferença com a corretora
            msg = f"[{book.name}] {symbol} executada sem preço ({res.note}); posição não registrada"
            self.storage.log_event("ERROR", msg)
            self.notify(f"⚠️ {msg}")
            return False, msg
        self._record_fill(book.name, "sell" if short else "buy", symbol, ref, qty, res)
        cost = res.amount * res.price
        book.cash -= cost + res.fee
        sign = -1 if short else 1  # short: stop acima, alvo abaixo
        pos = Position(book.name, symbol, res.amount, res.price, res.fee, ts,
                       stop_price=res.price - sign * stop_dist if stop_dist is not None else 0.0,
                       take_profit_price=res.price + sign * target_dist if target_dist is not None else 0.0,
                       side=side)
        book.positions[symbol] = pos
        self.storage.save_open(book, pos, res)
        exits = (f"stop {pos.stop_price:.2f} alvo {pos.take_profit_price:.2f}" if stop_dist is not None
                 else "saída pelo RISK das 20:05 NY")
        msg = (f"{'🔴' if short else '🟢'} [{self.s.mode}/{book.name}] {'VENDA A DESCOBERTO' if short else 'COMPRA'} "
               f"{symbol} {res.amount:g} @ {res.price:.2f} ({cost:.2f}, taxa {res.fee:.3f}) {exits}")
        log.info(msg)
        self.notify(msg)
        return True, f"{res.amount:g} @ {res.price:.2f}"

    def _close(self, book: Book, pos: Position, ref_price: float, ts: int, reason: str) -> None:
        """Long: vende. Short: recompra; volta ao caixa a margem mais o ganho (ou menos a perda) do short."""
        short = pos.is_short
        side, verb = ("buy", "recompra") if short else ("sell", "venda")
        oid = f"{book.name}-{pos.symbol.replace('/', '')}-{ts}-{'cover' if short else 'sell'}"
        t0 = time.perf_counter()
        res = self.exchange.place_order(OrderRequest(oid, pos.symbol, side, pos.qty, ref_price, reduce=True))
        self.agent("FILLS", f"[{book.name}] {verb} {pos.symbol} ({reason}): {res.status} {res.note}".strip(),
                   ok=res.status == "filled", latency_ms=(time.perf_counter() - t0) * 1000)
        if res.status != "filled":
            self.storage.log_event("ERROR", f"[{book.name}] {verb} rejeitada {pos.symbol}: {res.note}")
            return
        self._record_fill(book.name, side, pos.symbol, ref_price, pos.qty, res)
        back = res.amount * (2 * pos.entry_price - res.price) if short else res.amount * res.price
        book.cash += back - res.fee
        pnl = back - pos.qty * pos.entry_price - pos.entry_fee - res.fee
        trade = Trade(book.name, pos.symbol, pos.qty, pos.entry_price, res.price, pos.entry_ts, ts,
                      pos.entry_fee + res.fee, pnl, reason, side=pos.side)
        del book.positions[pos.symbol]
        book.trades.append(trade)
        self.storage.save_close(book, trade, res)
        icon = "✅" if pnl > 0 else "🔻"
        msg = (f"{icon} [{self.s.mode}/{book.name}] {'RECOMPRA' if short else 'VENDA'} {pos.symbol} @ {res.price:.2f} "
               f"— P&L simulado {pnl:+.2f} ({reason})")
        log.info(msg)
        self.notify(msg)


# ---------------------------------------------------------------------- PAPER ao vivo
def run_paper(engine: Engine, market_data, telegram=None, max_iterations: int | None = None,
              sleep: Callable[[float], None] = time.sleep, desk=None) -> None:
    s = engine.s
    kill_notified = False
    iteration = 0
    engine.storage.log_event("INFO", f"{s.mode} iniciado: {s.symbols} {s.timeframe} deciders={list(engine.books)}")
    engine.notify(f"▶️ rafs {s.mode} iniciado — {', '.join(s.symbols)} ({s.timeframe}). Valores são SIMULADOS (sem dinheiro real).")
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        engine.storage.set("heartbeat", str(int(time.time() * 1000)))
        if desk is None:  # com a mesa, o CHIEF mostra a prova das ferramentas
            engine.agent("CHIEF", f"ciclo {iteration}", ok=not engine.risk.circuit_open)
        if engine.risk.kill_switch_active() and not kill_notified:
            engine.notify("🛑 Kill switch ativo: novas entradas bloqueadas.")
            kill_notified = True
        for symbol in s.symbols:
            try:
                t0 = time.perf_counter()
                candles = market_data.fetch_closed_candles(symbol, s.timeframe, FEATURE_WINDOW)
                quote = market_data.get_quote(symbol)
                engine.agent("SCAN", f"{symbol}: {len(candles)} candles, bid {quote.bid:g} ask {quote.ask:g}",
                             latency_ms=(time.perf_counter() - t0) * 1000)
                if engine.telemetry:
                    engine.storage.set_json(f"quote:{symbol}", {"bid": quote.bid, "ask": quote.ask, "ts": quote.ts,
                                                                "spread_pct": quote.spread_pct})
                key = f"last_ts:{symbol}"
                if candles[-1].ts <= int(engine.storage.get(key, "0")):
                    engine.risk.record_success()
                    continue
                engine.process_candle(symbol, candles, FillRefs(quote.ask, quote.bid, quote.spread_pct))
                engine.storage.set(key, str(candles[-1].ts))
                engine.risk.record_success()
            except Exception as e:  # noqa: BLE001 — qualquer falha conta para o circuit breaker
                was_open = engine.risk.circuit_open
                engine.risk.record_error()
                msg = f"erro em {symbol}: {type(e).__name__}: {e}"
                log.warning(msg)
                engine.storage.log_event("ERROR", msg)
                engine.agent("CHIEF", msg, ok=False)
                if engine.risk.circuit_open and not was_open:
                    engine.notify(f"⚠️ Circuit breaker aberto após erros seguidos. Último: {msg}")
        engine.storage.set("circuit_open", "1" if engine.risk.circuit_open else "0")
        if desk is not None:
            desk.tick()  # relógio da mesa: 06:00 dial, 20:05 crypto, 23:55 relatório, RISK da conta
        if telegram is not None:
            try:
                telegram.poll_commands()
            except Exception as e:  # noqa: BLE001
                log.warning("telegram: %s", e)
        if max_iterations is None or iteration < max_iterations:
            sleep(s.poll_seconds)
