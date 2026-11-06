"""Contrato comum de decisão. Deciders só DECIDEM: não enviam ordens nem mexem em limites."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Decision, Features, Position


class Decider(ABC):
    name: str

    @abstractmethod
    def decide(self, features: Features, position: Position | None) -> Decision:
        """Sem posição: BUY (long), SELL (short, só com allow_short) ou HOLD.
        Long aberto: SELL fecha ou HOLD. Short aberto: BUY recompra ou HOLD."""
