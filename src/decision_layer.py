"""Edge Gateway Layer — Stateful Alert Budget & Refractory Decision Engine.

WHY THIS MODULE EXISTS
----------------------
Alarm fatigue is one of the most dangerous usability failures in clinical
monitoring systems.  Studies show that when healthcare professionals are
bombarded with frequent, often false-positive alerts, they begin to ignore
ALL alerts — including genuine critical ones.

This module exists to enforce **three independent suppression mechanisms**
that together prevent alarm fatigue while still detecting sustained vigilance
drops:

    1. **Temporal Persistence** (default: 30 seconds)
       A vigilance drop must be sustained for N consecutive seconds (each
       second's P(drop) ≥ threshold) before qualifying as genuine.  This
       eliminates momentary dips caused by blinks, micro-movements, or
       transient spectral noise.

    2. **Refractory Cooling Period** (default: 20 minutes)
       After an alert fires, the engine enters a "cooling off" state.  No
       new alert can fire during this period, giving the user time to act
       on the previous alert without being interrupted again.

    3. **Hourly Rolling Budget** (default: ≤ 2 alerts/hour)
       Even if persistence and refractory conditions are both met, the engine
       enforces a hard cap on the total number of alerts within any 60-minute
       sliding window.  This provides an absolute worst-case guarantee on
       alert frequency.

HOW IT WORKS
------------
``AlertDecisionEngine`` is a **stateful** object — it maintains:
    - ``consecutive_drop_seconds``: persistence counter (reset on any gap).
    - ``last_alert_timestamp``: used to check the refractory window.
    - ``alert_history``: list of alert timestamps for the rolling budget.

Each call to ``process_window()`` updates state and returns an
``AlertDecisionResult`` describing the decision and its reasoning.

CRITICAL INTERACTION WITH QUALITY GATE
--------------------------------------
Windows marked as ``indeterminate`` by the Quality Gate are treated as
"gaps" — they reset the persistence counter to zero.  This is intentional:
if the signal is unreliable, the system cannot be confident that the drop
is genuine, and starting the count over is the conservative (safe) choice.
"""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
from src.config_loader import DecisionConfig


class AlertDecisionResult(BaseModel):
    """Structured result returned by the decision engine after evaluating
    a single EEG window against persistence, refractory, and budget rules.

    This model is consumed by:
    - ``gateway_main.py`` → for logging and telemetry output.
    - ``fhir_builder.py`` → to encode the ``alert-triggered`` boolean
      component inside the FHIR R4 Observation resource.
    - ``app_streamlit.py`` → to render alert banners on the dashboard.
    """
    trigger_alert: bool = Field(
        description="True if all three conditions are met and an alert should be dispatched",
    )
    reason: str = Field(
        description="Human-readable explanation of why the alert was or was not triggered",
    )
    sustained_seconds: int = Field(
        description="Current count of consecutive seconds exceeding the drop threshold",
    )
    alerts_in_last_hour: int = Field(
        description="Number of alerts already triggered in the rolling 60-minute window",
    )
    in_refractory: bool = Field(
        description="True if the engine is currently within the refractory cooling period",
    )
    budget_exceeded: bool = Field(
        description="True if the hourly alert budget has been fully consumed",
    )


class AlertDecisionEngine:
    """Stateful decision engine that enforces persistence, refractory, and
    hourly budget rules to prevent alarm fatigue.

    The engine is designed to be called once per second (matching the 1 Hz
    window stride).  It maintains internal state across calls, so a single
    instance must be reused for the entire session.
    """

    def __init__(self, config: Optional[DecisionConfig] = None):
        """Initialises the decision engine with policy configuration.

        Args:
            config: ``DecisionConfig`` instance defining persistence duration,
                    probability threshold, refractory period, and hourly budget.
                    Falls back to safe defaults if ``None``.
        """
        self.config = config or DecisionConfig()

        # Persistence counter: incremented each second the drop is sustained,
        # reset to zero on any gap (low probability or indeterminate quality).
        self.consecutive_drop_seconds: int = 0

        # Timestamp of the most recently dispatched alert — used to enforce
        # the refractory cooling period.
        self.last_alert_timestamp: Optional[datetime] = None

        # Rolling history of all alert timestamps within the current hour —
        # used to enforce the hourly budget cap.
        self.alert_history: List[datetime] = []

    def _clean_alert_history(self, current_time: datetime) -> None:
        """Removes alert timestamps older than 60 minutes from history.

        This implements the "sliding window" aspect of the hourly budget.
        Old alerts that have scrolled out of the 60-minute window no longer
        count toward the budget, allowing new alerts to fire.

        Args:
            current_time: Reference timestamp for the current window.
        """
        one_hour_ago = current_time - timedelta(hours=1)
        self.alert_history = [ts for ts in self.alert_history if ts > one_hour_ago]

    def process_window(
        self,
        p_vigilance_drop: float,
        is_usable: bool,
        timestamp: Optional[datetime] = None,
    ) -> AlertDecisionResult:
        """Evaluates a single window against all three suppression mechanisms.

        The evaluation follows a strict priority order:
            1. Persistence check   → must be met first.
            2. Refractory check    → suppresses if within cooling period.
            3. Budget check        → suppresses if hourly cap reached.
            4. Fire alert          → only if ALL conditions pass.

        Args:
            p_vigilance_drop: Estimated probability of vigilance drop,
                              in the range [0.0, 1.0].
            is_usable: Boolean from ``QualityGate`` — ``True`` if the window's
                       signal quality is trustworthy.
            timestamp: Datetime of the window.  Defaults to UTC now if not
                       provided (useful for synthetic streams).

        Returns:
            ``AlertDecisionResult`` containing the trigger decision, a
            human-readable reason, and current engine state metrics.
        """
        now = timestamp or datetime.now(timezone.utc)

        # --- Step 1: Clean expired entries from the rolling alert history ---
        # Alerts older than 60 minutes are no longer relevant to the budget.
        self._clean_alert_history(now)
        alerts_in_last_hour = len(self.alert_history)

        # --- Step 2: Update temporal persistence counter ---
        # The counter increments only when BOTH conditions hold:
        #   (a) the window is physically usable (quality gate passed), AND
        #   (b) the drop probability meets or exceeds the threshold.
        # ANY break in either condition resets the counter to zero — this is
        # the "sustained" requirement that filters out transient dips.
        if is_usable and p_vigilance_drop >= self.config.probability_threshold:
            self.consecutive_drop_seconds += 1
        else:
            self.consecutive_drop_seconds = 0

        # Has the drop been sustained for the required duration?
        persistence_met = (self.consecutive_drop_seconds >= self.config.persistence_n_seconds)

        # --- Step 3: Check refractory cooling period ---
        # If an alert was recently dispatched, suppress new alerts until the
        # cooling period expires.  This prevents rapid-fire alert sequences.
        in_refractory = False
        if self.last_alert_timestamp is not None:
            refractory_delta = timedelta(minutes=self.config.refractory_minutes)
            if now < (self.last_alert_timestamp + refractory_delta):
                in_refractory = True

        # --- Step 4: Check hourly rolling budget ---
        # Even if persistence and refractory pass, the hard budget cap
        # provides an absolute worst-case limit on alert frequency.
        budget_exceeded = (alerts_in_last_hour >= self.config.max_alerts_per_hour)

        # --- Step 5: Make the final decision ---
        # Priority: persistence → refractory → budget → fire.
        if not persistence_met:
            reason = (
                f"Persistence not met ({self.consecutive_drop_seconds}/"
                f"{self.config.persistence_n_seconds}s)"
            )
            trigger_alert = False

        elif in_refractory:
            time_left = (
                self.last_alert_timestamp
                + timedelta(minutes=self.config.refractory_minutes)
                - now
            )
            mins_left = max(0, int(time_left.total_seconds() // 60))
            reason = f"Alert suppressed: In refractory period (~{mins_left}m remaining)"
            trigger_alert = False

        elif budget_exceeded:
            reason = (
                f"Alert suppressed: Hourly alert budget exceeded "
                f"({alerts_in_last_hour}/{self.config.max_alerts_per_hour} "
                f"alerts in past hour)"
            )
            trigger_alert = False

        else:
            # All three gates passed — dispatch the alert.
            trigger_alert = True
            reason = (
                f"Sustained vigilance drop detected "
                f"({self.consecutive_drop_seconds}s >= "
                f"{self.config.persistence_n_seconds}s). Dispatching alert."
            )
            # Record this alert for future refractory and budget calculations.
            self.last_alert_timestamp = now
            self.alert_history.append(now)

        return AlertDecisionResult(
            trigger_alert=trigger_alert,
            reason=reason,
            sustained_seconds=self.consecutive_drop_seconds,
            alerts_in_last_hour=len(self.alert_history),
            in_refractory=in_refractory,
            budget_exceeded=budget_exceeded,
        )
