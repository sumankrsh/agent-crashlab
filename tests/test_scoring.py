from agent_crashlab import run_once
from agent_crashlab.faults import FaultProfile
from agent_crashlab.harness import RunRecord
from agent_crashlab.scoring import evaluate, judge, reason, wilson
from agent_crashlab.world import Effect

NONE = FaultProfile(name="none")


def test_every_example_agent_passes_with_no_faults(scenario, agents):
    for fn in agents.values():
        result = judge(run_once(fn, scenario, NONE, seed=1), scenario)
        assert result.passed, result.violations


def test_judge_flags_duplicates_wrong_amounts_and_extra_effects(scenario):
    effects = []
    for i, (inv, amount) in enumerate(
        [("INV-1001", 1250.0), ("INV-1001", 1250.0), ("INV-1002", 1.0), ("INV-1003", 620.0)]
    ):
        effects.append(
            Effect(
                key=f"pay:{inv}",
                tool="pay_invoice",
                args={"invoice_id": inv, "amount": amount, "payment_id": "P"},
                call=i,
            )
        )
    rec = RunRecord(seed=1, outcome="finished", error="", restarts=0, calls=[], effects=effects, waited=0)
    result = judge(rec, scenario)
    by = {(v.invariant, v.detail.split(" ")[2]) for v in result.violations}
    assert ("no_duplicates", "INV-1001") in by
    assert ("no_unexpected", "INV-1002") in by  # wrong amount
    assert ("no_unexpected", "INV-1003") in by  # pending invoice paid
    assert result.overpaid == 1250.0
    assert any(v.invariant == "complete" for v in result.violations)


def test_retry_after_lost_reply_is_attributed(scenario, agents):
    profile = FaultProfile(rates={"lost_reply": 0.3})
    result = judge(run_once(agents["retry_agent"], scenario, profile, seed=3), scenario)
    causes = {v.cause for v in result.violations if v.invariant == "no_duplicates"}
    assert causes == {"lost_reply"}
    assert result.overpaid > 0


def test_crash_restart_repeats_work_for_agent_without_checkpoint(scenario, agents):
    profile = FaultProfile(rates={"crash": 0.5})
    result = judge(run_once(agents["retry_agent"], scenario, profile, seed=2), scenario)
    assert result.record.restarts == 1
    assert any(v.cause == "crash" and v.invariant == "no_duplicates" for v in result.violations)
    careful = judge(run_once(agents["careful_agent"], scenario, profile, seed=2), scenario)
    assert careful.record.restarts == 1 and careful.passed


def test_partial_page_skips_work_for_agent_that_trusts_it(scenario, agents):
    profile = FaultProfile(rates={"partial": 0.5})
    retry = judge(run_once(agents["retry_agent"], scenario, profile, seed=1), scenario)
    assert any(v.invariant == "complete" and v.cause == "partial" for v in retry.violations)
    assert judge(run_once(agents["careful_agent"], scenario, profile, seed=1), scenario).passed


def test_evaluate_ranks_the_example_agents(scenario, agents):
    profile = FaultProfile.named("default")
    scores = {name: evaluate(fn, name, scenario, profile, runs=60, seed=1) for name, fn in agents.items()}
    assert scores["careful_agent"].score == 1.0
    assert scores["careful_agent"].crashed_runs == scores["careful_agent"].resumed_cleanly > 0
    assert scores["naive_agent"].score < scores["retry_agent"].score < 0.5
    assert scores["retry_agent"].duplicate_effects > 0 and scores["retry_agent"].overpaid > 0
    reasons = [t for t, _, _ in scores["retry_agent"].reasons()]
    assert "Did it twice: retried after a lost reply" in reasons
    d = scores["retry_agent"].as_dict()
    assert d["runs"] == 60 and len(d["results"]) == 60 and d["failing_seeds"]


def test_wilson_interval():
    lo, hi = wilson(200, 200)
    assert hi == 1.0 and 0.97 < lo < 0.99
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and hi < 0.35
    assert wilson(0, 0) == (0.0, 0.0)


def test_reason_wording():
    from agent_crashlab.scoring import Violation

    assert reason(Violation("no_duplicates", "", "duplicate")).startswith("Did it twice")
    assert reason(Violation("finished", "", "timeout")) == "Stopped: gave up after a timeout"
    assert reason(Violation("complete", "", "partial")) == "Skipped work: trusted a partial page"
