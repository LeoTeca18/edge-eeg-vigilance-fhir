"""Storage & Interoperability Layer: FHIR R4 Observation & Device Generator."""

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

    Args:
        device_id: Unique hardware or logical device identifier.
        manufacturer: Hardware manufacturer name.
        model_name: Device model name.

    Returns:
        FHIR R4 Device resource.
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
    """Converts an EEG window into a standardized FHIR R4 Observation resource.

    Crucially, rejected/indeterminate windows are NEVER silently dropped; they are
    encoded with status="preliminary", valueString="indeterminate", and a FHIR
    dataAbsentReason coding ("unreliable").

    Args:
        window: Raw or synthetic window telemetry dictionary.
        quality_eval: QualityEvaluation from QualityGate.
        decision_result: Optional AlertDecisionResult from decision engine.
        user_id: Subject patient identifier.
        device_id: Acquisition headband device identifier.

    Returns:
        FHIR R4 Observation resource instance.
    """
    timestamp_str = window.get("timestamp") or datetime.now(timezone.utc).isoformat()
    p_drop = float(window.get("p_vigilance_drop", 0.0))
    at_ratio = float(window.get("alpha_theta_ratio", 1.0))
    window_idx = window.get("window_id", 0)

    # Category: Activity
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

    # Primary Observation Code
    code = CodeableConcept(
        coding=[
            Coding(
                system="http://local-eeg-system/codes",
                code="vigilance-window",
                display="EEG Vigilance Window Assessment",
            )
        ]
    )

    # Subject & Device references
    subject = Reference(reference=f"Patient/{user_id}")
    device_ref = Reference(reference=f"Device/{device_id}")

    # Build observation components
    components = []

    # 1. Probability of Vigilance Drop Component
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
                code="1",
            ),
        )
    )

    # 2. Alpha/Theta Ratio Component
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

    # 3. Quality Gate Status Component
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
        # Indeterminate window: add dataAbsentReason coding ("unreliable")
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

    # 4. Decision Alert Status Component
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

    # Build final Observation object
    obs_id = f"obs-{user_id}-w{window_idx}"
    observation = Observation(
        id=obs_id,
        status="preliminary",  # Every window is encoded as preliminary
        category=category,
        code=code,
        subject=subject,
        device=device_ref,
        effectiveDateTime=timestamp_str,
        component=components,
    )

    return observation


def observation_to_json(observation: Observation) -> str:
    """Serializes FHIR R4 Observation resource to JSON string.

    Args:
        observation: Observation resource.

    Returns:
        JSON representation string.
    """
    return observation.model_dump_json(exclude_none=True)
