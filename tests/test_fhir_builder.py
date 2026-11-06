"""Unit tests for the FHIR Builder module.

These tests verify that the FHIR R4 resource generation functions produce
valid, standards-compliant resources. They cover:

    1. Device resource construction with correct identifiers and status.
    2. Observation resource for USABLE windows — all four components present.
    3. Observation resource for INDETERMINATE windows — canonical 'IND' status,
       ``dataAbsentReason`` of ``"unreliable"``, and avoidance of synthetic numeric
       quantities on corrupted signals (FHIR invariant obs-6).
"""

import json
from datetime import datetime, timezone
import pytest
from src.decision_layer import AlertDecisionResult
from src.fhir_builder import build_fhir_device, build_fhir_observation, observation_to_json
from src.quality_gate import QualityEvaluation, QualityGateResult


def test_build_fhir_device():
    """Verifies that the FHIR Device resource is constructed with correct
    identifiers, manufacturer, and an 'active' status.
    """
    device = build_fhir_device(device_id="tgam-01", manufacturer="NeuroSky", model_name="TGAM Headband")
    assert device.id == "tgam-01"
    assert device.manufacturer == "NeuroSky"
    assert device.status == "active"


def test_build_fhir_observation_usable_window():
    """Verifies FHIR Observation generation for a usable (high-quality) window."""
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

    # Verify JSON serialisation produces valid FHIR resource.
    json_str = observation_to_json(obs)
    data = json.loads(json_str)
    assert data["resourceType"] == "Observation"
    assert data["status"] == "preliminary"


def test_build_fhir_observation_indeterminate_window():
    """Verifies FHIR Observation generation for an indeterminate (rejected) window.

    Validates that:
        1. Rejected windows are preserved in the clinical record.
        2. The quality-gate-status component carries the canonical 'IND' code
           and dataAbsentReason='unreliable'.
        3. Numeric inference values (e.g. p-vigilance-drop) are not published
           as valid quantities when the physical signal is rejected; instead,
           they carry dataAbsentReason.
    """
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

    # Locate components
    quality_comp = None
    p_drop_comp = None
    for comp in data["component"]:
        codes = [c["code"] for c in comp["code"]["coding"]]
        if "quality-gate-status" in codes:
            quality_comp = comp
        elif "p-vigilance-drop" in codes:
            p_drop_comp = comp

    # Verify canonical IND quality encoding with dataAbsentReason
    assert quality_comp is not None
    code_val = (
        quality_comp.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code")
        or quality_comp.get("valueCodeableConcept", {}).get("text")
        or quality_comp.get("valueString")
    )
    assert code_val == "IND"
    assert "dataAbsentReason" in quality_comp
    assert quality_comp["dataAbsentReason"]["coding"][0]["code"] == "unreliable"

    # Verify p-vigilance-drop is marked unreliable and has no numerical value
    assert p_drop_comp is not None
    assert "valueQuantity" not in p_drop_comp
    assert p_drop_comp["dataAbsentReason"]["coding"][0]["code"] == "unreliable"
