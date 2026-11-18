import pytest

from agent_crashlab.faults import FaultPlan, FaultProfile, ProfileError, parse_overrides


def test_named_profile_with_override():
    p = FaultProfile.named("default", {"crash": 0.2})
    assert p.rates["crash"] == 0.2
    assert p.rates["timeout"] == 0.04


def test_unknown_fault_and_bad_rate_are_rejected():
    with pytest.raises(ProfileError, match="unknown fault"):
        FaultProfile(rates={"meteor": 0.1})
    with pytest.raises(ProfileError, match="between 0 and 1"):
        FaultProfile(rates={"timeout": 1.5})
    with pytest.raises(ProfileError, match="add up"):
        FaultProfile(rates={"timeout": 0.5, "error": 0.5})
    with pytest.raises(ProfileError, match="unknown profile"):
        FaultProfile.named("chaos-monkey")


def draws(profile, seed, n=200):
    plan = FaultPlan(profile, seed)
    return [plan.draw("write") for _ in range(n)]


def test_plan_is_deterministic_for_a_seed():
    p = FaultProfile.named("flaky-network")
    assert draws(p, 42) == draws(p, 42)
    assert draws(p, 42) != draws(p, 43)
    assert {"timeout", "lost_reply", "error"} <= set(draws(p, 42))


def test_read_only_and_write_only_faults():
    p = FaultProfile(rates={"partial": 0.9})
    plan = FaultPlan(p, 1)
    assert all(plan.draw("write") is None for _ in range(100))
    assert sum(plan.draw("read") == "partial" for _ in range(100)) > 70
    p = FaultProfile(rates={"lost_reply": 0.9})
    plan = FaultPlan(p, 1)
    assert all(plan.draw("read") is None for _ in range(100))


def test_crashes_are_capped_per_run():
    plan = FaultPlan(FaultProfile(rates={"crash": 0.9}), seed=5, max_crashes=2)
    draws = [plan.draw("write") for _ in range(100)]
    assert draws.count("crash") == 2


def test_parse_overrides():
    assert parse_overrides(["crash=0.1", "timeout = 0.2"]) == {"crash": 0.1, "timeout": 0.2}
    with pytest.raises(ProfileError):
        parse_overrides(["crash"])
    with pytest.raises(ProfileError):
        parse_overrides(["crash=lots"])
