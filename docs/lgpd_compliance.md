# 🛡️ LGPD Compliance Report (Federal Law No. 13.709/2018)

> **Project:** Vigilance‑Drop Detection With Single‑Channel Consumer EEG — Edge Inference And FHIR‑Native Observations
> **Academic Context:** Master's in Applied Computing — UNISINOS (Applied Computing in Healthcare)
> **Data Classification:** Sensitive Health Personal Data (Art. 5 II of the LGPD)

---

## 1. Legal Basis and Processing Grounds

Electroencephalographic (EEG) signals, fatigue indices, and vigilance‑drop probabilities constitute **biomedical health data**, classified as **Sensitive Personal Data** under **Article 5, clause II of the LGPD**.

The processing of these data in this software architecture is based on the following legal grounds:

1. **Art. 11, clause II, item c** (Research studies by a research body):
   * Explicitly authorises the processing of sensitive personal data for academic and scientific purposes, guaranteeing anonymisation and pseudonymisation where possible.
2. **Art. 11, clause II, item f** (Health protection):
   * Applies to procedures with technical standards that aim to protect the physical integrity and safety of operators/drivers against accidents caused by extreme drowsiness.

---

## 2. LGPD Principles Satisfied by the Architecture (Art. 6)

| Principle | Article | How the Architecture Meets It |
|---|---|---|
| **Purpose** | Art. 6 I | The system processes telemetry for a legitimate, specific, and explicit purpose: real‑time detection of vigilance drops. |
| **Adequacy** | Art. 6 II | Collected data (alpha and theta spectral frequencies) are strictly compatible with state‑of‑the‑art neuroscience for vigilance monitoring. |
| **Necessity (Data Minimisation)** | Art. 6 III | **No biographic data** (e.g., name, CPF, phone, address, photo) are collected or transmitted. The pipeline extracts only aggregated physical features. |
| **Free Access & Transparency** | Art. 6 IV & VI | The Streamlit dashboard and local FHIR persistence allow direct and transparent query of session‑recorded data. |
| **Security & Prevention** | Art. 6 VII & VIII | Write‑ahead logging in a local SQLite database prevents data loss; HL7 FHIR `meta.security` tagging with confidentiality `R` (Restricted) protects downstream hospital bus systems. |
| **Non‑Discrimination** | Art. 6 IX | **Design‑by‑law prohibition:** the system does not compute "brain score", "productivity ranking", "biological age", or any comparisons between individuals. |
| **Accountability** | Art. 6 X | A complete audit trail with UTC timestamps and traceability is stored in the local SQLite database. |

---

## 3. Technical Safeguards Implemented (*Privacy by Design*)

### 3.1 Mandatory Pseudonymisation at Ingestion (Art. 13 & Art. 5 XI)
* Module: [`src/privacy.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/privacy.py)
* Function `pseudonymize_identifier()`:
  * Detects and blocks direct identifiers (e.g., e‑mail, CPF, full names).
  * Converts identifiers into cryptographic irreversible hashes with a salt (`anon-<digest>`).
  * Guarantees that no direct PII traverses MQTT topics (`study/{user_id}/eeg/observation`) or FHIR resources.

### 3.2 HL7 FHIR R4 Confidentiality Tagging (Art. 46)
* Module: [`src/fhir_builder.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/fhir_builder.py)
* All built observations include the element `meta.security`:
```json
"meta": {
  "security": [
    {
      "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
      "code": "R",
      "display": "Restricted"
    }
  ]
}
```
  This instructs any interoperable hospital bus (EHR/EMR) downstream that the record requires strict access control.

### 3.3 Data Subject’s Right to Erasure (Art. 18, clause VI)
* Module: [`src/local_store.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/local_store.py)
* Method `delete_subject_data(subject_id: str) -> int`:
  * Allows the controller to instantly purge all observations from the local store if the data subject withdraws consent or requests deletion.

### 3.4 Edge‑Gateway Storage Limitation (Art. 16)
* Module: [`src/local_store.py`](file:///c:/Users/Lenovo/Documents/GitHub/edge-eeg-vigilance-fhir/src/local_store.py)
* Method `purge_old_published_observations(retention_days=7) -> int`:
  * Automatically removes old records that have already been confirmed and published via MQTT.
  * Protects the edge device’s physical memory against intrusion or theft, reducing long‑term exposure surface.
  * **No‑Loss Guarantee:** Observations that failed to publish (`mqtt_published = 0`) are **never** purged before successful synchronization.

---

## 4. Recommendations for Production Deployment

1. **Secure Communication (MQTTS):** Enable TLS 1.3 with AES‑256 encryption and mutual certificates (mTLS) on port 8883 of Mosquitto.
2. **At‑Rest Encryption:** Use *SQLCipher* or OS‑level volume encryption (BitLocker / LUKS) for the `fhir_observations.db` file.
3. **Informed Consent Form (Free‑Informed‑Consent – TCLE):** Ensure research participants sign a clear consent document before data collection.

---

## License & Attribution

Developed for the **Master's Programme in Applied Computing at UNISINOS (2026)**. Licensed for academic and research purposes.
