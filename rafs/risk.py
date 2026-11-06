"""RISK — motor determinístico com poder de veto. Nenhum decider (IA ou não) consegue desligá-lo."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .portfolio import Book, utc_day

MAX_SPREAD_PCT = 0.5
MAX_CONSECUTIVE_ERRORS = 5


@dataclass(frozen=True)
class RiskVerdict:
    allowed: bool
    reasons: tuple[str, ...]


class RiskEngine:
    def __init__(self, settings: Settings, kill_switch_check=None):
        self.s = settings
        self.consecutive_errors = 0
        self._kill_check = kill_switch_check or (lambda: settings.kill_switch_path.exists())

    # --- circuit breaker ---
    def record_error(self) -> None:
        self.consecutive_errors += 1

    def record_success(self) -> None:
        self.consecutive_errors = 0

    @property
    def circuit_open(self) -> bool:
        return self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS

    def kill_switch_active(self) -> bool:
        return bool(self._kill_check())

    def check_entry(self, book: Book, symbol: str, notional: float, now_ms: int,
                    spread_pct: float | None, paused: bool, prices: dict[str, float] | None = None) -> RiskVerdict:
        reasons: list[str] = []
        if self.kill_switch_active():
            reasons.append("kill switch ativo")
        if paused:
            reasons.append("entradas pausadas")
        if self.circuit_open:
            reasons.append(f"circuit breaker ({self.consecutive_errors} erros seguidos)")
        if notional <= 0:
            reasons.append("tamanho zero")
        if notional > self.s.entry_brl + 1e-9:
            reasons.append(f"entrada {notional:.2f} acima do máximo {self.s.entry_brl:.2f}")
        if symbol in book.positions:
            reasons.append(f"já existe posição em {symbol}")
        if book.exposure() + notional > self.s.max_exposure_brl + 1e-9:
            reasons.append(f"exposição {book.exposure() + notional:.2f} > {self.s.max_exposure_brl:.2f}")
        if notional > book.cash:
            reasons.append("saldo insuficiente")
        day_loss = book.realized_on(utc_day(now_ms))
        if prices:
            day_loss += min(0.0, book.unrealized(prices))
        if day_loss <= -self.s.daily_loss_limit_brl:
            reasons.append(f"limite diário de perda atingido ({day_loss:.2f})")
        if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
            reasons.append(f"spread {spread_pct:.2f}% > {MAX_SPREAD_PCT}%")
        return RiskVerdict(not reasons, tuple(reasons))
