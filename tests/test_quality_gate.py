"""Unit tests for the QualityGate module.

These tests verify that the signal quality gate correctly classifies EEG
windows as USABLE or INDETERMINATE based on electrode contact quality and
IMU motion thresholds.  They cover:

    1. Clean windows that pass both checks.
    2. Windows with poor electrode contact only.
    3. Windows with excessive motion only.
    4. Windows with both artifact types simultaneously.

Each test uses explicit threshold configuration to ensure deterministic
behaviour independent of config file changes.
"""

import pytest
from src.config_loader import QualityConfig
from src.quality_gate import QualityGate, QualityGateResult


def test_quality_gate_usable_window():
    """Verifies that a window with perfect contact and minimal motion is
    classified as USABLE with zero rejection reasons.

    This is the baseline "happy path" test — it confirms that the gate
    does not produce false rejections for clean signals.
    """
    config = QualityConfig(contact_quality_max_threshold=50.0, imu_motion_max_threshold=1.5)
    gate = QualityGate(config)

    clean_window = {
        "contact_quality": 0.0,
        "imu_motion": 0.2,
    }
    result = gate.evaluate_window(clean_window)
    assert result.status == QualityGateResult.USABLE
    assert result.is_usable is True
    assert len(result.reasons) == 0


def test_quality_gate_poor_contact():
    """Verifies that a window with electrode contact quality exceeding the
    threshold is classified as INDETERMINATE.

    Poor electrode contact is the most common artifact in consumer EEG
    headbands — it occurs when the sensor lifts from the forehead or when
    skin impedance increases due to sweat.  The gate must reject these
    windows to prevent corrupted spectral features from reaching the
    decision engine.
    """
    config = QualityConfig(contact_quality_max_threshold=50.0, imu_motion_max_threshold=1.5)
    gate = QualityGate(config)

    poor_contact_window = {
        "contact_quality": 80.0,
        "imu_motion": 0.3,
    }
    result = gate.evaluate_window(poor_contact_window)
    assert result.status == QualityGateResult.INDETERMINATE
    assert result.is_usable is False
    assert any("contact quality" in r.lower() for r in result.reasons)


def test_quality_gate_excessive_motion():
    """Verifies that a window with high head/body movement is classified
    as INDETERMINATE.

    Excessive motion introduces EMG artifacts and accelerometer noise into
    the EEG signal, distorting alpha and theta band power estimates.  The
    gate must flag these windows even if electrode contact is perfect.
    """
    config = QualityConfig(contact_quality_max_threshold=50.0, imu_motion_max_threshold=1.5)
    gate = QualityGate(config)

    high_motion_window = {
        "contact_quality": 10.0,
        "imu_motion": 2.5,
    }
    result = gate.evaluate_window(high_motion_window)
    assert result.status == QualityGateResult.INDETERMINATE
    assert result.is_usable is False
    assert any("movement index" in r.lower() for r in result.reasons)


def test_quality_gate_multiple_artifacts():
    """Verifies that a window failing BOTH contact and motion checks
    records two distinct rejection reasons.

    This test ensures that the gate does not short-circuit after the first
    failed check — all checks must always run so that the full set of
    reasons is available for FHIR encoding and clinical auditing.
    """
    config = QualityConfig(contact_quality_max_threshold=50.0, imu_motion_max_threshold=1.5)
    gate = QualityGate(config)

    artifact_window = {
        "contact_quality": 120.0,
        "imu_motion": 3.0,
    }
    result = gate.evaluate_window(artifact_window)
    assert result.status == QualityGateResult.INDETERMINATE
    assert len(result.reasons) == 2
