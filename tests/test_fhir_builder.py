"""Unit tests for the FHIR Builder module.

These tests verify that the FHIR R4 resource generation functions produce
valid, standards-compliant resources.  They cover:

    1. Device resource construction with correct identifiers and status.
    2. Observation resource for USABLE windows — all four components present.
    3. Observation resource for INDETERMINATE windows — ``dataAbsentReason``
       of ``"unreliable"`` is correctly applied to the quality-gate-status
       component, ensuring that no data is silently dropped.

The third test is the most critical: it validates the project's core design
principle that rejected windows are ALWAYS preserved with proper FHIR
metadata rather than being discarded.
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

    The Device resource is linked from every Observation via the ``device``
    reference, enabling provenance tracking across the entire dataset.
    """
    device = build_fhir_device(device_id="tgam-01", manufacturer="NeuroSky", model_name="TGAM Headband")
    assert device.id == "tgam-01"
    assert device.manufacturer == "NeuroSky"
    assert device.status == "active"


def test_build_fhir_observation_usable_window():
    """Verifies FHIR Observation generation for a usable (high-quality) window.

    A usable window should produce an Observation with:
        - ``status = "preliminary"`` (continuous monitoring, not clinician-reviewed).
        - Correct ``subject`` and ``device`` references.
        - Exactly four components (p-drop, α/θ ratio, quality status, alert).
        - Valid JSON serialisation with ``resourceType = "Observation"``.
    """
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

    THIS IS THE MOST CRITICAL TEST in the project.  It validates that:
        1. Rejected windows are NOT dropped — they produce a full Observation.
        2. The quality-gate-status component has ``valueString = "indeterminate"``.
        3. A ``dataAbsentReason`` with coding ``"unreliable"`` is present,
           following FHIR best practices for recording unreliable data.

    Without this behaviour, the clinical audit trail would have gaps — there
    would be no record of windows that were acquired but deemed physically
    unreliable.
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

    # Find the quality-gate-status component in the serialised JSON.
    quality_comp = None
    for comp in data["component"]:
        codes = [c["code"] for c in comp["code"]["coding"]]
        if "quality-gate-status" in codes:
            quality_comp = comp
            break

    # Verify indeterminate encoding with dataAbsentReason.
    assert quality_comp is not None
    assert quality_comp["valueString"] == "indeterminate"
    assert "dataAbsentReason" in quality_comp
    assert quality_comp["dataAbsentReason"]["coding"][0]["code"] == "unreliable"
