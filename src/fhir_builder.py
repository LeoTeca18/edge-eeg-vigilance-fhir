"""Storage & Interoperability Layer — FHIR R4 Observation & Device Generator.

WHY THIS MODULE EXISTS
----------------------
Healthcare data interoperability is one of the foundational challenges in
clinical IoT. Proprietary data formats lock information into vendor-specific
silos, hindering cross-institution research, integration with electronic health
records (EHRs), and clinical auditing.

This module converts each processed EEG telemetry window into an international
standard HL7 FHIR R4 Observation resource and supports creating associated
FHIR R4 Device resources. Key architectural principles include:

    1. **Zero Data Loss Audit Trail**: Corrupted or artifact-laden windows are
       never dropped silently. They are preserved in the clinical record as
       FHIR Observations with `status="preliminary"`.
    2. **Canonical Indeterminate Status ('IND') & FHIR Invariant obs-6**:
       Per HL7 FHIR R4 rules, when a signal quality gate fails (contact loss,
       excessive motion, EMG artifacts), the quality gate status component is
       formally marked as 'IND' (HL7 Table 0532 Indeterminate) with a
       `dataAbsentReason` of 'unreliable'. Crucially, numerical inference values
       such as p-vigilance-drop and alpha-theta-ratio do NOT emit synthetic or
       misleading numbers; they carry `dataAbsentReason` to maintain clinical
       fidelity.
    3. **Session Device Provenance**: Devices are explicitly modeled as FHIR
       Device resources and referenced from every Observation.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.coding import Coding
from fhir.resources.device import Device, DeviceName
from fhir.resources.observation import Observation, ObservationComponent
from fhir.resources.quantity import Quantity
from fhir.resources.reference import Reference

from src.decision_layer import AlertDecisionResult
from src.privacy import get_fhir_lgpd_security_meta
from src.quality_gate import QualityEvaluation, QualityGateResult

# Standard HL7 Terminology URIs
HL7_DATA_ABSENT_REASON_SYSTEM = "http://terminology.hlth.org/CodeSystem/data-absent-reason"
HL7_OBS_CATEGORY_SYSTEM = "http://terminology.hlth.org/CodeSystem/observation-category"
HL7_V2_TABLE_0532_SYSTEM = "http://terminology.hlth.org/CodeSystem/v2-0532"
LOCAL_CODE_SYSTEM = "http://local-eeg-system/codes"


def build_fhir_device(
    device_id: str = "tgam-headband-01",
    manufacturer: str = "NeuroSky",
    model_name: str = "TGAM Single-Channel EEG",
) -> Device:
    """Builds a native FHIR R4 Device resource representing the EEG sensor.

    The Device resource is referenced from each Observation via
    ``observation.device``, linking every window to the specific hardware
    that acquired it.

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
    """Converts a raw EEG window dictionary into a compliant FHIR R4 Observation.

    This function handles both usable and rejected (indeterminate) windows:
    - Usable windows include quantitative measurements for P(vigilance drop),
      Alpha/Theta spectral ratio, quality status ('usable'), and alert status.
    - Indeterminate windows preserve the event for auditing without leaking
      untrustworthy numerical quantities. The quality status is explicitly
      coded as 'IND' (HL7 Table 0532) and accompanied by a ``dataAbsentReason``
      of 'unreliable', adhering to the FHIR invariant obs-6.

    Args:
        window: Raw telemetry dictionary from the acquisition layer.
        quality_eval: Quality evaluation result from the QualityGate.
        decision_result: Optional result from AlertDecisionEngine.
        user_id: Pseudonymized subject identifier for ``subject``.
        device_id: Identifier for ``device``.

    Returns:
        A fully validated FHIR R4 ``Observation`` resource instance.
    """
    timestamp_str = window.get("timestamp") or datetime.now(timezone.utc).isoformat()
    p_drop = float(window.get("p_vigilance_drop", 0.0))
    at_ratio = float(window.get("alpha_theta_ratio", 1.0))
    window_idx = window.get("window_id", 0)
    is_usable = (quality_eval.status == QualityGateResult.USABLE)

    # 1. Category: Activity / Monitoring
    category = [
        CodeableConcept(
            coding=[
                Coding(
                    system=HL7_OBS_CATEGORY_SYSTEM,
                    code="activity",
                    display="Activity",
                )
            ]
        )
    ]

    # 2. Main Observation Code
    code = CodeableConcept(
        coding=[
            Coding(
                system=LOCAL_CODE_SYSTEM,
                code="vigilance-window",
                display="EEG Vigilance Window Assessment",
            )
        ]
    )

    # 3. Observation Components
    components = []

    # Component 1: Probability of Vigilance Drop
    # When signal quality is indeterminate, do not emit misleading numeric quantities.
    if is_usable:
        components.append(
            ObservationComponent(
                code=CodeableConcept(
                    coding=[
                        Coding(
                            system=LOCAL_CODE_SYSTEM,
                            code="p-vigilance-drop",
                            display="Probability of Vigilance Drop",
                        )
                    ]
                ),
                valueQuantity=Quantity(
                    value=round(p_drop, 4),
                    unit="probability",
                    system="http://unitsofmeasure.org",
                    code="1",  # Dimensionless ratio per UCUM
                ),
            )
        )
    else:
        components.append(
            ObservationComponent(
                code=CodeableConcept(
                    coding=[
                        Coding(
                            system=LOCAL_CODE_SYSTEM,
                            code="p-vigilance-drop",
                            display="Probability of Vigilance Drop",
                        )
                    ]
                ),
                dataAbsentReason=CodeableConcept(
                    coding=[
                        Coding(
                            system=HL7_DATA_ABSENT_REASON_SYSTEM,
                            code="unreliable",
                            display="Unreliable",
                        )
                    ]
                ),
            )
        )

    # Component 2: Alpha/Theta Band Ratio
    if is_usable:
        components.append(
            ObservationComponent(
                code=CodeableConcept(
                    coding=[
                        Coding(
                            system=LOCAL_CODE_SYSTEM,
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
    else:
        components.append(
            ObservationComponent(
                code=CodeableConcept(
                    coding=[
                        Coding(
                            system=LOCAL_CODE_SYSTEM,
                            code="alpha-theta-ratio",
                            display="Alpha to Theta Band Ratio",
                        )
                    ]
                ),
                dataAbsentReason=CodeableConcept(
                    coding=[
                        Coding(
                            system=HL7_DATA_ABSENT_REASON_SYSTEM,
                            code="unreliable",
                            display="Unreliable",
                        )
                    ]
                ),
            )
        )

    # Component 3: Signal Quality Gate Status
    # Encodes 'usable' vs. canonical 'IND' (HL7 Table 0532) with dataAbsentReason
    if is_usable:
        quality_component = ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system=LOCAL_CODE_SYSTEM,
                        code="quality-gate-status",
                        display="Signal Quality Gate Status",
                    )
                ]
            ),
            valueCodeableConcept=CodeableConcept(
                coding=[
                    Coding(
                        system=LOCAL_CODE_SYSTEM,
                        code="usable",
                        display="Usable",
                    )
                ],
                text="usable",
            ),
        )
    else:
        quality_component = ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system=LOCAL_CODE_SYSTEM,
                        code="quality-gate-status",
                        display="Signal Quality Gate Status",
                    )
                ]
            ),
            valueCodeableConcept=CodeableConcept(
                coding=[
                    Coding(
                        system=HL7_V2_TABLE_0532_SYSTEM,
                        code="IND",
                        display="Indeterminate",
                    )
                ],
                text="IND",
            ),
            dataAbsentReason=CodeableConcept(
                coding=[
                    Coding(
                        system=HL7_DATA_ABSENT_REASON_SYSTEM,
                        code="unreliable",
                        display="Unreliable",
                    )
                ]
            ),
        )

    components.append(quality_component)

    # Component 4: Decision Engine Alert Status
    trigger_alert = decision_result.trigger_alert if decision_result else False
    components.append(
        ObservationComponent(
            code=CodeableConcept(
                coding=[
                    Coding(
                        system=LOCAL_CODE_SYSTEM,
                        code="alert-triggered",
                        display="Decision Engine Alert Dispatched",
                    )
                ]
            ),
            valueBoolean=trigger_alert,
        )
    )

    # Build the final Observation resource
    obs_id = f"obs-{user_id}-w{window_idx}"
    return Observation(
        id=obs_id,
        meta=get_fhir_lgpd_security_meta(),
        status="preliminary",
        category=category,
        code=code,
        subject=Reference(reference=f"Patient/{user_id}"),
        device=Reference(reference=f"Device/{device_id}"),
        effectiveDateTime=timestamp_str,
        component=components,
    )


def observation_to_json(observation: Observation) -> str:
    """Serialises a FHIR R4 Observation resource to a compact JSON string.

    Uses Pydantic's ``model_dump_json`` with ``exclude_none=True`` to produce
    a clean JSON payload without null fields.

    Args:
        observation: The FHIR R4 Observation resource to serialise.

    Returns:
        A JSON string ready for MQTT transport or local SQLite storage.
    """
    return observation.model_dump_json(exclude_none=True)
