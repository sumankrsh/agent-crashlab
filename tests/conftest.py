from pathlib import Path

import pytest

from agent_crashlab import load_scenario
from agent_crashlab.loader import load_agent

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "examples" / "agents"


@pytest.fixture
def scenario():
    return load_scenario("month-end")


@pytest.fixture
def agents():
    return {name: load_agent(str(AGENTS / f"{name}.py"))[0] for name in ("naive_agent", "retry_agent", "careful_agent")}


class ScriptedPlan:
    """A fault plan that injects a fixed fault at chosen call numbers."""

    def __init__(self, faults: dict[int, str]):
        self.faults = faults
        self.n = 0

    def draw(self, kind):
        fault = self.faults.get(self.n)
        self.n += 1
        return fault
