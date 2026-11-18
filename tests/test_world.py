import pytest

from agent_crashlab import ToolConflict, ToolError
from agent_crashlab.world import ScenarioError, load_scenario, scenario_from_dict


def test_builtin_scenario_and_expected_effects(scenario):
    assert scenario.name == "month-end-payables"
    expected = scenario.expected()
    assert len(scenario.to_pay) == 6
    assert expected["pay:INV-1004"] == {"invoice_id": "INV-1004", "amount": 9800.0}
    assert "pay:INV-1003" not in expected  # pending
    assert "pay:INV-1005" not in expected  # disputed


def test_example_scenario_file_loads():
    s = load_scenario("examples/scenarios/quarter-end.yaml")
    assert len(s.invoices) == 18 and len(s.to_pay) == 13 and s.page_size == 5


def test_scenario_validation_lists_every_problem():
    with pytest.raises(ScenarioError) as exc:
        scenario_from_dict({"name": "x", "invoices": [{"id": "A", "amount": -1, "status": "late"}], "page_size": 0})
    text = str(exc.value)
    assert "'task' is required" in text and "page_size" in text and "missing" in text
    with pytest.raises(ScenarioError, match="nothing to do"):
        scenario_from_dict(
            {
                "name": "x",
                "task": "t",
                "invoices": [{"id": "A", "supplier": "S", "email": "a@b.example", "amount": 1, "status": "pending"}],
            }
        )
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario("no-such-file.yaml")


def test_pagination(scenario):
    b = scenario.new_backend()
    first = b.call("list_invoices", {"status": "approved"}, call=0)
    assert [i["id"] for i in first["items"]] == ["INV-1001", "INV-1002", "INV-1004", "INV-1006"]
    second = b.call("list_invoices", {"status": "approved", "cursor": first["next_cursor"]}, call=1)
    assert [i["id"] for i in second["items"]] == ["INV-1007", "INV-1009"]
    assert second["next_cursor"] is None


def test_idempotency_key_dedupes_and_conflicts(scenario):
    b = scenario.new_backend()
    r1 = b.call("pay_invoice", {"invoice_id": "INV-1001", "amount": 1250.0, "idempotency_key": "k1"}, call=0)
    r2 = b.call("pay_invoice", {"invoice_id": "INV-1001", "amount": 1250.0, "idempotency_key": "k1"}, call=1)
    assert r1 == r2 and len(b.effects) == 1 and b.deduplicated == 1
    with pytest.raises(ToolConflict):
        b.call("pay_invoice", {"invoice_id": "INV-1001", "amount": 99.0, "idempotency_key": "k1"}, call=2)
    b.call("pay_invoice", {"invoice_id": "INV-1001", "amount": 1250.0}, call=3)
    assert len(b.effects) == 2  # no key, no protection
    assert len(b.call("list_payments", {"invoice_id": "INV-1001"}, call=4)["items"]) == 2


def test_bad_calls_are_rejected_without_effects(scenario):
    b = scenario.new_backend()
    with pytest.raises(ToolError) as exc:
        b.call("pay_invoice", {"invoice_id": "INV-9999", "amount": 1.0}, call=0)
    assert exc.value.transient is False
    with pytest.raises(ToolError):
        b.call("send_email", {"to": "nobody", "subject": "x"}, call=1)
    assert b.effects == []


def test_email_effect_is_keyed_by_invoice_number(scenario):
    b = scenario.new_backend()
    b.call("send_email", {"to": "a@b.example", "subject": "Remittance", "body": "Paid INV-1002 today"}, call=0)
    b.call("send_email", {"to": "a@b.example", "subject": "Hello"}, call=1)
    assert [e.key for e in b.effects] == ["email:INV-1002", "email:a@b.example:Hello"]
