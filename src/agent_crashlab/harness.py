"""Run an agent against the sandbox with faults injected, and record everything."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import AgentCrashed, CallBudgetExceeded, ToolError, ToolTimeout
from .faults import FaultPlan, FaultProfile
from .world import READ, TOOLS, Effect, PayablesBackend, Scenario, Task

Agent = Callable[[Task, "Tools", "Memory"], Any]


class Memory(dict):
    """Durable storage that survives a crash.

    Anything the agent stores here (a checkpoint, the invoices it has already
    paid) is still here when the run restarts after a crash. Local variables
    and objects created during the crashed attempt are gone.
    """


@dataclass
class Call:
    index: int
    process: int  # 0 for the first attempt, 1 after the first restart, ...
    tool: str
    args: dict[str, Any]
    fault: str | None
    outcome: str  # ok, timeout, error, crash, rejected
    detail: str = ""
    effects: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "process": self.process,
            "tool": self.tool,
            "args": self.args,
            "fault": self.fault,
            "outcome": self.outcome,
            "detail": self.detail,
            "effects": self.effects,
        }


class Tools:
    """What the agent calls. ``tools.pay_invoice(...)`` and ``tools.call("pay_invoice", ...)`` both work."""

    def __init__(self, backend: PayablesBackend, plan: FaultPlan, max_calls: int = 400):
        self._backend = backend
        self._plan = plan
        self._max_calls = max_calls
        self.calls: list[Call] = []
        self.process = 0
        self.waited = 0.0

    @property
    def names(self) -> list[str]:
        return list(TOOLS)

    def sleep(self, seconds: float) -> None:
        """Back off before a retry. Time is simulated, so runs stay fast."""
        if seconds > 0:
            self.waited += float(seconds)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or name not in TOOLS:
            raise AttributeError(name)
        return lambda **kwargs: self.call(name, **kwargs)

    def call(self, tool: str, **args: Any) -> Any:
        if tool not in TOOLS:
            raise ToolError(tool, "no such tool", transient=False)
        if len(self.calls) >= self._max_calls:
            raise CallBudgetExceeded(f"more than {self._max_calls} tool calls")
        kind = TOOLS[tool][0]
        fault = self._plan.draw(kind)
        record = Call(
            index=len(self.calls), process=self.process, tool=tool, args=copy.deepcopy(args), fault=fault, outcome="ok"
        )
        self.calls.append(record)

        if fault == "timeout":
            record.outcome = "timeout"
            raise ToolTimeout(tool, "timed out")
        if fault == "error":
            record.outcome = "error"
            raise ToolError(tool, "503 service unavailable", transient=True)

        before = len(self._backend.effects)
        deduped = self._backend.deduplicated
        try:
            result = self._backend.call(tool, args, call=record.index)
            if fault == "duplicate":
                self._backend.call(tool, args, call=record.index, delivery=2)
        except ToolError as exc:
            record.outcome = "rejected"
            record.detail = str(exc)
            record.effects = [e.key for e in self._backend.effects[before:]]
            raise
        except TypeError as exc:
            record.outcome = "rejected"
            record.detail = f"bad arguments: {exc}"
            raise ToolError(tool, f"bad arguments: {exc}", transient=False) from None
        record.effects = [e.key for e in self._backend.effects[before:]]
        if self._backend.deduplicated > deduped:
            record.detail = "deduplicated by idempotency key"

        if fault == "lost_reply":
            record.outcome = "timeout"
            raise ToolTimeout(tool, "timed out")
        if fault == "crash":
            record.outcome = "crash"
            raise AgentCrashed(f"process died during {tool}")
        if fault == "partial" and kind == READ and isinstance(result, dict) and "items" in result:
            items = result["items"]
            result = dict(result, items=items[: len(items) // 2], partial=True)
            record.detail = f"returned {len(result['items'])} of {len(items)} items"
        return copy.deepcopy(result)


@dataclass
class RunRecord:
    seed: int
    outcome: str  # finished, agent_error, crash_budget, call_budget
    error: str
    restarts: int
    calls: list[Call]
    effects: list[Effect]
    waited: float


def run_once(
    agent: Agent,
    scenario: Scenario,
    profile: FaultProfile,
    seed: int,
    max_restarts: int = 3,
    max_crashes: int = 1,
    max_calls: int = 400,
) -> RunRecord:
    """Run the agent once. A crash restarts it with the same memory and backend."""
    backend = scenario.new_backend()
    plan = FaultPlan(profile, seed, max_crashes=max_crashes)
    tools = Tools(backend, plan, max_calls=max_calls)
    memory = Memory()
    task = scenario.task_for_agent()
    outcome, error = "finished", ""
    while True:
        try:
            agent(task, tools, memory)
            break
        except AgentCrashed:
            if tools.process >= max_restarts:
                outcome, error = "crash_budget", f"still crashing after {max_restarts} restarts"
                break
            tools.process += 1
        except CallBudgetExceeded as exc:
            outcome, error = "call_budget", str(exc)
            break
        except Exception as exc:  # the agent gave up or has a bug
            outcome, error = "agent_error", f"{type(exc).__name__}: {exc}"
            break
    return RunRecord(
        seed=seed,
        outcome=outcome,
        error=error,
        restarts=tools.process,
        calls=tools.calls,
        effects=list(backend.effects),
        waited=tools.waited,
    )
