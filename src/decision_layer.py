"""Edge Gateway Layer: Stateful Alert Budget & Refractory Decision Engine."""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pydantic import BaseModel, Field
from src.config_loader import DecisionConfig


class AlertDecisionResult(BaseModel):
    """Result returned by the decision engine after processing a window."""
    trigger_alert: bool = Field(description="True if an alert should be dispatched to patient/dashboard")
    reason: str = Field(description="Explanation of decision outcome")
    sustained_seconds: int = Field(description="Current count of consecutive seconds exceeding drop threshold")
    alerts_in_last_hour: int = Field(description="Number of alerts triggered in the rolling 60-minute window")
    in_refractory: bool = Field(description="True if engine is currently in refractory period")
    budget_exceeded: bool = Field(description="True if hourly alert budget has been reached")


class AlertDecisionEngine:
    """Enforces persistence, refractory period, and rolling hourly alert budget.

    Prevents alarm fatigue through temporal persistence verification (e.g. 30s drop),
    refractory cooling periods (e.g. 20 min), and strict hourly capping (max 2/hr).
    """

    def __init__(self, config: Optional[DecisionConfig] = None):
        """Initializes the decision engine with policy configuration.

        Args:
            config: DecisionConfig instance or default configuration.
        """
        self.config = config or DecisionConfig()
        self.consecutive_drop_seconds: int = 0
        self.last_alert_timestamp: Optional[datetime] = None
        self.alert_history: List[datetime] = []

    def _clean_alert_history(self, current_time: datetime) -> None:
        """Removes alerts older than 60 minutes from rolling alert history.

        Args:
            current_time: Current window timestamp.
        """
        one_hour_ago = current_time - timedelta(hours=1)
        self.alert_history = [ts for ts in self.alert_history if ts > one_hour_ago]

    def process_window(
        self,
        p_vigilance_drop: float,
        is_usable: bool,
        timestamp: Optional[datetime] = None,
    ) -> AlertDecisionResult:
        """Evaluates vigilance drop probability against persistence & budget rules.

        Args:
            p_vigilance_drop: Estimated probability of vigilance drop [0.0, 1.0].
            is_usable: Usability status from Quality Gate.
            timestamp: Datetime object of window (defaults to UTC now).

        Returns:
            AlertDecisionResult with trigger status and context.
        """
        now = timestamp or datetime.now(timezone.utc)

        # 1. Clean rolling alert history (older than 1 hour)
        self._clean_alert_history(now)
        alerts_in_last_hour = len(self.alert_history)

        # 2. Update temporal persistence counter
        if is_usable and p_vigilance_drop >= self.config.probability_threshold:
            self.consecutive_drop_seconds += 1
        else:
            # Indeterminate windows or probabilities below threshold reset persistence
            self.consecutive_drop_seconds = 0

        # Check persistence condition (e.g. >= 30 seconds)
        persistence_met = (self.consecutive_drop_seconds >= self.config.persistence_n_seconds)

        # 3. Check refractory period
        in_refractory = False
        if self.last_alert_timestamp is not None:
            refractory_delta = timedelta(minutes=self.config.refractory_minutes)
            if now < (self.last_alert_timestamp + refractory_delta):
                in_refractory = True

        # 4. Check hourly budget
        budget_exceeded = (alerts_in_last_hour >= self.config.max_alerts_per_hour)

        # 5. Make decision
        if not persistence_met:
            reason = f"Persistence not met ({self.consecutive_drop_seconds}/{self.config.persistence_n_seconds}s)"
            trigger_alert = False
        elif in_refractory:
            time_left = (self.last_alert_timestamp + timedelta(minutes=self.config.refractory_minutes)) - now
            mins_left = max(0, int(time_left.total_seconds() // 60))
            reason = f"Alert suppressed: In refractory period (~{mins_left}m remaining)"
            trigger_alert = False
        elif budget_exceeded:
            reason = f"Alert suppressed: Hourly alert budget exceeded ({alerts_in_last_hour}/{self.config.max_alerts_per_hour} alerts in past hour)"
            trigger_alert = False
        else:
            trigger_alert = True
            reason = f"Sustained vigilance drop detected ({self.consecutive_drop_seconds}s >= {self.config.persistence_n_seconds}s). Dispatching alert."
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
