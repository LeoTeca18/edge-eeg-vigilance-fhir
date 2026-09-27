"""Unit tests for AlertDecisionEngine (persistence, refractory, hourly budget)."""

from datetime import datetime, timezone, timedelta
import pytest
from src.config_loader import DecisionConfig
from src.decision_layer import AlertDecisionEngine


def test_persistence_requirement():
    """Tests that 30 consecutive usable windows exceeding threshold trigger an alert."""
    config = DecisionConfig(
        persistence_n_seconds=30,
        probability_threshold=0.70,
        refractory_minutes=20,
        max_alerts_per_hour=2,
    )
    engine = AlertDecisionEngine(config)
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Process 29 windows exceeding threshold (p=0.80, usable=True)
    for i in range(29):
        ts = start_time + timedelta(seconds=i)
        res = engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=ts)
        assert res.trigger_alert is False
        assert res.sustained_seconds == i + 1

    # 30th window triggers alert
    ts_30 = start_time + timedelta(seconds=29)
    res_30 = engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=ts_30)
    assert res_30.trigger_alert is True
    assert res_30.sustained_seconds == 30
    assert res_30.alerts_in_last_hour == 1


def test_persistence_reset_on_low_prob_or_indeterminate():
    """Tests that low probability or indeterminate windows reset persistence counter."""
    config = DecisionConfig(persistence_n_seconds=30, probability_threshold=0.70)
    engine = AlertDecisionEngine(config)
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # 10 windows above threshold
    for i in range(10):
        engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=start_time + timedelta(seconds=i))
    assert engine.consecutive_drop_seconds == 10

    # 11th window is indeterminate -> resets counter
    res_indet = engine.process_window(
        p_vigilance_drop=0.85,
        is_usable=False,
        timestamp=start_time + timedelta(seconds=10),
    )
    assert res_indet.sustained_seconds == 0
    assert res_indet.trigger_alert is False


def test_refractory_period_suppression():
    """Tests that alerts are suppressed during the 20-minute refractory period."""
    config = DecisionConfig(
        persistence_n_seconds=5,  # Shortened for easy testing
        probability_threshold=0.70,
        refractory_minutes=20,
        max_alerts_per_hour=5,
    )
    engine = AlertDecisionEngine(config)
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Trigger 1st alert
    for i in range(5):
        res = engine.process_window(0.80, True, t0 + timedelta(seconds=i))
    assert res.trigger_alert is True

    # Attempt 2nd alert 5 minutes later (well within 20-min refractory window)
    t_5m = t0 + timedelta(minutes=5)
    for i in range(5):
        res2 = engine.process_window(0.85, True, t_5m + timedelta(seconds=i))

    assert res2.trigger_alert is False
    assert res2.in_refractory is True
    assert "refractory" in res2.reason.lower()


def test_hourly_alert_budget_limit():
    """Tests that maximum 2 alerts are allowed per rolling hour."""
    config = DecisionConfig(
        persistence_n_seconds=5,
        probability_threshold=0.70,
        refractory_minutes=10,
        max_alerts_per_hour=2,
    )
    engine = AlertDecisionEngine(config)
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Trigger Alert #1 at t0 (requires 5 consecutive drop windows)
    res_list_1 = [engine.process_window(0.80, True, t0 + timedelta(seconds=i)) for i in range(5)]
    assert any(r.trigger_alert for r in res_list_1)

    # Intermittent normal window to reset consecutive drop counter
    engine.process_window(0.20, True, t0 + timedelta(seconds=10))

    # Trigger Alert #2 at t0 + 15 min (after 10 min refractory, 5 consecutive drop windows)
    t_15m = t0 + timedelta(minutes=15)
    res_list_2 = [engine.process_window(0.80, True, t_15m + timedelta(seconds=i)) for i in range(5)]
    assert any(r.trigger_alert for r in res_list_2)

    # Intermittent normal window to reset consecutive drop counter
    engine.process_window(0.20, True, t_15m + timedelta(seconds=10))

    # Attempt Alert #3 at t0 + 30 min (refractory passed, but hourly budget=2 reached)
    t_30m = t0 + timedelta(minutes=30)
    res_list_3 = [engine.process_window(0.80, True, t_30m + timedelta(seconds=i)) for i in range(5)]
    assert not any(r.trigger_alert for r in res_list_3)
    last_res = res_list_3[-1]
    assert last_res.budget_exceeded is True
    assert "budget exceeded" in last_res.reason.lower()
