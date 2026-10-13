"""Edge Gateway Layer — Signal Quality Assessment (Quality Gate).

WHY THIS MODULE EXISTS
----------------------
Single-channel consumer EEG headbands (e.g. NeuroSky TGAM) are highly
susceptible to two physical artifact categories:

    1. **Poor electrode contact**: When the forehead sensor lifts or the user
       sweats excessively, impedance rises and the EEG trace becomes noise.
    2. **Excessive motion**: Head movements, jaw clenching, or walking
       introduce EMG and accelerometer artifacts that corrupt spectral features.

If these corrupted windows were fed directly to the decision engine, the
vigilance-drop probability would be meaningless — leading to either false
alerts (alarm fatigue) or missed events.

This module acts as a **binary quality gate**: each 2-second EEG window is
classified as either ``USABLE`` (passed both checks) or ``INDETERMINATE``
(failed one or both).

IMPORTANT DESIGN DECISION — NO DATA IS DROPPED
-----------------------------------------------
Indeterminate windows are **never** discarded silently.  Instead, the gate
returns detailed rejection reasons so that the FHIR builder can encode them
with ``status="preliminary"``, ``valueString="indeterminate"``, and a
``dataAbsentReason`` of ``"unreliable"``.  This guarantees full audit-trail
compliance — every window is accounted for in the clinical record.

HOW IT WORKS
------------
``QualityGate.evaluate_window(window)`` extracts ``contact_quality`` and
``imu_motion`` from the incoming telemetry dictionary, compares each against
configurable thresholds (from ``QualityConfig``), collects human-readable
rejection reasons, and returns a ``QualityEvaluation`` Pydantic model.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.config_loader import QualityConfig


class QualityGateResult(str, Enum):
    """Signal quality outcome enumeration.

    Only two states are possible — there is no partial pass.  A window is
    either fully trustworthy for inference (``USABLE``) or it carries one
    or more physical artifacts that make the EEG signal unreliable
    (``INDETERMINATE``).
    """
    USABLE = "usable"
    INDETERMINATE = "indeterminate"


class QualityEvaluation(BaseModel):
    """Structured result returned after evaluating a single EEG window.

    This model is consumed by:
    - ``AlertDecisionEngine.process_window()`` → uses ``is_usable`` to decide
      whether to count the window toward the persistence counter.
    - ``build_fhir_observation()`` → uses ``status`` and ``reasons`` to encode
      quality metadata into the FHIR R4 Observation resource.
    """
    status: QualityGateResult
    reasons: List[str] = Field(
        default_factory=list,
        description=(
            "Human-readable list of rejection reasons.  Empty when the window "
            "is usable; populated with one entry per failed threshold check "
            "when indeterminate."
        ),
    )
    contact_quality: float
    imu_motion: float

    @property
    def is_usable(self) -> bool:
        """Convenience property for downstream consumers.

        Returns ``True`` when the window passed both contact and motion
        quality checks, meaning its spectral features can be trusted for
        vigilance inference.
        """
        return self.status == QualityGateResult.USABLE


class QualityGate:
    """Evaluates 2-second EEG window usability based on electrode contact
    impedance and inertial motion artifacts.

    The gate is intentionally simple (two threshold comparisons) because it
    runs on every single window at 1 Hz and must be deterministic, fast, and
    easy to audit.  More sophisticated artifact rejection (e.g. ICA, wavelet
    denoising) would be applied upstream in a future ML pre-processing stage.
    """

    def __init__(self, config: Optional[QualityConfig] = None):
        """Initialises the quality gate with threshold configuration.

        Args:
            config: ``QualityConfig`` instance containing the maximum
                    acceptable contact quality and IMU motion thresholds.
                    If ``None``, Pydantic defaults are used (contact ≤ 50,
                    motion ≤ 1.5).
        """
        self.config = config or QualityConfig()

    def evaluate_window(self, window: Dict[str, Any]) -> QualityEvaluation:
        """Runs contact quality, motion, blink, and EMG artifact checks on a
        single window.

        The evaluation is **order-independent** — all four checks always run,
        and all failing reasons are collected.  This means a window can fail
        on multiple grounds simultaneously.

        Args:
            window: Dictionary containing at least ``contact_quality`` (float),
                    ``imu_motion`` (float), ``blink_count`` (int), and
                    ``emg_power`` (float) keys.  Missing keys default to
                    safe values that pass all checks.

        Returns:
            ``QualityEvaluation`` with status, collected rejection reasons,
            and the raw metric values for logging/FHIR encoding.
        """
        contact = float(window.get("contact_quality", 0.0))
        motion = float(window.get("imu_motion", 0.0))
        blink_count = int(window.get("blink_count", 0))
        emg_power = float(window.get("emg_power", 0.0))
        reasons: List[str] = []

        # Check 1: Electrode contact impedance
        # The TGAM sensor reports 0 for perfect contact.  Values above the
        # threshold indicate the sensor has lifted or the user's skin is too
        # oily/sweaty for reliable signal acquisition.
        if contact > self.config.contact_quality_max_threshold:
            reasons.append(
                f"Poor electrode contact quality ({contact:.1f} > threshold "
                f"{self.config.contact_quality_max_threshold:.1f})"
            )

        # Check 2: Inertial motion artifact
        # The IMU motion index quantifies head/body movement.  Excessive
        # motion corrupts spectral power estimates — especially in the alpha
        # and theta bands that are critical for vigilance classification.
        if motion > self.config.imu_motion_max_threshold:
            reasons.append(
                f"Excessive head/body movement index ({motion:.2f} > threshold "
                f"{self.config.imu_motion_max_threshold:.2f})"
            )

        # Check 3: Excessive blink artifacts (EOG contamination)
        # Eye blinks produce large frontal potentials that propagate into
        # the single-channel EEG, distorting alpha-band power estimates.
        if blink_count > self.config.blink_count_max_threshold:
            reasons.append(
                f"Excessive blink count ({blink_count} > threshold "
                f"{self.config.blink_count_max_threshold})"
            )

        # Check 4: High-beta / EMG proxy power (muscle artifact)
        # Jaw clenching, facial muscle tension, and teeth grinding produce
        # broadband EMG noise that elevates high-beta power and corrupts
        # spectral ratio calculations.
        if emg_power > self.config.emg_power_max_threshold:
            reasons.append(
                f"High EMG/beta proxy power ({emg_power:.1f} > threshold "
                f"{self.config.emg_power_max_threshold:.1f})"
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
