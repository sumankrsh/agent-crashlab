"""agent-crashlab: fault injection for AI agents, judged by their side effects."""

from .errors import AgentCrashed, ToolConflict, ToolError, ToolFailure, ToolTimeout
from .faults import FaultProfile
from .harness import Memory, Tools, run_once
from .scoring import evaluate, judge
from .world import Scenario, Task, load_scenario

__version__ = "0.1.0"

__all__ = [
    "AgentCrashed",
    "FaultProfile",
    "Memory",
    "Scenario",
    "Task",
    "ToolConflict",
    "ToolError",
    "ToolFailure",
    "ToolTimeout",
    "Tools",
    "evaluate",
    "judge",
    "load_scenario",
    "run_once",
    "__version__",
]
