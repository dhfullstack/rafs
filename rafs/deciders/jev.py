"""JUDGE — o Jev decide BUY/SELL/HOLD a partir de features numéricas.

Proteção contra prompt injection: o estado enviado contém APENAS números calculados localmente
e o símbolo vindo da configuração (validado por regex). Nenhum texto externo chega ao Jev.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from ..exits import exit_pcts
from ..jev_client import JevAuthError, JevClient, JevCreditsError, JevError, JevRateLimited
from .grok_screen import ENTRY_BUDGET_SHARE, GrokScreener
from ..models import Action, Decision, Features, Position
from ..portfolio import utc_day
from .base import Decider

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}/[A-Z]{3,5}$")
GATED_PREFIX = "filtro:"
GROK_PREFIX = "grok:"  # o Grok leu e não escalou para o Jev
COOLDOWN_MIN_S = 60
COOLDOWN_MAX_S = 1800  # cada 429 seguido dobra a espera até 30 min

ENTRY_QUESTION = (
    "Spot crypto, {tf} candles. Should we open a long position now, expecting price to rise more than "
    "~0.4% (fees+slippage) within the next few hours? A stop sits {stop}x ATR below entry. "
    "Answer BUY only if the setup is clearly favorable."
)
# v2: pergunta exatamente o que decide o trade (alvo antes do stop). A probabilidade vira chance de ganho
# e o corte sai do ponto de equilíbrio com custos, não de um número fixo.
ENTRY_QUESTION_V2 = (
    "Spot crypto, {tf} candles. If we buy now, will price hit the take-profit at +{tp:.2f}% before the "
    "stop-loss at -{sl:.2f}%, within the next {hold} candles?"
)
V2_CHOICES = ["TARGET", "STOP"]
PROMPT_VERSIONS = ("v1", "v2")


def breakeven_probability(tp_pct: float, sl_pct: float, cost_pct: float) -> float:
    """p mínima para expectativa ≥ 0: p·alvo − (1−p)·stop − custos ≥ 0 (tudo em % do preço)."""
    return (sl_pct + cost_pct) / (tp_pct + sl_pct)


EXIT_QUESTION = (
    "Spot crypto, {tf} candles. We hold a long. Should we exit now (SELL) or keep holding (HOLD)? "
    "Consider trend, momentum and the current P&L."
)
# FUTURES (demo, 1x): mesmas perguntas com o mercado certo, mais o espelho para short
FUTURES_MARKET = "USDT perpetual futures at 1x"
ENTRY_SHORT_QUESTION = (
    "USDT perpetual futures at 1x, {tf} candles. Should we open a short position now, expecting price to fall "
    "more than the round-trip costs within the next few hours? A stop sits {stop}x ATR above entry. "
    "Answer SELL only if the setup is clearly favorable."
)
ENTRY_SHORT_QUESTION_V2 = (
    "USDT perpetual futures at 1x, {tf} candles. If we open a short now, will price fall to the take-profit at "
    "-{tp:.2f}% before rising to the stop-loss at +{sl:.2f}%, within the next {hold} candles?"
)
EXIT_SHORT_QUESTION = (
    "USDT perpetual futures at 1x, {tf} candles. We hold a short. Should we close it now by buying back (BUY) "
    "or keep holding (HOLD)? Consider trend, momentum and the current P&L."
)
# fade: a entrada aposta na volta à VWAP48; a pergunta diz isso para o Jev julgar a tese certa
FADE_ENTRY_NOTE = " Setup: mean reversion, price {dev:.2f}% {where} its 48-candle VWAP."
FADE_EXIT_NOTE = " It was opened to fade a stretch from the 48-candle VWAP: exit only if that reversion failed."


def build_state(f: Features, position: Position | None, context: str | None = None) -> dict:
    if not SYMBOL_RE.match(f.symbol):
        raise ValueError(f"símbolo fora do padrão permitido: {f.symbol!r}")
    lines = [
        f"asset={f.symbol}",
        f"close={f.close:.8g}",
        f"ema12={f.ema_fast:.8g} ema26={f.ema_slow:.8g} ema_spread_pct={(f.ema_fast / f.ema_slow - 1) * 100:.3f}",
        f"rsi14={f.rsi:.1f}",
        f"atr14_pct={f.atr_pct:.3f}",
        f"vwap48_dev_pct={f.vwap_dev_pct:.3f}",
        f"momentum12_pct={f.momentum_pct:.3f}",
        f"volatility48_pct={f.volatility_pct:.3f}",
        f"relative_volume={f.rel_volume:.2f}",
        f"regime={f.regime}",
        f"trend24h_pct={f.trend24h_pct:.3f} mom4h_pct={f.mom4h_pct:.3f} range24h_pos={f.range24h_pos:.2f}",
    ]
    if context:
        lines.append(context)  # só números da mesa (dial); o Jev lê o dial, nunca o muda
    state: dict = {"message": "\n".join(lines), "rsi": round(f.rsi, 2), "momentum_pct": round(f.momentum_pct, 3)}
    if position is not None:
        state["in_position"] = True
        state["position_pnl_pct"] = round(position.pnl_pct(f.close), 3)  # short: preço caindo é ganho
        if position.is_short:
            state["position_side"] = "short"
    else:
        state["in_position"] = False
    return state


class JevDecider(Decider):
    name = "jev"

    def __init__(self, client: JevClient, timeframe: str, stop_atr_mult: float, min_probability: float,
                 max_calls_per_day: int, calls_today: Callable[[str], int], key_prefix: str = "gbj-p",
                 clock: Callable[[], float] = time.time, trend_gate: bool = True, prompt_version: str = "v1",
                 take_profit_atr_mult: float = 3.0, cost_pct: float = 0.3, edge_margin: float = 0.03,
                 max_hold_candles: int = 32, max_calls_per_hour: int = 0,
                 recent_calls: Callable[[int], int] | None = None, screener: GrokScreener | None = None,
                 context: Callable[[], str] | None = None, cooldown_get: Callable[[], float] | None = None,
                 cooldown_set: Callable[[float], None] | None = None, allow_short: bool = False,
                 min_stop_pct: float = 0.0, entry_style: str = "trend", fade_min_dev_pct: float = 1.0):
        if entry_style not in ("trend", "fade"):
            raise ValueError(f"entry_style inválido: {entry_style}")
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"prompt_version inválida: {prompt_version}")
        self.client = client
        self.key_prefix = key_prefix
        self.timeframe = timeframe
        self.stop_atr_mult = stop_atr_mult
        self.min_probability = min_probability
        self.max_calls_per_day = max_calls_per_day
        self.calls_today = calls_today
        self.last_error: str | None = None
        self.disabled_reason: str | None = None
        self.clock = clock
        self.trend_gate = trend_gate
        self.cooldown_until = 0.0
        self.rate_strikes = 0
        self.prompt_version = prompt_version
        self.take_profit_atr_mult = take_profit_atr_mult
        self.cost_pct = cost_pct
        self.edge_margin = edge_margin
        self.max_hold_candles = max_hold_candles
        self.max_calls_per_hour = max_calls_per_hour
        self.recent_calls = recent_calls
        self.screener = screener
        self.context = context
        self.cooldown_get = cooldown_get  # pausa gravada no banco: a thread dos memes usa a mesma chave
        self.cooldown_set = cooldown_set
        self.allow_short = allow_short  # FUTURES: tendência de baixa vira candidata a short
        self.min_stop_pct = min_stop_pct  # mesmo piso do motor: a pergunta usa o stop/alvo que vai valer
        self.fade = entry_style == "fade"  # candidata = esticão da VWAP48 (backtest: a favor da EMA perde da moeda)
        self.fade_min_dev_pct = fade_min_dev_pct

    def _market(self, question: str) -> str:
        return question.replace("Spot crypto", FUTURES_MARKET) if self.allow_short else question

    def entry_threshold(self, f: Features) -> tuple[float, float, float]:
        sl, tp = exit_pcts(f.atr_pct, self.stop_atr_mult, self.take_profit_atr_mult, self.min_stop_pct)
        return tp, sl, min(0.95, breakeven_probability(tp, sl, self.cost_pct) + self.edge_margin)

    def _jev_blocked(self, f: Features, position: Position | None = None) -> str | None:
        """Motivo para não chamar o Jev agora (orçamento, reserva, pausa por 429, teto por hora)."""
        if self.max_calls_per_day > 0:  # 0 = ilimitado
            used = self.calls_today(utc_day(f.ts))
            if used >= self.max_calls_per_day:
                return "orçamento diário de chamadas esgotado"
            if position is None and used >= ENTRY_BUDGET_SHARE * self.max_calls_per_day:
                return "80% do orçamento do Jev usado: o resto fica para posições abertas"
        shared = self.cooldown_get() if self.cooldown_get is not None else 0.0
        wait = max(self.cooldown_until, shared) - self.clock()
        if wait > 0:  # sem chamar: cada tentativa durante o bloqueio consome a cota da chave
            return f"Jev em pausa pelo limite de requisições da API (volta em {wait / 60:.0f} min)"
        if self.max_calls_per_hour > 0 and self.recent_calls is not None:
            used = self.recent_calls(int((self.clock() - 3600) * 1000))
            if used >= self.max_calls_per_hour:  # teto nosso, abaixo do limite da chave: nunca chega ao 429
                return f"teto de {self.max_calls_per_hour} chamadas/hora ao Jev atingido, não consultado"
        return None

    def decide(self, f: Features, position: Position | None) -> Decision:
        if self.disabled_reason:
            return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, f"desativado: {self.disabled_reason}")
        if position is None and self.fade:
            dev = f.vwap_dev_pct
            short_entry = self.allow_short and dev >= self.fade_min_dev_pct
            if dev > -self.fade_min_dev_pct and not short_entry:
                # preço perto da média: nada para reverter, poupa o Grok e a cota do Jev
                return Decision(self.name, f.symbol, f.ts, Action.HOLD, None,
                                f"{GATED_PREFIX} preço a {dev:+.2f}% da VWAP48 (fade só a partir de "
                                f"±{self.fade_min_dev_pct:.1f}%), Jev não consultado")
        else:
            short_entry = position is None and self.allow_short and f.ema_fast < f.ema_slow
            if position is None and self.trend_gate and f.ema_fast <= f.ema_slow and not short_entry:
                # sem tendência de alta não há entrada a avaliar: poupa a chamada (e a cota da chave)
                return Decision(self.name, f.symbol, f.ts, Action.HOLD, None,
                                f"{GATED_PREFIX} sem tendência de alta (ema12≤ema26), Jev não consultado")
        grok: dict | None = None
        grok_score: float | None = None
        if self.screener is not None:
            # CHIEF (Grok) lê primeiro; o Jev só é chamado para o que ele escalar
            v = self.screener.review(f, position, short=short_entry)
            grok, grok_score = (v.raw or {"error": v.error}), v.score
            if not v.escalate:
                reason = v.summary if v.error else f"{GROK_PREFIX} {v.summary}"
                return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, reason, raw={"grok": grok})
        blocked = self._jev_blocked(f, position)
        if blocked:
            if grok is not None:
                blocked = f"Grok escalou ({grok.get('summary', '')}), mas {blocked}"
            return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, blocked,
                            raw={"grok": grok} if grok is not None else {})

        in_pos = position is not None
        short = short_entry or (in_pos and position.is_short)
        v2_entry = not in_pos and self.prompt_version == "v2"
        if in_pos and short:
            choices, question = ["BUY", "HOLD"], EXIT_SHORT_QUESTION.format(tf=self.timeframe)
        elif in_pos:
            choices, question = ["SELL", "HOLD"], self._market(EXIT_QUESTION.format(tf=self.timeframe))
        elif v2_entry:
            tp, sl, threshold = self.entry_threshold(f)
            choices = V2_CHOICES
            question = self._market((ENTRY_SHORT_QUESTION_V2 if short else ENTRY_QUESTION_V2).format(
                tf=self.timeframe, tp=tp, sl=sl, hold=self.max_hold_candles))
        elif short:
            choices = ["SELL", "HOLD"]
            question = ENTRY_SHORT_QUESTION.format(tf=self.timeframe, stop=self.stop_atr_mult)
        else:
            choices = ["BUY", "HOLD"]
            question = self._market(ENTRY_QUESTION.format(tf=self.timeframe, stop=self.stop_atr_mult))
        if self.fade:
            question += FADE_EXIT_NOTE if in_pos else FADE_ENTRY_NOTE.format(
                dev=abs(f.vwap_dev_pct), where="above" if short else "below")
        # chave determinística: após restart, o mesmo candle reaproveita o resultado e não cobra de novo
        key = f"{self.key_prefix}-{f.symbol.replace('/', '')}-{f.ts}-{'x' if in_pos else 'e'}{'s' if short else ''}"
        if v2_entry:
            key += "-v2"  # pergunta diferente: não pode reaproveitar a resposta da v1 do mesmo candle
        if self.fade:
            key += "-f"
        try:
            state = build_state(f, position, self.context() if self.context is not None else None)
            if grok_score is not None:
                state["grok_score"] = round(grok_score, 3)  # só o número: o texto do Grok nunca chega ao Jev
            res = self.client.run_choice(state, question, choices, key)
        except JevRateLimited as e:
            self.rate_strikes += 1
            pause = min(max(e.retry_after, COOLDOWN_MIN_S) * 2 ** (self.rate_strikes - 1), COOLDOWN_MAX_S)
            self.cooldown_until = self.clock() + pause
            if self.cooldown_set is not None:
                self.cooldown_set(self.cooldown_until)
            self.last_error = f"{e} (pausa de {pause / 60:.0f} min)"
            return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, f"erro Jev: {self.last_error}")
        except (JevAuthError, JevCreditsError) as e:
            self.disabled_reason = str(e)  # não adianta tentar de novo sem ação humana
            self.last_error = str(e)
            return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, f"erro Jev: {e}")
        except (JevError, ValueError) as e:
            self.last_error = str(e)
            return Decision(self.name, f.symbol, f.ts, Action.HOLD, None, f"erro Jev: {e}")

        self.last_error = None
        self.rate_strikes = 0
        chosen = str(res.decision)
        raw = {"probabilities": res.probabilities, "request_id": res.request_id, "latency_ms": res.latency_ms,
               "prompt": "v2" if v2_entry else "v1", "style": "fade" if self.fade else "trend"}
        if grok is not None:
            raw["grok"] = grok
        if v2_entry:
            p = res.probabilities.get("TARGET", 0.0)
            action = (Action.SELL if short else Action.BUY) if p >= threshold else Action.HOLD
            raw["threshold"] = round(threshold, 4)
            up, down = ("-", "+") if short else ("+", "-")
            reason = (f"v2 {'short ' if short else ''}alvo antes do stop p={p:.2f} (corte {threshold:.2f}: "
                      f"alvo {up}{tp:.2f}% stop {down}{sl:.2f}% custos {self.cost_pct:.2f}%) → {action.value}")
            return Decision(self.name, f.symbol, f.ts, action, p, reason, raw=raw, cost_credits=res.credits)
        p = res.probabilities.get(chosen, 0.0)
        action = Action(chosen)
        if not in_pos and action is not Action.HOLD and p < self.min_probability:  # só entradas pedem p mínima
            action = Action.HOLD
        reason = f"jev={chosen} p={p:.2f} conf={res.confidence} → {action.value}"
        return Decision(self.name, f.symbol, f.ts, action, res.confidence, reason, raw=raw, cost_credits=res.credits)
