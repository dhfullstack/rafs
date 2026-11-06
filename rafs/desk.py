"""A mesa do guia "The Hedge Fund Terminal" (savip, 13/out/2026) — CHIEF, QUANT, RISK e o relatório do dia.

Código determinístico no relógio de Nova York, como no guia:
  06:00  CHIEF prova as ferramentas; QUANT publica o dial      (NO DIAL, NO DESK)
  20:05  crypto: book "quant" segue os alvos 12-1 × dial; RISK vende o que saiu dos alvos ou dial < 0.50
  23:55  CHIEF manda o relatório do dia (montado do banco, nunca inventado)
  sempre RISK: conta −4% no dia → fecha tudo e fica FLAT até amanhã; CHIEF avisa número que mexer mais de 10%

Adaptação ao que temos: Binance testnet no lugar de Alpaca, FRED + Binance pública no lugar do QuantGPT,
Discord/Telegram para o relatório. Memes rodam em PAPER com GeckoTerminal no lugar do FOMO (rafs/memes.py);
ações (Alpaca) não existem aqui: essa cadeira fica OFF.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, time as dtime, timedelta
from typing import Callable

from .config import Settings
from .dial import NY, STAND_DOWN_BELOW, is_valid
from .engine import Engine, FillRefs
from .portfolio import Book
from .storage import Storage

log = logging.getLogger("rafs.desk")

DIAL_AT = dtime(6, 0)
CRYPTO_AT = dtime(20, 5)
REPORT_AT = dtime(23, 55)
ACCOUNT_STOP_PCT = 4.0  # equity abaixo do fechamento anterior em 4% → FLAT
MOVE_ALERT_PCT = 10.0  # mensagem no meio da sessão só quando um número mexe mais que isso
PROOF_RETRY_S = 1800  # checagem vermelha: tenta de novo a cada 30 min (o sleeve volta quando passar)
DIAL_RETRY_S = 900
QUANT_BOOK = "quant"
MEMES_OFF = {"memes": "MEME_SLEEVE=0: memecoin SCAN and JUDGE off"}
STOCKS_OFF = {"stocks": "no Alpaca/QuantGPT: STOCKS and the Runner (TQQQ/SHY) do not exist here"}
SLEEVES_OFF = {**MEMES_OFF, **STOCKS_OFF}
MEME_BOOKS = ("memes", "memes_top")
GREEN_LABELS = {"liquidity": "liquidez", "volatility": "VIX", "credit": "crédito", "btc": "BTC>200d",
                "sol": "SOL>50d", "nasdaq": "Nasdaq>200d"}


def ny_now(now_s: float) -> datetime:
    return datetime.fromtimestamp(now_s, NY)


def ny_day(ms: int) -> str:
    return ny_now(ms / 1000).date().isoformat()


def day_start_ms(day: str) -> int:
    return int(datetime.combine(datetime.fromisoformat(day).date(), dtime(0, 0), NY).timestamp() * 1000)


def at_ms(day: str, t: dtime) -> int:
    return int(datetime.combine(datetime.fromisoformat(day).date(), t, NY).timestamp() * 1000)


def dial_text(d: dict) -> str:
    dial = d["dial"]
    greens = " ".join(f"{GREEN_LABELS[k]} {'✓' if v else '✗'}" for k, v in d["greens"].items())
    targets = (" + ".join(f"{b} {w:.2f}" for b, w in d["crypto_targets"].items())
               or "caixa (nenhum momentum 12-1 positivo)")
    lines = [f"📟 QUANT {d['date']}: dial crypto {dial['crypto']:.2f} · meme {dial['meme']:.2f} · "
             f"stocks {dial['stocks']:.2f}", f"verdes: {greens}", f"alvos crypto: {targets}"]
    if d.get("degraded"):
        lines.append(f"DEGRADED (conta como vermelho): {', '.join(d['degraded'])}")
    if d.get("errors"):
        lines.append(f"fontes com erro: {'; '.join(d['errors'])[:200]}")
    if dial["crypto"] < STAND_DOWN_BELOW:
        lines.append("dial crypto abaixo de 0.50: a mesa crypto fica parada hoje")
    return "\n".join(lines)


def context_line(d: dict | None) -> str:
    """O que o Grok e o Jev leem da mesa: só números (o dial é aritmética, o Jev só lê)."""
    if not d:
        return "desk crypto_dial=none (no dial published today)"
    btc = d["inputs"].get("btc_vs_200d")
    return (f"desk crypto_dial={d['dial']['crypto']:.2f} "
            f"btc_vs_200d={'n/a' if btc is None else format(btc, '+.4f')}")


def sleeves_off(s: Settings) -> dict[str, str]:
    return {**({} if s.meme_sleeve else MEMES_OFF), **STOCKS_OFF}


def off_text(s: Settings) -> str:
    return "ações e Runner (sem Alpaca/QuantGPT)" + ("" if s.meme_sleeve else " · memes (MEME_SLEEVE=0)")


def block_reason(storage: Storage, book: str, now_ms: int, sleeves: dict[str, tuple[str, ...]] | None = None) -> str | None:
    """RISK FLAT hoje ou CHIEF DEGRADED: nenhuma entrada neste book. Serve aos books de 15m e aos de memes."""
    if storage.get(f"desk:flat:{book}") == ny_day(now_ms):
        return f"RISK: FLAT hoje (patrimônio caiu {ACCOUNT_STOP_PCT:.0f}% no dia)"
    proof = storage.get_json("desk:proof") or {}
    if book in proof.get("degraded", []):
        need = (proof.get("sleeves") or sleeves or {}).get(book, ())
        failed = [k for k in need if not proof["checks"].get(k, {}).get("ok", True)]
        return f"CHIEF: sleeve DEGRADED ({', '.join(failed) or 'checagem'} falhou na checagem)"
    return None


def desk_context(storage: Storage, now_ms: int | None = None) -> str:
    d = storage.get_json("desk:dial")
    return context_line(d if is_valid(d, int(time.time() * 1000) if now_ms is None else now_ms) else None)


class Desk:
    def __init__(self, settings: Settings, storage: Storage, engine: Engine, market_data,
                 publish_dial: Callable[[datetime], dict], tools: dict[str, Callable[[], str]],
                 sleeves: dict[str, tuple[str, ...]], gate_all_books: bool = True,
                 clock: Callable[[], float] = time.time):
        self.s = settings
        self.storage = storage
        self.engine = engine
        self.md = market_data
        self.publish_dial = publish_dial
        self.tools = tools
        self.sleeves = sleeves
        self.gate_all_books = gate_all_books  # DESK_RULES: dial e alvos valem para todos os books
        self.clock = clock
        self.notify = engine.notify
        engine.desk = self

    # ------------------------------------------------------------ leitura
    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def dial(self) -> dict | None:
        d = self.storage.get_json("desk:dial")
        return d if is_valid(d, self.now_ms()) else None

    def book_block(self, book: str) -> str | None:
        return block_reason(self.storage, book, self.now_ms(), self.sleeves)

    def entry_block(self, book: str, symbol: str) -> str | None:
        """Motivo para nenhuma entrada chegar ao SIZE agora (None = liberado)."""
        block = self.book_block(book)
        if block or not self.gate_all_books:
            return block
        d = self.dial()
        if d is None:
            return "sem dial do QUANT publicado hoje (NO DIAL, NO DESK)"
        crypto = d["dial"]["crypto"]
        if crypto < STAND_DOWN_BELOW:
            return f"dial crypto {crypto:.2f} < 0.50, mesa parada hoje"
        base = symbol.split("/")[0]
        if base not in d["crypto_targets"]:
            score = d.get("momentum_12_1", {}).get(base)
            return (f"{base} fora dos alvos do QUANT (momentum 12-1 "
                    f"{'n/a' if score is None else format(score * 100, '+.1f') + '%'})")
        return None

    def size_multiplier(self) -> float:
        """ticket *= crypto_dial. Nunca maior que 1: o dial só reduz."""
        if not self.gate_all_books:
            return 1.0
        d = self.dial()
        return min(1.0, max(0.0, d["dial"]["crypto"])) if d else 0.0

    # ------------------------------------------------------------ relógio
    def tick(self) -> None:
        now = self.now_ms()
        ny = ny_now(now / 1000)
        day = ny.date().isoformat()
        for step in (self._morning, self._account_risk, self._crypto_session, self._alerts, self._report):
            try:
                step(now, ny, day)
            except Exception as e:  # noqa: BLE001 — uma etapa com erro não derruba as outras
                msg = f"mesa {step.__name__.strip('_')}: {type(e).__name__}: {e}"
                log.warning(msg)
                self.storage.log_event("ERROR", msg)

    def _morning(self, now: int, ny: datetime, day: str) -> None:
        after_open = ny.time() >= DIAL_AT
        proof = self.storage.get_json("desk:proof")
        if (proof is None or (after_open and proof.get("day") != day)
                or (not proof.get("ready") and now - proof.get("ts", 0) >= PROOF_RETRY_S * 1000)):
            self.prove_tools(now, day)
        d = self.storage.get_json("desk:dial")
        stale = (not is_valid(d, now) or (after_open and d.get("date") != day)
                 or (bool(d.get("errors")) and now - d["computed_at"] >= DIAL_RETRY_S * 1000))  # fonte voltou?
        if stale and now >= int(self.storage.get("desk:dial_retry_at", "0") or 0):
            prev = d
            try:
                d = self.publish_dial(ny)
            except Exception as e:  # noqa: BLE001
                self.storage.set("desk:dial_retry_at", str(now + DIAL_RETRY_S * 1000))
                msg = f"QUANT não publicou o dial: {type(e).__name__}: {e}"
                self.storage.log_event("ERROR", msg)
                self.engine.agent("QUANT", msg, ok=False)
                return
            self.storage.set_json("desk:dial", d)
            self.storage.set_json(f"desk:dial:{d['date']}", d)
            text = dial_text(d)
            self.engine.agent("QUANT", text.replace("\n", " | "))
            same = prev and all(prev.get(k) == d.get(k) for k in ("date", "dial", "crypto_targets", "degraded"))
            if not same:  # republicação com os mesmos números não vira mensagem
                self.storage.log_event("QUANT", text.replace("\n", " | "))
                self.notify(text)

    def prove_tools(self, now: int, day: str) -> dict:
        """PROVE THE TOOLS: todas precisam passar. Uma falha deixa só o sleeve dela DEGRADED."""
        checks = {}
        for name, fn in self.tools.items():
            t0 = time.perf_counter()
            try:
                checks[name] = {"ok": True, "detail": str(fn())[:160]}
            except Exception as e:  # noqa: BLE001
                checks[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"[:160]}
            checks[name]["ms"] = round((time.perf_counter() - t0) * 1000)
        degraded = sorted(b for b, need in self.sleeves.items() if any(not checks.get(t, {"ok": True})["ok"] for t in need))
        ready = all(c["ok"] for c in checks.values())  # nunca READY com uma checagem vermelha
        proof = {"day": day, "ts": now, "checks": checks, "degraded": degraded, "ready": ready,
                 "off": sleeves_off(self.s), "sleeves": {b: list(t) for b, t in self.sleeves.items()}}
        prev = self.storage.get_json("desk:proof") or {}
        self.storage.set_json("desk:proof", proof)
        summary = " · ".join(f"{k} {'ok' if c['ok'] else 'FALHOU'}" for k, c in checks.items())
        head = "READY" if ready else f"DEGRADED ({', '.join(degraded) or 'nenhum book afetado'})"
        self.engine.agent("CHIEF", f"checagem {day}: {head} — {summary}", ok=ready)
        self.storage.log_event("CHIEF", f"checagem {day}: {head} — {summary}")
        if prev.get("day") != day or prev.get("ready") != ready:  # não repete a mesma mensagem a cada 30 min
            lines = [f"{'✅' if ready else '⚠️'} CHIEF {day}: {head}", summary]
            lines += [f"{k}: {c['detail']}" for k, c in checks.items() if not c["ok"]]
            lines.append(f"off: {off_text(self.s)}")
            self.notify("\n".join(lines))
        return proof

    def _account_risk(self, now: int, ny: datetime, day: str) -> None:
        """THE ACCOUNT: equity abaixo da abertura do dia em 4% → fecha tudo, FLAT até amanhã."""
        for name, book in self.engine.books.items():
            equity = book.equity(self.engine.last_prices)
            key = f"desk:open:{day}:{name}"
            opened = self.storage.get(key)
            if opened is None:
                self.storage.set(key, repr(equity))
                continue
            if self.storage.get(f"desk:flat:{name}") == day:
                continue
            opened_f = float(opened)
            if opened_f > 0 and equity < opened_f * (1 - ACCOUNT_STOP_PCT / 100):
                self.flatten(book, now, f"RISK conta −{ACCOUNT_STOP_PCT:.0f}% no dia")
                self.storage.set(f"desk:flat:{name}", day)
                msg = (f"🛑 RISK [{self.s.mode}/{name}] patrimônio {opened_f:.2f} → {equity:.2f} "
                       f"({(equity / opened_f - 1) * 100:+.2f}%) no dia. FLAT até amanhã: posições fechadas, "
                       f"nenhuma entrada hoje.")
                self.storage.log_event("RISK", msg)
                self.engine.agent("RISK", msg)
                self.notify(msg)

    def flatten(self, book: Book, now: int, reason: str) -> None:
        for pos in list(book.positions.values()):
            q = self.md.get_quote(pos.symbol)
            self.engine.close_position(book, pos, q.ask if pos.is_short else q.bid, now, reason)

    def _crypto_session(self, now: int, ny: datetime, day: str) -> None:
        if QUANT_BOOK not in self.engine.books or ny.time() < CRYPTO_AT or self.storage.get("desk:crypto_day") == day:
            return
        self.rebalance_quant(at_ms(day, CRYPTO_AT), day)
        self.storage.set("desk:crypto_day", day)

    def rebalance_quant(self, ts: int, day: str) -> list[str]:
        """crypto: QUANT -> SIZE -> FILLS -> RISK, às 20:05 NY. Sem LLM no caminho."""
        book = self.engine.books[QUANT_BOOK]
        d = self.dial()
        crypto = d["dial"]["crypto"] if d else 0.0
        targets = d["crypto_targets"] if d else {}
        done: list[str] = []
        for pos in list(book.positions.values()):  # RISK primeiro: vende o que não pode ficar
            base = pos.symbol.split("/")[0]
            why = ("sem dial válido" if d is None else f"dial crypto {crypto:.2f} < 0.50"
                   if crypto < STAND_DOWN_BELOW else None if base in targets else f"{base} saiu dos alvos")
            if why:
                q = self.md.get_quote(pos.symbol)
                self.engine.close_position(book, pos, q.ask if pos.is_short else q.bid, ts, f"RISK 20:05 ({why})")
                done.append(f"vende {pos.symbol}: {why}")
        block = self.book_block(QUANT_BOOK)
        if d is None or crypto < STAND_DOWN_BELOW or block:
            done.append(block or ("sem dial: nada entra" if d is None else "dial < 0.50: caixa"))
        else:
            for base, weight in targets.items():
                symbol = next((s for s in self.s.symbols if s.split("/")[0] == base), None)
                if symbol is None:
                    done.append(f"{base} é alvo mas não está em SYMBOLS")
                    continue
                if symbol in book.positions:
                    done.append(f"mantém {symbol}")
                    continue
                q = self.md.get_quote(symbol)
                notional = min(self.s.entry_brl, self.s.max_exposure_brl * crypto * weight)  # SIZE: só reduz
                ok, note = self.engine.open_notional(book, symbol, notional, FillRefs(q.ask, q.bid, q.spread_pct), ts,
                                                     f"QUANT 20:05 alvo {weight:.2f} × dial {crypto:.2f}")
                done.append(f"compra {symbol}: {note}" if ok else f"{symbol} não entrou: {note}")
        msg = f"[quant] sessão crypto 20:05 NY {day}: " + ("; ".join(done) or "nada a fazer")
        self.storage.log_event("FILLS", msg)
        self.engine.agent("FILLS", msg)
        return done

    def _alerts(self, now: int, ny: datetime, day: str) -> None:
        """Mensagem no meio da sessão só quando um número mexe mais de 10%."""
        values = {f"book {n}": b.equity(self.engine.last_prices) for n, b in self.engine.books.items()}
        values.update({f"preço {s}": p for s, p in self.engine.last_prices.items() if s in self.s.symbols})
        for label, value in values.items():
            key = f"desk:alert_open:{day}:{label}"
            opened = self.storage.get(key)
            if opened is None:
                self.storage.set(key, repr(value))
                continue
            move = (value / float(opened) - 1) * 100 if float(opened) else 0.0
            if abs(move) > MOVE_ALERT_PCT and self.storage.get(f"desk:alerted:{day}:{label}") is None:
                self.storage.set(f"desk:alerted:{day}:{label}", "1")
                self.notify(f"📣 CHIEF: {label} mexeu {move:+.1f}% hoje ({float(opened):.2f} → {value:.2f}).")

    def _report(self, now: int, ny: datetime, day: str) -> None:
        if ny.time() < REPORT_AT or self.storage.get("desk:report_day") == day:
            return
        text = report_text(self.storage, self.s, self.engine.last_prices, day, now)
        self.storage.set("desk:report_day", day)
        self.storage.set("desk:last_report", text)
        self.notify(text)


# ---------------------------------------------------------------- relatório
def report_text(storage: Storage, s: Settings, prices: dict[str, float], day: str, now_ms: int) -> str:
    """REPORT: dial por sleeve, candidatos, rejeições com a checagem que disparou, fills com slippage,
    fechamentos com a regra que disparou, banca de abertura e fechamento. Montado do banco, nunca inventado."""
    start = day_start_ms(day)
    d = storage.get_json(f"desk:dial:{day}") or storage.get_json("desk:dial")
    lines = [f"DESK {day} · {s.mode} · valores SIMULADOS (sem dinheiro real)"]
    if d:
        dial = d["dial"]
        lines.append(f"dial     crypto {dial['crypto']:.2f}   meme {dial['meme']:.2f}{'' if s.meme_sleeve else ' (off)'}   "
                     f"stocks {dial['stocks']:.2f} (off)" + ("" if d["date"] == day else f"  [dial de {d['date']}]"))
        lines.append("alvos    " + (" + ".join(f"{b} {w:.2f}" for b, w in d["crypto_targets"].items())
                                    or "caixa (momentum 12-1 sem positivo)"))
    else:
        lines.append("dial     nenhum publicado (NO DIAL, NO DESK)")
    proof = storage.get_json("desk:proof") or {}
    if proof.get("day") == day:
        bad = [f"{k} ({c['detail']})" for k, c in proof["checks"].items() if not c["ok"]]
        lines.append("CHIEF    " + ("READY" if proof["ready"] else "DEGRADED: " + "; ".join(bad)))
    else:
        lines.append("CHIEF    sem checagem hoje")

    grok_reads = grok_escalated = jev_calls = desk_blocked = 0
    grok_usd = 0.0
    jev_actions: dict[str, int] = {}
    for decider, action, reason, raw, credits in storage.decisions_between(start, now_ms):
        if reason.startswith("mesa:"):
            desk_blocked += 1
        g = (json.loads(raw) if raw else {}).get("grok") or {}
        if "escalate" in g:
            grok_reads += 1
            grok_escalated += bool(g["escalate"])
            grok_usd += float(g.get("cost_usd") or 0)
        if decider == "jev" and credits > 0:
            jev_calls += 1
            jev_actions[action] = jev_actions.get(action, 0) + 1
    lines.append(f"GROK     {grok_reads} leituras, {grok_escalated} candidatos escalados ao Jev, US$ {grok_usd:.4f}")
    lines.append(f"JEV      {jev_calls} chamadas" + "".join(f" · {a} {n}" for a, n in sorted(jev_actions.items())))
    lines.append(f"MESA     {desk_blocked} candles barrados antes do SIZE (dial, alvos, FLAT ou DEGRADED)")
    if s.meme_sleeve:
        lines.append(memes_line(storage, start, now_ms))

    rejections: dict[str, int] = {}
    for _, level, msg in storage.events_between(start, now_ms):
        if level in ("RISK", "SIZE") and ("bloqueou" in msg or "rejeitado" in msg):
            check = msg.split(": ", 1)[-1].split(";")[0].split(" (")[0][:60]
            rejections[f"{level} {check}"] = rejections.get(f"{level} {check}", 0) + 1
    lines.append("REJEIÇÕES " + (", ".join(f"{k} ×{n}" for k, n in sorted(rejections.items(), key=lambda x: -x[1])[:4])
                                 or "nenhuma"))
    fills = (storage.get_json(f"desk:fills:{day}") or []) + (storage.get_json(f"memes:fills:{day}") or [])
    if fills:
        avg = sum(f["slippage_bps"] for f in fills) / len(fills)
        partial = sum(1 for f in fills if f["partial"])
        lines.append(f"FILLS    {len(fills)} ordens, slippage médio {avg:+.1f} bps, pior "
                     f"{max(f['slippage_bps'] for f in fills):+.1f} bps, {partial} parciais")
    else:
        lines.append("FILLS    nenhuma ordem executada")

    prices = {**prices, **(storage.get_json("memes:prices") or {})}  # memes: preço de saída (impacto + taxa)
    rules: dict[str, int] = {}
    kills = sum(1 for n in storage.book_names() if storage.get(f"desk:flat:{n}") == day)
    for name in storage.book_names():
        b = storage.load_book(name, s.meme_bank_usd if name in MEME_BOOKS else s.capital_brl)
        today = [t for t in b.trades if start <= t.exit_ts <= now_ms]
        for t in today:
            rule = t.exit_reason.split(" (")[0]
            rules[rule] = rules.get(rule, 0) + 1
        wins = sum(1 for t in today if t.pnl > 0)
        best = max((t.pnl / (t.qty * t.entry_price) * 100 for t in today if t.qty), default=None)
        opened = storage.get(f"desk:open:{day}:{name}")
        close = b.equity(prices)
        bank = f"banca {float(opened):.2f} → {close:.2f}" if opened else f"banca {close:.2f}"
        lines.append(f"[{name}]".ljust(11) + f"{len(today)} trades, {wins}W {len(today) - wins}L"
                     + (f", melhor {best:+.2f}%" if best is not None else "") + f" · {bank}")
    lines.append(f"RISK     {kills} kills (FLAT) · fechamentos: "
                 + (", ".join(f"{r} {n}" for r, n in sorted(rules.items())) or "nenhum"))
    lines.append(f"off      {off_text(s)}")
    return "\n".join(lines)


def memes_line(storage: Storage, start: int, now_ms: int) -> str:
    """SCANs do dia, quantos foram ao Grok, o que o Jev escolheu. Do banco, nunca inventado."""
    scans = reads = picks = nones = 0
    usd = 0.0
    for decider, action, reason, raw, credits in storage.decisions_between(start, now_ms):
        if decider != "memes" or not reason:
            continue
        r = json.loads(raw) if raw else {}
        g = r.get("grok_memes") or {}
        if "shortlist" in g:
            reads += 1
            usd += float(g.get("cost_usd") or 0)
        scans += not reason.startswith(("alvo antes do stop", "erro Jev: chance"))
        picks += reason.startswith("jev escolheu")
        nones += reason.startswith("jev: NONE")
    return (f"MEMES    {scans} SCANs registrados · Grok leu {reads} (US$ {usd:.4f}) · Jev escolheu {picks}, "
            f"NONE {nones}")


def next_times(now_s: float) -> dict[str, int]:
    """Próximos horários da mesa (para o painel)."""
    ny = ny_now(now_s)
    out = {}
    for name, t in (("dial", DIAL_AT), ("crypto", CRYPTO_AT), ("report", REPORT_AT)):
        when = datetime.combine(ny.date(), t, NY)
        if when <= ny:
            when += timedelta(days=1)
        out[name] = int(when.timestamp() * 1000)
    return out
