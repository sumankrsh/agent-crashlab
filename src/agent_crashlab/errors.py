"""Exceptions an agent sees when a tool call fails.

Agents under test import these to decide what to retry:

    from agent_crashlab import ToolTimeout, ToolError
"""

from __future__ import annotations


class ToolFailure(Exception):
    """Base class for every failure a tool call can raise."""

    def __init__(self, tool: str, message: str):
        super().__init__(f"{tool}: {message}")
        self.tool = tool


class ToolTimeout(ToolFailure):
    """The call did not answer in time.

    The agent cannot tell whether the request reached the backend. Sometimes it
    did and the effect happened (a lost reply); sometimes it did not.
    """


class ToolError(ToolFailure):
    """The backend answered with an error.

    ``transient`` is True for errors worth retrying (503, 429) and False for
    errors that will not go away (unknown invoice, bad arguments).
    """

    def __init__(self, tool: str, message: str, transient: bool = True):
        super().__init__(tool, message)
        self.transient = transient


class ToolConflict(ToolError):
    """An idempotency key was reused with different arguments."""

    def __init__(self, tool: str, message: str):
        super().__init__(tool, message, transient=False)


class AgentCrashed(BaseException):
    """Raised inside a tool call to simulate the agent's process dying.

    It derives from BaseException so that an agent's ``except Exception``
    blocks do not swallow it, the same way a real crash can't be caught.
    """


class CallBudgetExceeded(BaseException):
    """The agent made more tool calls than the run allows (a likely loop)."""
