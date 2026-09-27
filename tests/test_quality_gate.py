"""Unit tests for QualityGate module."""

import pytest
from src.config_loader import QualityConfig
from src.quality_gate import QualityGate, QualityGateResult


def test_quality_gate_usable_window():
    """Tests that a clean window passes quality gate as USABLE."""
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
    """Tests that poor electrode contact results in INDETERMINATE status."""
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
    """Tests that high head/body movement results in INDETERMINATE status."""
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
    """Tests that both contact and motion artifacts are recorded."""
    config = QualityConfig(contact_quality_max_threshold=50.0, imu_motion_max_threshold=1.5)
    gate = QualityGate(config)

    artifact_window = {
        "contact_quality": 120.0,
        "imu_motion": 3.0,
    }
    result = gate.evaluate_window(artifact_window)
    assert result.status == QualityGateResult.INDETERMINATE
    assert len(result.reasons) == 2
