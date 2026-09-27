"""Unit tests for FHIR Builder module."""

import json
from datetime import datetime, timezone
import pytest
from src.decision_layer import AlertDecisionResult
from src.fhir_builder import build_fhir_device, build_fhir_observation, observation_to_json
from src.quality_gate import QualityEvaluation, QualityGateResult


def test_build_fhir_device():
    """Tests building FHIR R4 Device resource."""
    device = build_fhir_device(device_id="tgam-01", manufacturer="NeuroSky", model_name="TGAM Headband")
    assert device.id == "tgam-01"
    assert device.manufacturer == "NeuroSky"
    assert device.status == "active"


def test_build_fhir_observation_usable_window():
    """Tests FHIR Observation generation for usable window telemetry."""
    window = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "window_id": 1,
        "p_vigilance_drop": 0.85,
        "alpha_theta_ratio": 0.65,
    }
    quality_eval = QualityEvaluation(
        status=QualityGateResult.USABLE,
        reasons=[],
        contact_quality=0.0,
        imu_motion=0.1,
    )
    decision = AlertDecisionResult(
        trigger_alert=True,
        reason="Sustained drop detected",
        sustained_seconds=30,
        alerts_in_last_hour=1,
        in_refractory=False,
        budget_exceeded=False,
    )

    obs = build_fhir_observation(window, quality_eval, decision, user_id="user-42", device_id="tgam-01")

    assert obs.status == "preliminary"
    assert obs.subject.reference == "Patient/user-42"
    assert obs.device.reference == "Device/tgam-01"
    assert len(obs.component) == 4

    json_str = observation_to_json(obs)
    data = json.loads(json_str)
    assert data["resourceType"] == "Observation"
    assert data["status"] == "preliminary"


def test_build_fhir_observation_indeterminate_window():
    """Tests FHIR Observation generation for indeterminate/rejected window."""
    window = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "window_id": 2,
        "p_vigilance_drop": 0.90,
        "alpha_theta_ratio": 0.40,
    }
    quality_eval = QualityEvaluation(
        status=QualityGateResult.INDETERMINATE,
        reasons=["Poor contact quality"],
        contact_quality=95.0,
        imu_motion=0.2,
    )

    obs = build_fhir_observation(window, quality_eval, user_id="user-42", device_id="tgam-01")

    assert obs.status == "preliminary"
    json_str = observation_to_json(obs)
    data = json.loads(json_str)

    # Find quality-gate-status component
    quality_comp = None
    for comp in data["component"]:
        codes = [c["code"] for c in comp["code"]["coding"]]
        if "quality-gate-status" in codes:
            quality_comp = comp
            break

    assert quality_comp is not None
    assert quality_comp["valueString"] == "indeterminate"
    assert "dataAbsentReason" in quality_comp
    assert quality_comp["dataAbsentReason"]["coding"][0]["code"] == "unreliable"
