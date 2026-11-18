"""Record real CLI output into docs/demo.svg.

Run from the project root:  python scripts/make_demo.py

It runs the three example agents through the real CLI, then replays one
failing seed, and saves what the terminal showed.
"""

from __future__ import annotations

import io
import os
import shlex
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from agent_crashlab import cli

ROOT = Path(__file__).resolve().parents[1]


def run(console: Console, command: str) -> None:
    console.print(f"[bold green]$[/bold green] [bold]{command}[/bold]")
    result = CliRunner().invoke(cli.app, shlex.split(command)[1:])
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    console.print(f"[dim]exit status {result.exit_code}[/dim]")
    console.print()


def main() -> None:
    console = Console(record=True, width=100, file=io.StringIO(), force_terminal=True, highlight=False)
    cli.console = console
    os.chdir(ROOT)
    run(
        console,
        "agent-crashlab run examples/agents/naive_agent.py examples/agents/retry_agent.py examples/agents/careful_agent.py",
    )
    run(console, "agent-crashlab replay examples/agents/retry_agent.py --seed 3")
    (ROOT / "docs").mkdir(exist_ok=True)
    console.save_svg(str(ROOT / "docs" / "demo.svg"), title="agent-crashlab")
    print("wrote docs/demo.svg")


if __name__ == "__main__":
    main()
