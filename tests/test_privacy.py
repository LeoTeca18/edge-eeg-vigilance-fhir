"""Unit tests for the LGPD Privacy & Data Governance Module (Lei nº 13.709/2018).

These tests verify that:
    1. Direct personal identifiers (email, CPF, names) are strictly pseudonymised (Art. 13).
    2. Opaque pseudo-identifiers (e.g. "user-01", "anon-xxx") are preserved.
    3. The FHIR Meta security tag is properly generated with HL7 v3 Confidentiality 'R'.
    4. FHIR Observations built by the builder include the LGPD Meta security tag.
"""

from fhir.resources.observation import Observation
from src.privacy import pseudonymize_identifier, get_fhir_lgpd_security_meta
from src.fhir_builder import build_fhir_observation
from src.quality_gate import QualityEvaluation, QualityGateResult


def test_pseudonymize_direct_pii():
    """Verifies that direct personal identifiers (emails, CPFs, full names)
    are hashed into safe, opaque pseudonyms.
    """
    email_id = "paciente.joao@hospital.com.br"
    cpf_id = "123.456.789-00"
    name_id = "Leonardo Silva de Oliveira"

    pseudo_email = pseudonymize_identifier(email_id)
    pseudo_cpf = pseudonymize_identifier(cpf_id)
    pseudo_name = pseudonymize_identifier(name_id)

    assert pseudo_email.startswith("anon-")
    assert pseudo_cpf.startswith("anon-")
    assert pseudo_name.startswith("anon-")

    # Direct PII must NOT appear anywhere in the pseudonym string
    assert "joao" not in pseudo_email
    assert "123" not in pseudo_cpf
    assert "Silva" not in pseudo_name

    # Idempotent / deterministic with same salt
    assert pseudonymize_identifier(email_id) == pseudo_email


def test_preserve_opaque_identifiers():
    """Verifies that already opaque/anonymised identifiers are preserved."""
    assert pseudonymize_identifier("user-01") == "user-01"
    assert pseudonymize_identifier("subject-42") == "subject-42"
    assert pseudonymize_identifier("anon-1234abcd") == "anon-1234abcd"


def test_fhir_lgpd_meta_security():
    """Verifies that the generated FHIR Meta includes HL7 Confidentiality 'R'."""
    meta = get_fhir_lgpd_security_meta()
    assert meta.security is not None
    assert len(meta.security) == 1
    assert meta.security[0].system == "http://terminology.hl7.org/CodeSystem/v3-Confidentiality"
    assert meta.security[0].code == "R"
    assert meta.security[0].display == "Restricted"


def test_fhir_observation_has_lgpd_meta():
    """Verifies that build_fhir_observation() attaches the LGPD security meta tag."""
    window = {
        "timestamp": "2026-10-15T12:00:00Z",
        "window_id": 1,
        "p_vigilance_drop": 0.45,
        "alpha_theta_ratio": 1.2,
    }
    quality_eval = QualityEvaluation(
        status=QualityGateResult.USABLE,
        reasons=[],
        contact_quality=0.0,
        imu_motion=0.1,
    )
    obs = build_fhir_observation(
        window=window,
        quality_eval=quality_eval,
        user_id="user-01",
        device_id="tgam-01",
    )

    assert obs.meta is not None
    assert obs.meta.security is not None
    assert obs.meta.security[0].code == "R"
