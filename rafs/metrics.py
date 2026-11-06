"""Métricas de desempenho líquidas (após taxas e slippage)."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Trade


@dataclass(frozen=True)
class Performance:
    trades: int
    net_pnl: float
    return_pct: float
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    avg_win: float | None
    avg_loss: float | None
    fees: float
    max_drawdown: float
    max_drawdown_pct: float


def max_drawdown(curve: list[float]) -> tuple[float, float]:
    peak, mdd, mdd_pct = float("-inf"), 0.0, 0.0
    for v in curve:
        peak = max(peak, v)
        dd = peak - v
        if dd > mdd:
            mdd, mdd_pct = dd, dd / peak * 100 if peak > 0 else 0.0
    return mdd, mdd_pct


def performance(trades: list[Trade], initial: float, equity_curve: list[float] | None = None) -> Performance:
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    net = sum(t.pnl for t in trades)
    gross_win, gross_loss = sum(wins), -sum(losses)
    if equity_curve is None:
        equity_curve, eq = [initial], initial
        for t in sorted(trades, key=lambda t: t.exit_ts):
            eq += t.pnl
            equity_curve.append(eq)
    mdd, mdd_pct = max_drawdown(equity_curve) if equity_curve else (0.0, 0.0)
    n = len(trades)
    return Performance(
        trades=n,
        net_pnl=net,
        return_pct=net / initial * 100 if initial else 0.0,
        win_rate=len(wins) / n * 100 if n else None,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else (None if not wins else float("inf")),
        expectancy=net / n if n else None,
        avg_win=gross_win / len(wins) if wins else None,
        avg_loss=-gross_loss / len(losses) if losses else None,
        fees=sum(t.fees for t in trades),
        max_drawdown=mdd,
        max_drawdown_pct=mdd_pct,
    )


def _fmt(v: float | None, spec: str = ".2f", suffix: str = "") -> str:
    if v is None:
        return "—"
    if v == float("inf"):
        return "∞"
    return f"{v:{spec}}{suffix}"


def comparison_table(rows: dict[str, Performance]) -> str:
    header = f"{'decider':<10}{'trades':>7}{'líquido R$':>12}{'ret %':>8}{'win %':>8}{'PF':>7}{'exp R$':>9}{'taxas R$':>10}{'maxDD %':>9}"
    lines = [header, "-" * len(header)]
    for name, p in rows.items():
        lines.append(
            f"{name:<10}{p.trades:>7}{p.net_pnl:>12.2f}{p.return_pct:>8.2f}{_fmt(p.win_rate, '.1f'):>8}"
            f"{_fmt(p.profit_factor):>7}{_fmt(p.expectancy, '.3f'):>9}{p.fees:>10.2f}{p.max_drawdown_pct:>9.2f}"
        )
    return "\n".join(lines)
