"""Command line: agent-crashlab run | replay | faults | tools."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, reports
from .config import DEFAULT_FILE, Config, ConfigError, load_config
from .faults import FAULTS, PROFILES, FaultProfile, ProfileError, parse_overrides
from .harness import run_once
from .loader import AgentLoadError, load_agent
from .scoring import INVARIANTS, evaluate, judge
from .world import TOOLS, ScenarioError, builtin_scenarios, load_scenario

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Inject faults into an AI agent's tools and check that it never does anything twice.",
)
console = Console(highlight=False)


def fail(message: str) -> None:
    console.print(f"[red]error:[/red] {message}", soft_wrap=True)
    raise typer.Exit(2)


def version_callback(value: bool) -> None:
    if value:
        console.print(f"agent-crashlab {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    pass


def _profile(name: str, faults: dict[str, float]) -> FaultProfile:
    try:
        return FaultProfile.named(name, faults)
    except ProfileError as exc:
        fail(str(exc))
        raise  # unreachable


@app.command()
def run(
    agents: list[str] = typer.Argument(None, help="Agents to test, as file.py[:function] or module:function."),
    config: Path = typer.Option(None, "--config", "-c", help=f"Settings file (default: {DEFAULT_FILE} if present)."),
    scenario: str = typer.Option(None, "--scenario", "-s", help="Scenario YAML file or built-in name."),
    runs: int = typer.Option(None, "--runs", "-n", help="Seeded runs per agent (default 200)."),
    seed: int = typer.Option(None, "--seed", help="First seed (default 1)."),
    profile: str = typer.Option(None, "--profile", "-p", help="Fault profile: " + ", ".join(PROFILES) + "."),
    fault: list[str] = typer.Option([], "--fault", "-f", help="Override one fault rate, e.g. --fault crash=0.1."),
    max_restarts: int = typer.Option(None, "--max-restarts", help="Restarts allowed after crashes (default 3)."),
    min_score: float = typer.Option(None, "--min-score", help="Exit 1 if any agent's reliability is below this (0-1)."),
    reports_dir: Path = typer.Option(None, "--reports", help="Write report.json, report.md and report.html here."),
    json_out: Path = typer.Option(None, "--json", help="Write the JSON report to this file."),
    markdown_out: Path = typer.Option(None, "--markdown", help="Write the Markdown report to this file."),
    html_out: Path = typer.Option(None, "--html", help="Write the HTML report to this file."),
) -> None:
    """Run each agent many times under seeded faults and score how often it stays correct."""
    cfg = Config()
    cfg_path = config or (Path(DEFAULT_FILE) if not agents and Path(DEFAULT_FILE).is_file() else None)
    if cfg_path is not None:
        try:
            cfg = load_config(cfg_path)
        except ConfigError as exc:
            fail(str(exc))
    agent_specs = list(agents) if agents else [_resolve_spec(cfg, a) for a in cfg.agents]
    if not agent_specs:
        fail(
            f"name at least one agent, e.g. agent-crashlab run examples/agents/careful_agent.py, or add one to {DEFAULT_FILE}"
        )
    try:
        overrides = {**cfg.faults, **parse_overrides(fault)}
    except ProfileError as exc:
        fail(str(exc))
    prof = _profile(profile or cfg.profile, overrides)
    scenario_ref = scenario or (cfg.resolve(cfg.scenario) if cfg_path else cfg.scenario)
    try:
        scen = load_scenario(scenario_ref)
    except ScenarioError as exc:
        fail(str(exc))
    n = runs if runs is not None else cfg.runs
    first = seed if seed is not None else cfg.seed
    restarts = max_restarts if max_restarts is not None else cfg.max_restarts
    threshold = min_score if min_score is not None else cfg.min_score
    if n < 1:
        fail("--runs must be at least 1")
    if threshold is not None and not 0 <= threshold <= 1:
        fail("--min-score must be between 0 and 1")

    loaded = []
    for spec in agent_specs:
        try:
            loaded.append(load_agent(spec))
        except AgentLoadError as exc:
            fail(str(exc))
    summaries = [evaluate(fn, name, scen, prof, runs=n, seed=first, max_restarts=restarts) for fn, name in loaded]
    shown = [s if not Path(s.split(":")[0]).is_absolute() else _relative(s) for s in agent_specs]
    reports.print_summaries(console, scen, summaries, threshold, shown)

    out_dir = reports_dir or (Path(cfg.resolve(cfg.reports)) if cfg_path and cfg.reports else None)
    targets: list[tuple[Path, str]] = []
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        targets += [(out_dir / "report.json", "json"), (out_dir / "report.md", "md"), (out_dir / "report.html", "html")]
    targets += [(p, k) for p, k in ((json_out, "json"), (markdown_out, "md"), (html_out, "html")) if p is not None]
    for path, kind in targets:
        if kind == "json":
            text = reports.to_json(scen, summaries, threshold)
        elif kind == "md":
            text = reports.to_markdown(scen, summaries, threshold, shown)
        else:
            text = reports.to_html(scen, summaries, threshold, shown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if targets:
        console.print("[dim]Wrote " + ", ".join(str(p) for p, _ in targets) + "[/dim]")

    if any(not reports.verdict(s, threshold)[0] for s in summaries):
        raise typer.Exit(1)


def _resolve_spec(cfg: Config, spec: str) -> str:
    target, sep, func = spec.rpartition(":")
    if not sep:
        target, func = spec, ""
    if not target.endswith(".py"):
        return spec
    resolved = cfg.resolve(target)
    return f"{resolved}:{func}" if func else resolved


def _relative(spec: str) -> str:
    target, sep, func = spec.rpartition(":")
    path = Path(target if sep else spec)
    try:
        rel = str(path.relative_to(Path.cwd()))
    except ValueError:
        rel = str(path)
    return f"{rel}:{func}" if sep else rel


@app.command()
def replay(
    agent: str = typer.Argument(..., help="Agent as file.py[:function] or module:function."),
    seed: int = typer.Option(..., "--seed", help="The seed to replay."),
    scenario: str = typer.Option("month-end", "--scenario", "-s", help="Scenario YAML file or built-in name."),
    profile: str = typer.Option("default", "--profile", "-p", help="Fault profile: " + ", ".join(PROFILES) + "."),
    fault: list[str] = typer.Option([], "--fault", "-f", help="Override one fault rate, e.g. --fault crash=0.1."),
    max_restarts: int = typer.Option(3, "--max-restarts", help="Restarts allowed after crashes."),
) -> None:
    """Replay one seeded run call by call, showing every fault and side effect."""
    try:
        overrides = parse_overrides(fault)
    except ProfileError as exc:
        fail(str(exc))
    prof = _profile(profile, overrides)
    try:
        scen = load_scenario(scenario)
        fn, _ = load_agent(agent)
    except (ScenarioError, AgentLoadError) as exc:
        fail(str(exc))
    result = judge(run_once(fn, scen, prof, seed=seed, max_restarts=max_restarts), scen)
    reports.print_replay(console, scen, result, f"faults: {prof.name}")
    if not result.passed:
        raise typer.Exit(1)


@app.command("faults")
def list_faults() -> None:
    """List the fault types, the built-in profiles and the invariants each run is judged by."""
    table = Table(show_edge=False, pad_edge=False, header_style="bold")
    table.add_column("Fault")
    table.add_column("Hits")
    table.add_column("What happens")
    for name, (reads, writes, text) in FAULTS.items():
        hits = " and ".join(k for k, on in (("reads", reads), ("writes", writes)) if on)
        table.add_row(name, hits, text)
    console.print(table)
    console.print()
    for name, rates in PROFILES.items():
        console.print(f"[bold]{name}[/bold]: {FaultProfile(rates=rates).describe()}")
    console.print()
    for name, text in INVARIANTS.items():
        console.print(f"[bold]{name}[/bold]: {text}")


@app.command("tools")
def list_tools() -> None:
    """List the sandbox tools an agent can call, and the built-in scenarios."""
    for name, (kind, sig) in TOOLS.items():
        console.print(f"[bold]{name}[/bold] ({kind}): {sig}")
    console.print()
    console.print("Built-in scenarios: " + ", ".join(builtin_scenarios()))


def main() -> None:
    app()
