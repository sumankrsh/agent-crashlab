import pytest

from agent_crashlab import Memory, ToolTimeout, run_once
from agent_crashlab.faults import FaultProfile
from agent_crashlab.harness import Tools
from agent_crashlab.scoring import judge

from .conftest import ScriptedPlan

NONE = FaultProfile(name="none")


def tools_with(scenario, faults):
    backend = scenario.new_backend()
    return Tools(backend, ScriptedPlan(faults)), backend


def test_lost_reply_applies_the_effect_but_raises_timeout(scenario):
    tools, backend = tools_with(scenario, {0: "lost_reply"})
    with pytest.raises(ToolTimeout):
        tools.pay_invoice(invoice_id="INV-1001", amount=1250.0)
    assert [e.key for e in backend.effects] == ["pay:INV-1001"]
    assert tools.calls[0].outcome == "timeout" and tools.calls[0].effects == ["pay:INV-1001"]


def test_timeout_and_error_have_no_effect(scenario):
    tools, backend = tools_with(scenario, {0: "timeout", 1: "error"})
    with pytest.raises(ToolTimeout):
        tools.pay_invoice(invoice_id="INV-1001", amount=1250.0)
    with pytest.raises(Exception, match="503"):
        tools.pay_invoice(invoice_id="INV-1001", amount=1250.0)
    assert backend.effects == []


def test_duplicate_delivery_doubles_effect_unless_keyed(scenario):
    tools, backend = tools_with(scenario, {0: "duplicate", 1: "duplicate"})
    tools.pay_invoice(invoice_id="INV-1001", amount=1250.0)
    tools.pay_invoice(invoice_id="INV-1002", amount=4380.5, idempotency_key="pay-INV-1002")
    keys = [e.key for e in backend.effects]
    assert keys.count("pay:INV-1001") == 2
    assert keys.count("pay:INV-1002") == 1
    assert tools.calls[1].detail.startswith("deduplicated")


def test_partial_page_drops_items_but_keeps_cursor(scenario):
    tools, _ = tools_with(scenario, {0: "partial"})
    page = tools.list_invoices(status="approved")
    assert page["partial"] is True and len(page["items"]) == 2 and page["next_cursor"] == "4"


def test_crash_restarts_with_same_memory(scenario):
    seen = []

    def agent(task, tools, memory):
        seen.append(dict(memory))
        memory["attempts"] = memory.get("attempts", 0) + 1
        if memory["attempts"] == 1:
            from agent_crashlab.errors import AgentCrashed

            raise AgentCrashed("boom")

    rec = run_once(agent, scenario, NONE, seed=1)
    assert rec.outcome == "finished" and rec.restarts == 1
    assert seen == [{}, {"attempts": 1}]


def test_memory_is_a_dict():
    m = Memory()
    m["x"] = 1
    assert isinstance(m, dict) and m["x"] == 1


def test_crash_budget_and_call_budget(scenario):
    from agent_crashlab.errors import AgentCrashed

    def always_crash(task, tools, memory):
        raise AgentCrashed("again")

    rec = run_once(always_crash, scenario, NONE, seed=1, max_restarts=2)
    assert rec.outcome == "crash_budget" and rec.restarts == 2

    def loops(task, tools, memory):
        while True:
            tools.list_invoices()

    rec = run_once(loops, scenario, NONE, seed=1, max_calls=50)
    assert rec.outcome == "call_budget" and len(rec.calls) == 50


def test_agent_exception_is_recorded(scenario):
    def broken(task, tools, memory):
        tools.get_invoice(invoice_id="nope")

    result = judge(run_once(broken, scenario, NONE, seed=1), scenario)
    assert result.record.outcome == "agent_error"
    assert {v.invariant for v in result.violations} == {"finished", "complete"}


def test_unknown_tool(scenario):
    tools, _ = tools_with(scenario, {})
    with pytest.raises(AttributeError):
        tools.delete_everything()
    with pytest.raises(Exception, match="no such tool"):
        tools.call("delete_everything")


def test_runs_are_reproducible(scenario, agents):
    profile = FaultProfile.named("default")
    a = run_once(agents["retry_agent"], scenario, profile, seed=7)
    b = run_once(agents["retry_agent"], scenario, profile, seed=7)
    assert [c.as_dict() for c in a.calls] == [c.as_dict() for c in b.calls]
    assert [e.key for e in a.effects] == [e.key for e in b.effects]
