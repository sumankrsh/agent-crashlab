"""Load the agent under test from ``path/to/file.py:function`` or ``package.module:function``."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any


class AgentLoadError(ValueError):
    """The agent spec doesn't point at a callable."""


def load_agent(spec: str, base: Path | None = None) -> tuple[Any, str]:
    """Return (callable, display name). The function defaults to ``run``."""
    target, sep, func = spec.rpartition(":")
    if not sep or (len(target) == 1 and target.isalpha()):  # no ":" or a Windows drive letter
        target, func = spec, "run"
    func = func or "run"
    if target.endswith(".py"):
        path = Path(target)
        if not path.is_absolute() and base is not None:
            path = base / path
        if not path.is_file():
            raise AgentLoadError(f"agent file not found: {target}")
        name = f"_crashlab_agent_{abs(hash(str(path.resolve())))}"
        module_spec = importlib.util.spec_from_file_location(name, path)
        if module_spec is None or module_spec.loader is None:
            raise AgentLoadError(f"can't import {target}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        try:
            module_spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[name]
            raise AgentLoadError(f"importing {target} failed: {type(exc).__name__}: {exc}") from exc
        label = path.stem
    else:
        try:
            module = importlib.import_module(target)
        except Exception as exc:
            raise AgentLoadError(f"can't import module {target!r}: {exc}") from exc
        label = target.rsplit(".", 1)[-1]
    fn = getattr(module, func, None)
    if not callable(fn):
        raise AgentLoadError(f"{target} has no function {func!r}")
    return fn, label
