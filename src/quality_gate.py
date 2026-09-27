"""Edge Gateway Layer: Signal Quality Assessment (Quality Gate)."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.config_loader import QualityConfig


class QualityGateResult(str, Enum):
    """Signal quality outcome enumeration."""
    USABLE = "usable"
    INDETERMINATE = "indeterminate"


class QualityEvaluation(BaseModel):
    """Structured result of signal quality gate evaluation."""
    status: QualityGateResult
    reasons: List[str] = Field(default_factory=list)
    contact_quality: float
    imu_motion: float

    @property
    def is_usable(self) -> bool:
        """Helper property to check if window is usable."""
        return self.status == QualityGateResult.USABLE


class QualityGate:
    """Evaluates 2-second EEG window usability based on physical & physical movement artifacts."""

    def __init__(self, config: Optional[QualityConfig] = None):
        """Initializes QualityGate with threshold configuration.

        Args:
            config: QualityConfig instance or default if None.
        """
        self.config = config or QualityConfig()

    def evaluate_window(self, window: Dict[str, Any]) -> QualityEvaluation:
        """Evaluates contact quality and IMU movement indices of an EEG window.

        Args:
            window: Dictionary containing window telemetry.

        Returns:
            QualityEvaluation instance containing status and rejection reasons.
        """
        contact = float(window.get("contact_quality", 0.0))
        motion = float(window.get("imu_motion", 0.0))
        reasons: List[str] = []

        if contact > self.config.contact_quality_max_threshold:
            reasons.append(
                f"Poor electrode contact quality ({contact:.1f} > threshold {self.config.contact_quality_max_threshold:.1f})"
            )

        if motion > self.config.imu_motion_max_threshold:
            reasons.append(
                f"Excessive head/body movement index ({motion:.2f} > threshold {self.config.imu_motion_max_threshold:.2f})"
            )

        if reasons:
            status = QualityGateResult.INDETERMINATE
        else:
            status = QualityGateResult.USABLE

        return QualityEvaluation(
            status=status,
            reasons=reasons,
            contact_quality=contact,
            imu_motion=motion,
        )
