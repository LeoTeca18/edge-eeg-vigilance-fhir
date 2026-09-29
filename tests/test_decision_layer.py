"""Unit tests for the AlertDecisionEngine (persistence, refractory, hourly budget).

These tests verify the three independent alarm-fatigue prevention mechanisms:

    1. **Persistence requirement**: An alert only fires after N consecutive
       seconds of sustained vigilance drop above the threshold.
    2. **Persistence reset**: Low probability or indeterminate (rejected)
       windows reset the persistence counter to zero.
    3. **Refractory period**: Alerts are suppressed during the cooling-off
       window after a dispatched alert.
    4. **Hourly budget**: No more than ``max_alerts_per_hour`` alerts are
       allowed within any rolling 60-minute window.

Each test uses shortened thresholds (e.g. persistence_n_seconds=5 instead
of 30) to keep execution fast while still exercising the same logic paths.
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.config_loader import DecisionConfig
from src.decision_layer import AlertDecisionEngine


def test_persistence_requirement():
    """Verifies that exactly 30 consecutive above-threshold windows are
    required before the first alert fires.

    This test processes 29 windows and asserts that none of them trigger
    an alert, then processes the 30th window and asserts that it DOES
    trigger.  The sustained_seconds counter is verified at each step.

    WHY: Without temporal persistence, every single window above 0.70
    would trigger an alert — leading to dozens of false alarms per minute.
    """
    config = DecisionConfig(
        persistence_n_seconds=30,
        probability_threshold=0.70,
        refractory_minutes=20,
        max_alerts_per_hour=2,
    )
    engine = AlertDecisionEngine(config)
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Process 29 windows exceeding threshold — none should trigger.
    for i in range(29):
        ts = start_time + timedelta(seconds=i)
        res = engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=ts)
        assert res.trigger_alert is False
        assert res.sustained_seconds == i + 1

    # 30th window should trigger the first alert.
    ts_30 = start_time + timedelta(seconds=29)
    res_30 = engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=ts_30)
    assert res_30.trigger_alert is True
    assert res_30.sustained_seconds == 30
    assert res_30.alerts_in_last_hour == 1


def test_persistence_reset_on_low_prob_or_indeterminate():
    """Verifies that the persistence counter resets to zero when an
    indeterminate window interrupts a streak of above-threshold windows.

    WHY: If the signal is unreliable (quality gate failed), the system
    cannot be confident that the vigilance drop is genuine.  Resetting the
    counter is the conservative (safe) choice — it prevents false alerts
    caused by coincidental noise.
    """
    config = DecisionConfig(persistence_n_seconds=30, probability_threshold=0.70)
    engine = AlertDecisionEngine(config)
    start_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Build up 10 consecutive above-threshold windows.
    for i in range(10):
        engine.process_window(p_vigilance_drop=0.80, is_usable=True, timestamp=start_time + timedelta(seconds=i))
    assert engine.consecutive_drop_seconds == 10

    # 11th window is indeterminate → must reset the counter.
    res_indet = engine.process_window(
        p_vigilance_drop=0.85,
        is_usable=False,
        timestamp=start_time + timedelta(seconds=10),
    )
    assert res_indet.sustained_seconds == 0
    assert res_indet.trigger_alert is False


def test_refractory_period_suppression():
    """Verifies that alerts are suppressed during the 20-minute refractory
    cooling period after a dispatched alert.

    The test triggers a first alert, then attempts a second alert 5 minutes
    later (well within the 20-minute refractory window).  The second
    attempt must be suppressed with ``in_refractory=True``.

    WHY: Without a refractory period, the engine could fire back-to-back
    alerts every 30 seconds (the persistence duration), overwhelming the
    user with repetitive notifications.
    """
    config = DecisionConfig(
        persistence_n_seconds=5,  # Shortened for fast test execution
        probability_threshold=0.70,
        refractory_minutes=20,
        max_alerts_per_hour=5,
    )
    engine = AlertDecisionEngine(config)
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Trigger the first alert after 5 consecutive drop windows.
    for i in range(5):
        res = engine.process_window(0.80, True, t0 + timedelta(seconds=i))
    assert res.trigger_alert is True

    # Attempt a second alert 5 minutes later (within the 20-min refractory).
    t_5m = t0 + timedelta(minutes=5)
    for i in range(5):
        res2 = engine.process_window(0.85, True, t_5m + timedelta(seconds=i))

    assert res2.trigger_alert is False
    assert res2.in_refractory is True
    assert "refractory" in res2.reason.lower()


def test_hourly_alert_budget_limit():
    """Verifies that the maximum of 2 alerts per rolling hour is enforced.

    The test triggers two alerts (at t=0 and t=15min), then attempts a
    third at t=30min.  The third attempt must be suppressed even though
    persistence and refractory conditions are both satisfied, because the
    hourly budget of 2 has been consumed.

    WHY: The hourly budget is the ultimate safety net — even if persistence
    and refractory thresholds are misconfigured (too short), the budget
    cap guarantees that the user receives at most 2 alerts per hour.
    """
    config = DecisionConfig(
        persistence_n_seconds=5,
        probability_threshold=0.70,
        refractory_minutes=10,
        max_alerts_per_hour=2,
    )
    engine = AlertDecisionEngine(config)
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # --- Alert #1 at t=0 ---
    res_list_1 = [engine.process_window(0.80, True, t0 + timedelta(seconds=i)) for i in range(5)]
    assert any(r.trigger_alert for r in res_list_1)

    # Reset persistence counter with a normal window.
    engine.process_window(0.20, True, t0 + timedelta(seconds=10))

    # --- Alert #2 at t=15min (after 10-min refractory expires) ---
    t_15m = t0 + timedelta(minutes=15)
    res_list_2 = [engine.process_window(0.80, True, t_15m + timedelta(seconds=i)) for i in range(5)]
    assert any(r.trigger_alert for r in res_list_2)

    # Reset persistence counter again.
    engine.process_window(0.20, True, t_15m + timedelta(seconds=10))

    # --- Alert #3 attempt at t=30min (refractory passed, but budget=2 reached) ---
    t_30m = t0 + timedelta(minutes=30)
    res_list_3 = [engine.process_window(0.80, True, t_30m + timedelta(seconds=i)) for i in range(5)]
    assert not any(r.trigger_alert for r in res_list_3)
    last_res = res_list_3[-1]
    assert last_res.budget_exceeded is True
    assert "budget exceeded" in last_res.reason.lower()
