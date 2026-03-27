"""Shared types for all agents."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class AgentResult:
    agent_name: str
    score: float  # -100 to +100
    confidence: float  # 0.0 to 1.0
    signals: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
