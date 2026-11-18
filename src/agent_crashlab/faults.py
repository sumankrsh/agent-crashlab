"""Fault types, fault profiles and the seeded plan that decides each call's fault."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# name -> (applies to reads, applies to writes, description)
FAULTS: dict[str, tuple[bool, bool, str]] = {
    "timeout": (
        True,
        True,
        "The call times out before it reaches the backend. Nothing happens; the agent gets ToolTimeout.",
    ),
    "lost_reply": (
        False,
        True,
        "The backend applies the effect, then the reply is lost. The agent gets ToolTimeout anyway.",
    ),
    "error": (
        True,
        True,
        "The backend returns a transient error (503) before doing anything. The agent gets ToolError.",
    ),
    "duplicate": (
        False,
        True,
        "The request is delivered to the backend twice (at-least-once delivery). The agent sees one success.",
    ),
    "partial": (
        True,
        False,
        "A list call returns only part of the page, marked partial: true. The cursor still points past the page.",
    ),
    "crash": (
        False,
        True,
        "The agent process dies right after the backend applies the effect. The run restarts with its memory.",
    ),
}

PROFILES: dict[str, dict[str, float]] = {
    "none": {},
    "default": {
        "timeout": 0.04,
        "lost_reply": 0.04,
        "error": 0.05,
        "duplicate": 0.03,
        "partial": 0.10,
        "crash": 0.03,
    },
    "flaky-network": {"timeout": 0.10, "lost_reply": 0.08, "error": 0.10, "partial": 0.05},
    "crashy": {"crash": 0.12, "lost_reply": 0.03},
}


class ProfileError(ValueError):
    """A fault profile has an unknown fault name or a bad rate."""


@dataclass(frozen=True)
class FaultProfile:
    """Per-call probability of each fault. Reads and writes are drawn separately."""

    rates: dict[str, float] = field(default_factory=dict)
    name: str = "custom"

    def __post_init__(self) -> None:
        for fault, rate in self.rates.items():
            if fault not in FAULTS:
                raise ProfileError(f"unknown fault {fault!r}; known faults: {', '.join(FAULTS)}")
            if not isinstance(rate, int | float) or isinstance(rate, bool) or not 0 <= rate <= 1:
                raise ProfileError(f"rate for {fault!r} must be a number between 0 and 1, got {rate!r}")
        for kind in ("read", "write"):
            total = sum(self.rates.get(f, 0.0) for f in self.applicable(kind))
            if total > 0.9:
                raise ProfileError(f"{kind} fault rates add up to {total:.2f}; keep the total at or under 0.9")

    @staticmethod
    def applicable(kind: str) -> list[str]:
        index = 0 if kind == "read" else 1
        return [name for name, spec in FAULTS.items() if spec[index]]

    @classmethod
    def named(cls, name: str, overrides: dict[str, float] | None = None) -> FaultProfile:
        if name not in PROFILES:
            raise ProfileError(f"unknown profile {name!r}; known profiles: {', '.join(PROFILES)}")
        rates = dict(PROFILES[name])
        rates.update(overrides or {})
        return cls(rates={k: float(v) for k, v in rates.items() if v}, name=name if not overrides else f"{name}+")

    def describe(self) -> str:
        if not self.rates:
            return "no faults"
        return ", ".join(f"{k} {v:.0%}" for k, v in sorted(self.rates.items()))


class FaultPlan:
    """Decides, call by call, which fault (if any) to inject.

    The plan is a pure function of the seed and the order of calls, so a run
    with the same agent, scenario, profile and seed is repeated exactly.
    """

    def __init__(self, profile: FaultProfile, seed: int, max_crashes: int = 1):
        self.profile = profile
        self.seed = seed
        self.rng = random.Random(seed)
        self.max_crashes = max_crashes
        self.crashes = 0

    def draw(self, kind: str) -> str | None:
        roll = self.rng.random()
        edge = 0.0
        for fault in FaultProfile.applicable(kind):
            edge += self.profile.rates.get(fault, 0.0)
            if roll < edge:
                if fault == "crash":
                    if self.crashes >= self.max_crashes:
                        return None
                    self.crashes += 1
                return fault
        return None


def parse_overrides(items: list[str]) -> dict[str, float]:
    """Parse ``--fault name=rate`` options."""
    out: dict[str, float] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep:
            raise ProfileError(f"expected NAME=RATE, got {item!r}")
        try:
            out[name.strip()] = float(value)
        except ValueError as exc:
            raise ProfileError(f"rate for {name!r} must be a number, got {value!r}") from exc
    return out
