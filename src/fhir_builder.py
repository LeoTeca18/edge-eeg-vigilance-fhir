"""Storage & Interoperability Layer — FHIR R4 Observation & Device Generator.

WHY THIS MODULE EXISTS
----------------------
Healthcare data interoperability is one of the biggest challenges in clinical
IoT.  Proprietary data formats lock information into vendor-specific silos,
making it impossible to integrate across hospitals, research platforms, or
electronic health record (EHR) systems.

This module solves that problem by converting every EEG telemetry window into
a **HL7 FHIR R4 Observation** resource — the international standard for
healthcare data exchange.  Using FHIR means:

    - Any FHIR-compliant system (HAPI FHIR Server, Google Cloud Healthcare
      API, Azure Health Data Services, etc.) can ingest and query the data.
    - Clinical researchers can aggregate observations across patients, devices,
      and time periods using standard FHIR search parameters.
    - The data is self-describing — each observation carries its own codes,
      units, subject references, and quality metadata.

CRITICAL DESIGN DECISIONS
--------------------------
1. **No data is ever dropped.** Indeterminate windows (those that failed the
   quality gate) are encoded with ``status="preliminary"``,
   ``valueString="indeterminate"``, and a ``dataAbsentReason`` coding of
   ``"unreliable"``.  This guarantees a complete audit trail.

2. **All windows use ``status="preliminary"``** because this is a continuous
   monitoring pipeline — observations are not reviewed by a clinician before
   publication.  A downstream system could upgrade the status to ``"final"``
   after clinical review.

3. **Component-based encoding.** Each observation carries four FHIR components:
       - P(vigilance drop) — the core inference output.
       - Alpha/Theta ratio — the primary spectral feature.
       - Quality gate status — usable vs. indeterminate.
       - Alert triggered — boolean from the decision engine.

HOW IT WORKS
------------
``build_fhir_observation()`` takes a raw window dictionary, a
``QualityEvaluation``, and an optional ``AlertDecisionResult``, and returns
a fully structured ``fhir.resources.Observation`` instance.  The companion
``observation_to_json()`` serialises it to a JSON string for MQTT transport.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.coding import Coding
from fhir.resources.device import Device, DeviceName
from fhir.resources.observation import Observation, ObservationComponent
from fhir.resources.quantity import Quantity
from fhir.resources.reference import Reference

from src.decision_layer import AlertDecisionResult
from src.quality_gate import QualityEvaluation, QualityGateResult


def build_fhir_device(
    device_id: str = "tgam-headband-01",
    manufacturer: str = "NeuroSky",
    model_name: str = "TGAM Single-Channel EEG",
) -> Device:
    """Builds a FHIR R4 Device resource representing the EEG headband.

    The Device resource is referenced from each Observation via
    ``observation.device``, linking every data point to the specific hardware
    that acquired it.  This is essential for provenance tracking — if a
    device is later found to be defective, all its observations can be
    identified and flagged.

    Args:
        device_id: Unique hardware or logical device identifier.
        manufacturer: Hardware manufacturer name.
        model_name: Device model name (human-friendly).

    Returns:
        A FHIR R4 ``Device`` resource instance.
    """
    return Device(
        id=device_id,
        manufacturer=manufacturer,
        name=[
            DeviceName(
                value=model_name,
                type="user-friendly-name",
            )
        ],
        status="active",
    )


def build_fhir_observation(
    window: Dict[str, Any],
    quality_eval: QualityEvaluation,
    decision_result: Optional[AlertDecisionResult] = None,
    user_id: str = "user-01",
    device_id: str = "tgam-headband-01",
) -> Observation:
    """Converts a raw EEG window dictionary into a FHIR R4 Observation.

    This is the core interoperability function.  It maps internal telemetry
    fields to standardised FHIR components, ensuring that rejected windows
    are preserved with proper ``dataAbsentReason`` codings rather than being
    silently discarded.

    The resulting Observation contains four components:
        1. **p-vigilance-drop**: The probability of vigilance drop [0, 1].
        2. **alpha-theta-ratio**: Spectral ratio α/θ (higher = more alert).
        3. **quality-gate-status**: ``"usable"`` or ``"indeterminate"`` with
           ``dataAbsentReason = "unreliable"`` when indeterminate.
        4. **alert-triggered**: Boolean indicating whether the decision
           engine dispatched an alert for this window.

    Args:
        window: Raw telemetry dictionary from the simulator or file replayer.
        quality_eval: ``QualityEvaluation`` from the quality gate.
        decision_result: Optional ``AlertDecisionResult`` from the decision
                         engine.  If ``None``, ``alert-triggered`` defaults
                         to ``False``.
        user_id: Subject / patient identifier for the FHIR ``subject``
                 reference.
        device_id: EEG device identifier for the FHIR ``device`` reference.

    Returns:
        A fully populated FHIR R4 ``Observation`` resource instance.
    """
    timestamp_str = window.get("timestamp") or datetime.now(timezone.utc).isoformat()
    p_drop = float(window.get("p_vigilance_drop", 0.0))
    at_ratio = float(window.get("alpha_theta_ratio", 1.0))
    window_idx = window.get("window_id", 0)

    # --- Category: Activity ---
    # FHIR observation categories help systems filter and group observations.
    # "activity" is appropriate because vigilance monitoring is a form of
    # continuous behavioural/physiological activity tracking.
    category = [
        CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hlth.org/CodeSystem/observation-category",
                    code="activity",
                    display="Activity",
                )
            ]
        )
    ]

    # --- Primary Observation Code ---
    # Uses a local code system because there is no universally agreed LOINC
    # or SNOMED code for "EEG vigilance window assessment" yet.  When a
    # standard code becomes available, this should be updated.
    code = CodeableConcept(
        coding=[
            Coding(
                system="http://local-eeg-system/codes",
                code="vigilance-window",
                display="EEG Vigilance Window Assessment",
            )
        ]
    )

    # --- Subject & Device references ---
    # These references link each Observation to the patient and hardware,
    # enabling FHIR queries like "all observations for Patient/user-01".
    subject = Reference(reference=f"Patient/{user_id}")
    device_ref = Reference(reference=f"Device/{device_id}")

    # --- Build observation components ---
    components = []

    # Component 1: Probability of Vigilance Drop
    # This is the core inference output — the estimated probability that the
    # subject's vigilance has dropped below a safe level.
    components.append(
        ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://local-eeg-system/codes",
                        code="p-vigilance-drop",
                        display="Probability of Vigilance Drop",
                    )
                ]
            ),
            valueQuantity=Quantity(
                value=round(p_drop, 4),
                unit="probability",
                system="http://unitsofmeasure.org",
                code="1",  # Dimensionless unit code per UCUM
            ),
        )
    )

    # Component 2: Alpha/Theta Ratio
    # The α/θ spectral ratio is a well-established biomarker for vigilance.
    # High ratios indicate alert states (strong alpha rhythm); low ratios
    # indicate drowsiness (theta dominance).
    components.append(
        ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://local-eeg-system/codes",
                        code="alpha-theta-ratio",
                        display="Alpha to Theta Band Ratio",
                    )
                ]
            ),
            valueQuantity=Quantity(
                value=round(at_ratio, 3),
                unit="ratio",
            ),
        )
    )

    # Component 3: Quality Gate Status
    # This component records whether the window's signal was physically
    # trustworthy.  For indeterminate windows, a ``dataAbsentReason`` of
    # "unreliable" is added per FHIR best practices — signalling that the
    # observation exists but should not be used for clinical decisions.
    if quality_eval.status == QualityGateResult.USABLE:
        quality_component = ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://local-eeg-system/codes",
                        code="quality-gate-status",
                        display="Signal Quality Gate Status",
                    )
                ]
            ),
            valueString="usable",
        )
    else:
        # Indeterminate: add dataAbsentReason coding ("unreliable") so that
        # downstream FHIR consumers know this observation's numeric values
        # are NOT clinically reliable.
        quality_component = ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://local-eeg-system/codes",
                        code="quality-gate-status",
                        display="Signal Quality Gate Status",
                    )
                ]
            ),
            valueString="indeterminate",
            dataAbsentReason=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hlth.org/CodeSystem/data-absent-reason",
                        code="unreliable",
                        display="Unreliable",
                    )
                ]
            ),
        )

    components.append(quality_component)

    # Component 4: Decision Engine Alert Status
    # Records whether the decision engine dispatched an alert for this window.
    # This allows retrospective analysis of alert patterns.
    trigger_alert = decision_result.trigger_alert if decision_result else False
    components.append(
        ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system="http://local-eeg-system/codes",
                        code="alert-triggered",
                        display="Decision Engine Alert Dispatched",
                    )
                ]
            ),
            valueBoolean=trigger_alert,
        )
    )

    # --- Assemble the final Observation resource ---
    obs_id = f"obs-{user_id}-w{window_idx}"
    observation = Observation(
        id=obs_id,
        status="preliminary",  # All windows are "preliminary" — not clinician-reviewed
        category=category,
        code=code,
        subject=subject,
        device=device_ref,
        effectiveDateTime=timestamp_str,
        component=components,
    )

    return observation


def observation_to_json(observation: Observation) -> str:
    """Serialises a FHIR R4 Observation resource to a compact JSON string.

    Uses Pydantic's ``model_dump_json`` with ``exclude_none=True`` to produce
    a clean JSON payload without null fields — keeping MQTT message sizes
    minimal while remaining fully FHIR-compliant.

    Args:
        observation: The FHIR R4 Observation resource to serialise.

    Returns:
        A JSON string ready for MQTT publication or HTTP POST to a FHIR server.
    """
    return observation.model_dump_json(exclude_none=True)
