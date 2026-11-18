"""Read crashlab.yaml, the settings file for a CI run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FILE = "crashlab.yaml"

KEYS = {"agents", "agent", "scenario", "runs", "seed", "profile", "faults", "max_restarts", "min_score", "reports"}


class ConfigError(ValueError):
    """crashlab.yaml has an unknown key or a bad value."""


@dataclass
class Config:
    agents: list[str] = field(default_factory=list)
    scenario: str = "month-end"
    runs: int = 200
    seed: int = 1
    profile: str = "default"
    faults: dict[str, float] = field(default_factory=dict)
    max_restarts: int = 3
    min_score: float | None = None
    reports: str | None = None
    base: Path = field(default_factory=Path.cwd)

    def resolve(self, value: str) -> str:
        """Resolve a path in the config relative to the config file's folder."""
        p = Path(value)
        if p.is_absolute() or not (self.base / p).exists():
            return value
        return str(self.base / p)


def _int(data: dict, key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"'{key}' must be a whole number of at least {minimum}, got {value!r}")
    return value


def load_config(path: str | Path) -> Config:
    p = Path(path)
    try:
        data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: expected a mapping at the top level")
    unknown = sorted(set(data) - KEYS)
    if unknown:
        raise ConfigError(f"{p}: unknown key(s) {', '.join(unknown)}; known keys: {', '.join(sorted(KEYS))}")
    agents = data.get("agents", data.get("agent", []))
    if isinstance(agents, str):
        agents = [agents]
    if not isinstance(agents, list) or not all(isinstance(a, str) for a in agents):
        raise ConfigError("'agents' must be a list of file.py:function or module:function strings")
    faults = data.get("faults") or {}
    if not isinstance(faults, dict):
        raise ConfigError("'faults' must be a mapping of fault name to rate")
    min_score = data.get("min_score")
    if min_score is not None and (
        not isinstance(min_score, int | float) or isinstance(min_score, bool) or not 0 <= min_score <= 1
    ):
        raise ConfigError(f"'min_score' must be between 0 and 1, got {min_score!r}")
    return Config(
        agents=agents,
        scenario=str(data.get("scenario", "month-end")),
        runs=_int(data, "runs", 200, 1),
        seed=_int(data, "seed", 1, 0),
        profile=str(data.get("profile", "default")),
        faults={str(k): v for k, v in faults.items()},
        max_restarts=_int(data, "max_restarts", 3, 0),
        min_score=float(min_score) if min_score is not None else None,
        reports=str(data["reports"]) if data.get("reports") else None,
        base=p.resolve().parent,
    )
