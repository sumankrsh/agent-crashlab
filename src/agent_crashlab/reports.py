"""Terminal, JSON, Markdown and single-file HTML reports."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.table import Table

from . import __version__
from .harness import RunRecord
from .scoring import INVARIANTS, RunResult, Summary
from .world import Scenario


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def interval(s: Summary) -> str:
    lo, hi = s.interval
    return f"{lo * 100:.1f}–{hi * 100:.1f}%"


def verdict(s: Summary, min_score: float | None) -> tuple[bool, str]:
    if not s.baseline.passed:
        return False, "fails with no faults injected; fix the agent or scenario first"
    if min_score is None:
        return True, f"{pct(s.score)}"
    ok = s.score >= min_score
    return ok, f"{pct(s.score)} {'≥' if ok else '<'} minimum {pct(min_score)}"


def header(scenario: Scenario, s: Summary) -> str:
    n_effects = len(scenario.expected())
    return (
        f"Scenario [bold]{scenario.name}[/bold]: {len(scenario.to_pay)} invoices to pay, "
        f"{n_effects} side effects expected\n"
        f"Faults ([bold]{s.profile.name}[/bold]): {s.profile.describe()}\n"
        f"{s.runs} seeded runs per agent, seeds {s.seed}–{s.seed + s.runs - 1}"
    )


def print_summaries(
    console: Console, scenario: Scenario, summaries: list[Summary], min_score: float | None, agent_specs: list[str]
) -> None:
    first = summaries[0]
    console.print(header(scenario, first))
    console.print()
    table = Table(show_edge=False, pad_edge=False, header_style="bold", box=None, padding=(0, 2, 0, 0))
    table.add_column("Agent", no_wrap=True)
    table.add_column("Baseline", no_wrap=True)
    table.add_column("Clean", justify="right", no_wrap=True)
    table.add_column("95% range", justify="right", no_wrap=True)
    table.add_column("Twice", justify="right", no_wrap=True)
    table.add_column("Overpaid", justify="right", no_wrap=True)
    table.add_column("Recovered", justify="right", no_wrap=True)
    for s in summaries:
        colour = "green" if s.score >= (min_score if min_score is not None else 0.95) else "red"
        table.add_row(
            s.agent,
            "[green]pass[/green]" if s.baseline.passed else "[red]FAIL[/red]",
            f"[{colour}]{pct(s.score)}[/{colour}]",
            interval(s),
            str(s.duplicate_effects),
            f"{s.overpaid:,.0f}",
            f"{s.resumed_cleanly}/{s.crashed_runs}",
        )
    console.print(table)

    for s, spec in zip(summaries, agent_specs, strict=True):
        reasons = s.reasons()
        if not reasons:
            continue
        console.print()
        console.print(f"[bold]Why {s.agent} failed[/bold] ({s.runs - s.passes} of {s.runs} runs)")
        for text, n, _ in reasons[:5]:
            console.print(f"  {n:>3} runs  {text}")
        console.print(f"  [dim]Replay one: agent-crashlab replay {spec} --seed {s.failing_seeds[0]}[/dim]")

    console.print()
    for s in summaries:
        ok, text = verdict(s, min_score)
        line = f"{s.agent}: {s.passes}/{s.runs} runs clean, {s.duplicate_effects} done twice"
        if min_score is not None or not s.baseline.passed:
            mark = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
            line = f"{mark} {line} ({text})"
        console.print(line)


def print_replay(console: Console, scenario: Scenario, result: RunResult, profile_text: str) -> None:
    rec: RunRecord = result.record
    console.print(f"Seed [bold]{rec.seed}[/bold] · {scenario.name} · {profile_text}")
    console.print()
    if console.width < 90:
        _print_replay_narrow(console, rec)
    else:
        _print_replay_table(console, rec)
    console.print()
    if result.passed:
        console.print(
            f"[green]PASS[/green] {len(rec.calls)} calls, {_n(rec.restarts, 'restart')}, every invariant held"
        )
    else:
        console.print(f"[red]FAIL[/red] {len(rec.calls)} calls, {_n(rec.restarts, 'restart')}")
        for v in result.violations:
            console.print(f"  [red]✗[/red] {v.invariant}: {v.detail} [dim](cause: {v.cause})[/dim]")


def _print_replay_narrow(console: Console, rec: RunRecord) -> None:
    """One short line per call, for terminals under 90 columns."""
    seen: set[str] = set()
    process = 0
    for c in rec.calls:
        if c.process != process:
            process = c.process
            console.print(f"   [red]process restarted ({process})[/red]")
        status = c.fault.replace("_", " ") if c.fault else ("deduped" if c.detail.startswith("deduplicated") else "")
        if c.outcome == "rejected":
            status = "rejected"
        call = short_call(c.tool, {k: v for k, v in c.args.items() if k != "amount"})
        effect = short_effects(c.effects, seen, with_ref=False)
        seen.update(c.effects)
        colour = "red" if c.fault == "crash" else "magenta"
        console.print(f"{c.index:>2} {call:<26} [{colour}]{status:<10}[/{colour}] {effect}".rstrip(), no_wrap=True)


def _print_replay_table(console: Console, rec: RunRecord) -> None:
    table = Table(show_edge=False, pad_edge=False, header_style="bold", box=None, padding=(0, 2, 0, 0))
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Call", no_wrap=True, overflow="ellipsis")
    table.add_column("Fault", no_wrap=True)
    table.add_column("Result")
    table.add_column("Effect", no_wrap=True)
    process = 0
    seen: set[str] = set()
    for c in rec.calls:
        if c.process != process:
            process = c.process
            table.add_row("", f"[red]process restarted ({process})[/red]", "", "", "")
        outcome = {
            "ok": "ok" if c.fault != "partial" else "[yellow]partial[/yellow]",
            "timeout": "[yellow]timeout[/yellow]",
            "error": "[yellow]error[/yellow]",
            "crash": "[red]crash[/red]",
            "rejected": "[red]rejected[/red]",
        }[c.outcome]
        if c.detail and c.outcome == "rejected":
            outcome += f" [dim]({c.detail})[/dim]"
        elif c.detail.startswith("deduplicated"):
            outcome = "[cyan]deduped[/cyan]"
        table.add_row(
            str(c.index),
            short_call(c.tool, c.args),
            f"[magenta]{c.fault.replace('_', ' ')}[/magenta]" if c.fault else "",
            outcome,
            short_effects(c.effects, seen),
        )
        seen.update(c.effects)
    console.print(table)
    if not rec.calls:
        console.print("[dim]The agent made no calls.[/dim]")


INVOICE_REF = re.compile(r"\bINV-[A-Za-z0-9]+")


def short_call(tool: str, args: dict[str, Any]) -> str:
    """pay_invoice(INV-1004, 9,800.00, key) rather than every keyword argument."""
    parts: list[str] = []
    if tool == "send_email":
        ref = INVOICE_REF.search(f"{args.get('subject', '')} {args.get('body', '')}")
        parts.append(ref.group(0) if ref else str(args.get("to", "")))
        if "idempotency_key" in args:
            parts.append("key")
        return f"{tool}({', '.join(parts)})"
    for k, v in args.items():
        if k == "idempotency_key":
            parts.append("key")
        elif k == "amount" and isinstance(v, int | float):
            parts.append(f"{v:,.2f}")
        elif k == "cursor":
            if v is not None:
                parts.append(f"from {v}")
        elif k != "status":
            parts.append(str(v))
    return f"{tool}({', '.join(parts)})"


def short_effects(keys: list[str], seen: set[str] | None = None, with_ref: bool = True) -> str:
    """'paid INV-1004', in red with 'again' or 'x2' when it repeats an effect."""
    words = {"pay": "paid", "email": "emailed"}
    seen = seen or set()
    out: list[str] = []
    for key in dict.fromkeys(keys):
        kind, _, rest = key.partition(":")
        text = f"{words.get(kind, kind)} {rest}" if with_ref else words.get(kind, kind)
        n = keys.count(key)
        if n > 1:
            out.append(f"[red]{text} x{n}[/red]")
        elif key in seen:
            out.append(f"[red]{text} again[/red]")
        else:
            out.append(text)
    return ", ".join(out)


# --- files -----------------------------------------------------------------


def to_json(scenario: Scenario, summaries: list[Summary], min_score: float | None) -> str:
    payload: dict[str, Any] = {
        "tool": "agent-crashlab",
        "version": __version__,
        "scenario": {"name": scenario.name, "expected_effects": sorted(scenario.expected())},
        "min_score": min_score,
        "agents": [],
    }
    for s in summaries:
        d = s.as_dict()
        d["verdict"] = "pass" if verdict(s, min_score)[0] else "fail"
        payload["agents"].append(d)
    return json.dumps(payload, indent=2)


def to_markdown(scenario: Scenario, summaries: list[Summary], min_score: float | None, agent_specs: list[str]) -> str:
    s0 = summaries[0]
    out = [
        "# agent-crashlab report",
        "",
        f"Scenario **{scenario.name}**: {len(scenario.to_pay)} invoices to pay, {len(scenario.expected())} side effects expected. "
        f"Faults (**{s0.profile.name}**): {s0.profile.describe()}. "
        f"{s0.runs} seeded runs per agent, seeds {s0.seed}–{s0.seed + s0.runs - 1}.",
        "",
        "| Agent | No faults | Reliability | 95% range | Did something twice | Overpaid | Crash restarts ok | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for s in summaries:
        ok, _ = verdict(s, min_score)
        out.append(
            f"| `{s.agent}` | {'pass' if s.baseline.passed else 'FAIL'} | {pct(s.score)} | {interval(s)} | "
            f"{s.duplicate_effects} | {s.overpaid:,.2f} | {s.resumed_cleanly}/{s.crashed_runs} | {'pass' if ok else 'FAIL'} |"
        )
    out += ["", "## Invariants", "", "Share of runs in which each invariant held.", ""]
    out.append("| Invariant | " + " | ".join(f"`{s.agent}`" for s in summaries) + " |")
    out.append("|---|" + "---:|" * len(summaries))
    for name, text in INVARIANTS.items():
        out.append(f"| **{name}**: {text} | " + " | ".join(pct(s.invariant_rates()[name]) for s in summaries) + " |")
    for s, spec in zip(summaries, agent_specs, strict=True):
        reasons = s.reasons()
        out += ["", f"## Why `{s.agent}` failed", ""]
        if not reasons:
            out.append(f"It passed all {s.runs} runs.")
            continue
        out += ["| Runs | Reason | Replay |", "|---:|---|---|"]
        for text, n, seed in reasons:
            out.append(f"| {n} | {text} | `agent-crashlab replay {spec} --seed {seed}` |")
    out += ["", "## Faults injected", "", "| Fault | " + " | ".join(f"`{s.agent}`" for s in summaries) + " |"]
    out.append("|---|" + "---:|" * len(summaries))
    names = sorted({f for s in summaries for f in s.faults_injected()})
    for f in names:
        out.append(f"| {f} | " + " | ".join(str(s.faults_injected().get(f, 0)) for s in summaries) + " |")
    out += [
        "",
        "Reliability is the share of seeded runs in which every invariant held. The 95% range is a Wilson score "
        "interval. Each run is reproducible from its seed. All data is synthetic.",
        "",
    ]
    return "\n".join(out)


CSS = """
:root{--bg:#faf8f3;--fg:#24231f;--muted:#6b675d;--line:#e3dfd4;--card:#fff;--accent:#b4441c;--ok:#2f7d4a;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a17;--fg:#ece9e0;--muted:#a6a193;--line:#38352e;--card:#24231f;--accent:#e2774d;--ok:#6cc08a;--bad:#f0837a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1060px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 10px}
.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}.card h3{margin:0 0 6px;font-size:15px;font-family:ui-monospace,monospace}
.score{font-size:34px;font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:8px 0}.bar span{display:block;height:100%;background:var(--bad)}.bar span.ok{background:var(--ok)}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
code{font-family:ui-monospace,monospace;font-size:13px}.wrap{overflow-x:auto}
"""


def to_html(scenario: Scenario, summaries: list[Summary], min_score: float | None, agent_specs: list[str]) -> str:
    e = html.escape
    s0 = summaries[0]
    cards = []
    for s in summaries:
        ok, text = verdict(s, min_score)
        cls = "ok" if ok and s.score >= (min_score if min_score is not None else 0.95) else "bad"
        cards.append(
            f"<div class='card'><h3>{e(s.agent)}</h3><div class='score {cls}'>{pct(s.score)}</div>"
            f"<div class='bar'><span class='{cls}' style='width:{s.score * 100:.1f}%'></span></div>"
            f"<div class='muted'>{s.passes} of {s.runs} runs held every invariant · 95% range {interval(s)}</div>"
            f"<div>Did something twice: <b>{s.duplicate_effects}</b> · overpaid <b>{s.overpaid:,.2f}</b></div>"
            f"<div>Restarted after a crash and finished cleanly: <b>{s.resumed_cleanly}/{s.crashed_runs}</b></div>"
            + (
                f"<div class='{cls}'><b>{'PASS' if ok else 'FAIL'}</b> {e(text)}</div>"
                if min_score is not None or not ok
                else ""
            )
            + "</div>"
        )
    inv_rows = "".join(
        f"<tr><td><b>{e(name)}</b><br><span class='muted'>{e(text)}</span></td>"
        + "".join(f"<td class='n'>{pct(s.invariant_rates()[name])}</td>" for s in summaries)
        + "</tr>"
        for name, text in INVARIANTS.items()
    )
    heads = "".join(f"<th class='n'>{e(s.agent)}</th>" for s in summaries)
    sections = []
    for s, spec in zip(summaries, agent_specs, strict=True):
        reasons = s.reasons()
        if not reasons:
            sections.append(f"<h2>{e(s.agent)}</h2><p>It passed all {s.runs} runs.</p>")
            continue
        rows = "".join(
            f"<tr><td class='n'>{n}</td><td>{e(t)}</td><td><code>agent-crashlab replay {e(spec)} --seed {seed}</code></td></tr>"
            for t, n, seed in reasons
        )
        sections.append(
            f"<h2>Why {e(s.agent)} failed</h2><div class='wrap'><table><tr><th>Runs</th><th>Reason</th><th>Replay</th></tr>{rows}</table></div>"
        )
    fault_names = sorted({f for s in summaries for f in s.faults_injected()})
    fault_rows = "".join(
        f"<tr><td>{e(f)}</td>"
        + "".join(f"<td class='n'>{s.faults_injected().get(f, 0)}</td>" for s in summaries)
        + "</tr>"
        for f in fault_names
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>agent-crashlab report</title><style>{CSS}</style></head><body><main>
<h1>agent-crashlab report</h1>
<div class="muted">Scenario <b>{e(scenario.name)}</b>: {len(scenario.to_pay)} invoices to pay, {len(scenario.expected())} side effects expected.
Faults ({e(s0.profile.name)}): {e(s0.profile.describe())}. {s0.runs} seeded runs per agent, seeds {s0.seed}–{s0.seed + s0.runs - 1}.</div>
<div class="cards">{"".join(cards)}</div>
<h2>Invariants</h2><p class="muted">Share of runs in which each invariant held.</p>
<div class="wrap"><table><tr><th>Invariant</th>{heads}</tr>{inv_rows}</table></div>
{"".join(sections)}
<h2>Faults injected</h2><div class="wrap"><table><tr><th>Fault</th>{heads}</tr>{fault_rows}</table></div>
<p class="muted">Reliability is the share of seeded runs in which every invariant held; the range is a 95% Wilson score interval.
Every run can be replayed from its seed. All data is synthetic. Generated {stamp} by agent-crashlab {__version__}.</p>
</main></body></html>
"""


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
