import json
import os

import pytest
from typer.testing import CliRunner

from agent_crashlab import cli

from .conftest import ROOT

runner = CliRunner()
CAREFUL = "examples/agents/careful_agent.py"
RETRY = "examples/agents/retry_agent.py:run"


@pytest.fixture(autouse=True)
def in_project_root(monkeypatch):
    monkeypatch.chdir(ROOT)


def invoke(*args):
    return runner.invoke(cli.app, list(args))


def test_run_compares_agents_and_writes_reports(tmp_path):
    r = invoke("run", RETRY, CAREFUL, "--runs", "40", "--reports", str(tmp_path))
    assert r.exit_code == 0, r.output
    assert "Why retry_agent failed" in r.output
    assert "careful_agent: 40/40 runs clean" in r.output
    data = json.loads((tmp_path / "report.json").read_text())
    assert [a["agent"] for a in data["agents"]] == ["retry_agent", "careful_agent"]
    assert data["agents"][1]["score"] == 1.0
    md = (tmp_path / "report.md").read_text()
    assert "| `careful_agent` | pass | 100.0%" in md
    assert "agent-crashlab replay examples/agents/retry_agent.py:run --seed" in md
    assert (tmp_path / "report.html").read_text().startswith("<!doctype html>")


def test_min_score_sets_exit_status():
    assert invoke("run", RETRY, "--runs", "30", "--min-score", "0.9").exit_code == 1
    r = invoke("run", CAREFUL, "--runs", "30", "--min-score", "0.9")
    assert r.exit_code == 0 and "PASS careful_agent" in r.output


def test_run_uses_config_file(tmp_path):
    r = invoke("run")
    assert r.exit_code == 0, r.output
    assert "PASS careful_agent: 200/200 runs clean" in r.output
    assert (ROOT / "crashlab-report" / "report.json").is_file()


def test_config_paths_resolve_from_another_folder(tmp_path):
    os.chdir(tmp_path)
    r = invoke("run", "--config", str(ROOT / "crashlab.yaml"), "--runs", "10")
    assert r.exit_code == 0, r.output


def test_replay_shows_the_double_payment():
    r = invoke("replay", "examples/agents/retry_agent.py", "--seed", "3")
    assert r.exit_code == 1
    assert "lost reply" in r.output and "9,800.00 paid twice" in r.output
    ok = invoke("replay", CAREFUL, "--seed", "4")
    assert ok.exit_code == 0 and "process restarted (1)" in ok.output and "deduped" in ok.output


def test_usage_errors_exit_2(tmp_path):
    assert invoke("run", "missing_agent.py").exit_code == 2
    assert invoke("run", CAREFUL, "--profile", "nope").exit_code == 2
    assert invoke("run", CAREFUL, "--fault", "meteor=0.1").exit_code == 2
    assert invoke("run", CAREFUL, "--scenario", "nope.yaml").exit_code == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text("agents: [x.py]\nrunz: 3\n")
    r = invoke("run", "--config", str(bad))
    assert r.exit_code == 2 and "unknown key" in r.output


def test_fault_override_and_scenario_option():
    r = invoke(
        "run", CAREFUL, "-s", "examples/scenarios/quarter-end.yaml", "-n", "20", "-p", "none", "-f", "lost_reply=0.2"
    )
    assert r.exit_code == 0, r.output
    assert "quarter-end-payables: 13 invoices to pay" in r.output and "lost_reply 20%" in r.output


def test_info_commands():
    assert "lost_reply" in invoke("faults").output
    out = invoke("tools").output
    assert "pay_invoice" in out and "month-end" in out
    assert "0.1.0" in invoke("--version").output


def test_module_agent_spec(tmp_path, monkeypatch):
    (tmp_path / "my_agent_mod.py").write_text("def go(task, tools, memory):\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    r = invoke("run", "my_agent_mod:go", "-n", "5")
    assert r.exit_code == 1  # does nothing, so it fails even without faults
    assert "fails with no faults injected" in r.output
