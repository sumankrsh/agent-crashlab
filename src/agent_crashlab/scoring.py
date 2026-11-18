"""Judge each run by its recorded side effects, then score many seeded runs."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .faults import FaultProfile
from .harness import Agent, RunRecord, run_once
from .world import Scenario

INVARIANTS: dict[str, str] = {
    "finished": "The agent ran to the end (after any restarts) without giving up.",
    "complete": "Every expected side effect happened.",
    "no_duplicates": "No side effect happened more than once.",
    "no_unexpected": "No side effect happened that the task did not ask for.",
}


@dataclass
class Violation:
    invariant: str
    detail: str
    cause: str  # the fault that most likely led to it, or "agent logic"

    def as_dict(self) -> dict[str, str]:
        return {"invariant": self.invariant, "detail": self.detail, "cause": self.cause}


@dataclass
class RunResult:
    record: RunRecord
    violations: list[Violation]
    overpaid: float

    @property
    def passed(self) -> bool:
        return not self.violations

    @property
    def faults(self) -> Counter:
        return Counter(c.fault for c in self.record.calls if c.fault)

    @property
    def retries(self) -> int:
        """Calls that repeated an earlier call to the same tool with the same arguments."""
        seen: set[str] = set()
        repeats = 0
        for c in self.record.calls:
            sig = f"{c.tool}:{sorted(c.args.items())!r}"
            if sig in seen:
                repeats += 1
            seen.add(sig)
        return repeats


def _call_fault(record: RunRecord, index: int) -> str | None:
    return record.calls[index].fault if 0 <= index < len(record.calls) else None


def judge(record: RunRecord, scenario: Scenario) -> RunResult:
    expected = scenario.expected()
    by_key: dict[str, list] = {}
    for effect in record.effects:
        by_key.setdefault(effect.key, []).append(effect)
    violations: list[Violation] = []
    overpaid = 0.0

    if record.outcome != "finished":
        last = record.calls[-1] if record.calls else None
        if record.outcome == "agent_error":
            cause = last.fault if last and last.fault else "agent logic"
        elif record.outcome == "crash_budget":
            cause = "crash"
        else:
            cause = "agent logic"
        violations.append(Violation("finished", record.error, cause))

    partial_seen = any(c.fault == "partial" for c in record.calls)
    for key in expected:
        if key not in by_key:
            if record.outcome != "finished":
                cause = violations[0].cause
            else:
                cause = "partial" if partial_seen else "agent logic"
            violations.append(Violation("complete", f"{_label(key)} never happened", cause))

    for key, effects in by_key.items():
        if len(effects) > 1:
            first = effects[0]
            if effects[1].delivery == 2 and effects[1].call == first.call:
                cause = "duplicate"
            else:
                cause = _call_fault(record, first.call) or ""
                if cause not in ("lost_reply", "crash", "duplicate"):
                    processes = {record.calls[e.call].process for e in effects if e.call < len(record.calls)}
                    cause = "crash" if len(processes) > 1 else "agent logic"
            detail = f"{_label(key)} happened {len(effects)} times"
            if key.startswith("pay:"):
                extra = sum(e.args["amount"] for e in effects[1:])
                overpaid += extra
                detail += f" ({extra:,.2f} paid twice)" if len(effects) == 2 else f" ({extra:,.2f} overpaid)"
            violations.append(Violation("no_duplicates", detail, cause))

    for key, effects in by_key.items():
        want = expected.get(key)
        first = effects[0]
        if want is None:
            violations.append(Violation("no_unexpected", f"{_label(key)} was not part of the task", "agent logic"))
        elif key.startswith("pay:") and abs(first.args["amount"] - want["amount"]) > 0.005:
            violations.append(
                Violation(
                    "no_unexpected",
                    f"{_label(key)} for {first.args['amount']:,.2f} instead of {want['amount']:,.2f}",
                    "agent logic",
                )
            )
        elif key.startswith("email:") and first.args["to"] != want["to"]:
            violations.append(
                Violation(
                    "no_unexpected", f"{_label(key)} sent to {first.args['to']} instead of {want['to']}", "agent logic"
                )
            )
    return RunResult(record=record, violations=violations, overpaid=round(overpaid, 2))


def _label(key: str) -> str:
    kind, _, rest = key.partition(":")
    if kind == "pay":
        return f"payment for {rest}"
    if kind == "email":
        return f"remittance email for {rest}"
    return key


FAULT_WORDS = {
    "timeout": "a timeout",
    "lost_reply": "a lost reply",
    "error": "a transient error",
    "duplicate": "a duplicated request",
    "partial": "a partial page",
    "crash": "a crash",
}


def reason(v: Violation) -> str:
    """A short plain-English reason for a violation, used to group failures."""
    if v.invariant == "no_duplicates":
        return {
            "lost_reply": "Did it twice: retried after a lost reply",
            "duplicate": "Did it twice: a request arrived twice",
            "crash": "Did it twice: the restart redid work",
        }.get(v.cause, "Did it twice: the agent repeated an action")
    if v.invariant == "no_unexpected":
        return "Did something it was not asked to do"
    if v.invariant == "complete" and v.cause == "partial":
        return "Skipped work: trusted a partial page"
    if v.cause == "crash":
        return "Stopped: kept crashing past the restart limit"
    if v.cause in FAULT_WORDS and v.cause != "partial":
        return f"Stopped: gave up after {FAULT_WORDS[v.cause]}"
    if v.invariant == "finished":
        return "Stopped: the agent raised an error"
    return "Skipped work: some expected actions never happened"


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a pass rate."""
    if n == 0:
        return (0.0, 0.0)
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Summary:
    agent: str
    scenario: str
    profile: FaultProfile
    seed: int
    baseline: RunResult
    results: list[RunResult] = field(default_factory=list)

    @property
    def runs(self) -> int:
        return len(self.results)

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def score(self) -> float:
        return self.passes / self.runs if self.runs else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.passes, self.runs)

    def invariant_rates(self) -> dict[str, float]:
        out = {}
        for name in INVARIANTS:
            ok = sum(1 for r in self.results if not any(v.invariant == name for v in r.violations))
            out[name] = ok / self.runs if self.runs else 0.0
        return out

    def causes(self) -> list[tuple[str, str, int]]:
        """(invariant, cause, number of runs) for every kind of failure seen, most common first."""
        counter: Counter = Counter()
        for r in self.results:
            for pair in {(v.invariant, v.cause) for v in r.violations}:
                counter[pair] += 1
        return [(inv, cause, n) for (inv, cause), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    def reasons(self) -> list[tuple[str, int, int]]:
        """(reason, number of runs, first seed) for each kind of failure, most common first."""
        counter: Counter = Counter()
        first: dict[str, int] = {}
        for r in self.results:
            for text in {reason(v) for v in r.violations}:
                counter[text] += 1
                first.setdefault(text, r.record.seed)
        return [(t, n, first[t]) for t, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]

    def faults_injected(self) -> Counter:
        total: Counter = Counter()
        for r in self.results:
            total.update(r.faults)
        return total

    @property
    def duplicate_effects(self) -> int:
        return sum(sum(1 for v in r.violations if v.invariant == "no_duplicates") for r in self.results)

    @property
    def overpaid(self) -> float:
        return round(sum(r.overpaid for r in self.results), 2)

    @property
    def runs_overpaid(self) -> int:
        return sum(1 for r in self.results if r.overpaid > 0)

    @property
    def crashed_runs(self) -> int:
        return sum(1 for r in self.results if r.record.restarts > 0)

    @property
    def resumed_cleanly(self) -> int:
        return sum(1 for r in self.results if r.record.restarts > 0 and r.passed)

    @property
    def failing_seeds(self) -> list[int]:
        return [r.record.seed for r in self.results if not r.passed]

    def as_dict(self) -> dict[str, Any]:
        lo, hi = self.interval
        return {
            "agent": self.agent,
            "scenario": self.scenario,
            "profile": {"name": self.profile.name, "rates": self.profile.rates},
            "seed": self.seed,
            "runs": self.runs,
            "passed": self.passes,
            "score": round(self.score, 4),
            "interval_95": [round(lo, 4), round(hi, 4)],
            "baseline_passed": self.baseline.passed,
            "invariants": {k: round(v, 4) for k, v in self.invariant_rates().items()},
            "causes": [{"invariant": i, "cause": c, "runs": n} for i, c, n in self.causes()],
            "reasons": [{"reason": t, "runs": n, "example_seed": s} for t, n, s in self.reasons()],
            "faults_injected": dict(sorted(self.faults_injected().items())),
            "duplicate_effects": self.duplicate_effects,
            "overpaid": self.overpaid,
            "crashed_runs": self.crashed_runs,
            "resumed_cleanly": self.resumed_cleanly,
            "failing_seeds": self.failing_seeds,
            "results": [
                {
                    "seed": r.record.seed,
                    "passed": r.passed,
                    "outcome": r.record.outcome,
                    "restarts": r.record.restarts,
                    "calls": len(r.record.calls),
                    "retries": r.retries,
                    "faults": dict(sorted(r.faults.items())),
                    "violations": [v.as_dict() for v in r.violations],
                }
                for r in self.results
            ],
        }


def evaluate(
    agent: Agent,
    agent_name: str,
    scenario: Scenario,
    profile: FaultProfile,
    runs: int = 200,
    seed: int = 1,
    max_restarts: int = 3,
    max_calls: int = 400,
) -> Summary:
    """Run a fault-free baseline, then ``runs`` seeded runs (seeds seed, seed+1, ...)."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    baseline = judge(
        run_once(agent, scenario, FaultProfile(name="none"), seed=seed, max_restarts=max_restarts, max_calls=max_calls),
        scenario,
    )
    summary = Summary(agent=agent_name, scenario=scenario.name, profile=profile, seed=seed, baseline=baseline)
    for s in range(seed, seed + runs):
        record = run_once(agent, scenario, profile, seed=s, max_restarts=max_restarts, max_calls=max_calls)
        summary.results.append(judge(record, scenario))
    return summary
