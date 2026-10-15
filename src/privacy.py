"""LGPD Privacy & Data Governance Module (Lei Federal nº 13.709/2018).

WHY THIS MODULE EXISTS
----------------------
In healthcare IoT (IoHT) and biomedical signal processing, EEG telemetry and
cognitive vigilance states are classified as **Sensitive Personal Data**
(Art. 5º, II da LGPD — dados pessoais sobre a saúde).

Under the Brazilian General Data Protection Law (LGPD, Lei nº 13.709/2018),
processing health data requires strict adherence to:

    1. **Legal Basis (Art. 11, II, 'c')**: Academic and health research
       guaranteeing pseudonymisation whenever possible.
    2. **Minimisation (Art. 6º, III)**: Processing only what is strictly
       necessary for vigilance detection.
    3. **Pseudonymisation (Art. 13 & Art. 5º, XI)**: Ensuring direct identifiers
       (names, CPF, emails) never enter the telemetry pipeline or FHIR resources.
    4. **Right to Erasure (Art. 18, VI)**: Allowing data subjects to request
       immediate deletion of their stored observations.
    5. **Storage Limitation (Art. 16)**: Purging published edge telemetry
       after a configurable retention period.
    6. **Security & Confidentiality (Art. 46 & Art. 6º, VII)**: Tagging all
       FHIR Observations with HL7 v3 Confidentiality 'R' (Restricted).
"""

import hashlib
import re
from typing import Optional
from fhir.resources.coding import Coding
from fhir.resources.meta import Meta


# Pre-compiled regex to detect common direct personal identifiers
_EMAIL_PATTERN = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
_CPF_PATTERN = re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$")


def pseudonymize_identifier(raw_id: str, salt: str = "edge-eeg-lgpd-salt") -> str:
    """Ensures an identifier is strictly pseudonymised before pipeline ingestion.

    If ``raw_id`` is already an opaque pseudo-identifier (e.g. ``user-01``,
    ``sub-042``, ``anon-a1b2c3d4``, or a UUID), it is preserved.

    If ``raw_id`` contains direct PII (such as an email address, CPF, or
    full name), it is irreversibly hashed using salted SHA-256 to produce
    a safe pseudonym: ``anon-<12-hex-chars>``.

    Args:
        raw_id: The raw subject/user identifier from config or ingestion.
        salt: Secret salt for HMAC/hashing to prevent rainbow table attacks.

    Returns:
        A privacy-safe pseudonym string suitable for FHIR and MQTT.
    """
    raw_str = str(raw_id).strip()

    # Detect obvious direct PII (e-mails, CPFs, or names with spaces)
    is_direct_pii = bool(
        _EMAIL_PATTERN.match(raw_str)
        or _CPF_PATTERN.match(raw_str)
        or (" " in raw_str and len(raw_str) > 5)
    )

    if is_direct_pii:
        digest = hashlib.sha256(f"{salt}:{raw_str}".encode("utf-8")).hexdigest()[:12]
        return f"anon-{digest}"

    # If it's already an opaque identifier, return it as-is
    return raw_str


def get_fhir_lgpd_security_meta() -> Meta:
    """Returns HL7 FHIR R4 Meta security tags for LGPD sensitive health data.

    Tags the Observation with HL7 v3 Confidentiality 'R' (Restricted),
    signalling downstream EHR/EMR consumers that this resource contains
    sensitive physiological data protected by health privacy regulations.

    Returns:
        A FHIR R4 Meta resource with the appropriate security coding.
    """
    return Meta(
        security=[
            Coding(
                system="http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                code="R",
                display="Restricted",
            )
        ]
    )
