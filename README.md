# Edge-First EEG Vigilance Architecture (FHIR R4 + MQTT + Streamlit)

> **Research Title:** *Vigilance-Drop Detection via Single-Channel Consumer EEG and Edge Inference*

---

## 1. Executive Overview & Architecture Philosophy

This repository implements an end-to-end, production-ready software architecture for real-time vigilance-drop monitoring using single-channel consumer EEG headbands (e.g., NeuroSky TGAM).

The system addresses critical challenges in mobile health and wearable computing:
- **No Data Loss on Artifacts:** Rejected or noisy signal windows are **never** silently dropped. They are preserved and encoded as FHIR R4 `Observation` resources with `status="preliminary"`, `valueString="indeterminate"`, and standardized `dataAbsentReason` codings (`"unreliable"`).
- **Alarm Fatigue Mitigation:** The edge decision layer enforces strict temporal persistence ($N = 30\text{ seconds}$ sustained drop at $p \ge 0.70$), a **20-minute refractory cooling period**, and a rolling **hourly alert budget ($\le 2\text{ alerts/hour}$)**.
- **Interoperability & Standards Compliance:** Every window is serialized as an HL7 FHIR R4 `Observation` resource linked to a FHIR `Device` and published over MQTT for real-time visualization and clinical integration.

---

## 2. 4-Layer IoHT System Architecture

```
+-------------------------------------------------------------------------------+
| 1. Acquisition Layer (Stream Simulator / TGAM Export)                        |
|    - Reads Excel (.xlsx) / CSV exports or streams synthetic EEG windows        |
|    - 2-second windows, 1-second stride interval                              |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| 2. Edge Gateway Layer (Processing & Decision)                                 |
|    - Quality Gate: Contact Index <= 50, Motion <= 1.5 -> USABLE / INDETERMINATE|
|    - Feature Extraction & Inference: Spectral Band Powers & P(vigilance drop)  |
|    - Decision Layer: Persistence (30s), Refractory (20m), Budget (<=2/hr)     |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| 3. Storage & Interoperability Layer (FHIR R4 + MQTT Broker)                   |
|    - FHIR Builder: Constructs FHIR R4 Observation & Device JSON               |
|    - Encodes Indeterminate state with dataAbsentReason ("unreliable")        |
|    - MQTT Transport: Eclipse Mosquitto broker ('study/{user_id}/eeg/observation') |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| 4. Presentation Layer (Streamlit Dashboard)                                   |
|    - Live timeline visualization with translucent gray markers for rejected data |
|    - Metric cards (Usable %, Alerts Dispatched, Latest P(drop))               |
|    - Discrete alert notifications ("Consider taking a short pause")           |
+-------------------------------------------------------------------------------+
```

---

## 3. Directory Layout

```
eeg_vigilance_edge/
├── config/
│   └── config.yaml               # Central YAML configuration parameters
├── src/
│   ├── __init__.py               # Package initializer
│   ├── config_loader.py          # Pydantic & PyYAML configuration parser
│   ├── simulator.py              # Stream emulator from file exports or synthetic windows
│   ├── quality_gate.py           # Signal quality assessment (USABLE vs INDETERMINATE)
│   ├── decision_layer.py         # Stateful alert budget and refractory engine
│   ├── fhir_builder.py           # FHIR R4 Observation & Device generator (fhir.resources)
│   ├── mqtt_client.py            # Paho-MQTT publisher client wrapper
│   └── gateway_main.py           # Main edge orchestrator event loop
├── dashboard/
│   └── app_streamlit.py          # Real-time Streamlit dashboard & Plotly charts
├── tests/
│   ├── test_quality_gate.py      # Unit tests for Quality Gate thresholds
│   ├── test_fhir_builder.py      # Unit tests for FHIR Observation & dataAbsentReason
│   └── test_decision_layer.py    # Unit tests for persistence, refractory, & alert budget
├── mosquitto/
│   └── mosquitto.conf            # Eclipse Mosquitto broker configuration
├── Dockerfile                    # Container definition for pipeline & dashboard
├── docker-compose.yml            # Multi-container orchestration
├── requirements.txt              # Python dependency manifest
└── README.md                     # Architecture overview & setup instructions
```

---

## 4. Quick Start Guide

### Prerequisites
- Python 3.11+
- (Optional) Docker & Docker Compose

### Local Execution (Without Docker)

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run Unit Tests:**
   ```bash
   pytest tests/
   ```

3. **Start MQTT Broker (Mosquitto):**
   *(Ensure an MQTT broker is running on `localhost:1883`, or start Mosquitto via Docker)*
   ```bash
   docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:2.0
   ```

4. **Launch Edge Gateway Pipeline:**
   ```bash
   python src/gateway_main.py --config config/config.yaml
   ```

5. **Launch Streamlit Real-Time Dashboard:**
   ```bash
   streamlit run dashboard/app_streamlit.py
   ```
   Open your browser at `http://localhost:8501`.

---

### Docker Compose Execution (Recommended)

To run the complete system (Mosquitto Broker, Edge Gateway Pipeline, and Streamlit Dashboard) in isolated containers:

```bash
docker-compose up --build
```

Access the Streamlit Dashboard at `http://localhost:8501`.

---

## 5. Configuration Reference (`config/config.yaml`)

```yaml
mqtt:
  broker_host: "localhost"
  broker_port: 1883
  topic_template: "study/{user_id}/eeg/observation"
  client_id: "edge-gateway-01"

decision:
  persistence_n_seconds: 30       # Required duration of sustained vigilance drop
  probability_threshold: 0.70     # Drop probability threshold
  refractory_minutes: 20          # Cooling period after alert dispatch
  max_alerts_per_hour: 2          # Upper limit of alerts in rolling 60 minutes

simulator:
  stream_interval_seconds: 1.0    # Window stream stride (seconds)
  user_id: "user-01"              # Subject identifier
  device_id: "tgam-headband-01"   # EEG headband hardware ID
  input_file: null                # Optional path to .xlsx / .csv session dump

quality:
  contact_quality_max_threshold: 50.0  # TGAM contact quality <= 50 is acceptable
  imu_motion_max_threshold: 1.5        # IMU movement index <= 1.5 is acceptable
```

---

## 6. FHIR R4 Specification & Component Mapping

| FHIR Observation Element | Value / System | Description |
|---|---|---|
| `status` | `"preliminary"` | Applied to all continuous incoming windows |
| `category` | `activity` | System: `http://terminology.hlth.org/CodeSystem/observation-category` |
| `code` | `vigilance-window` | System: `http://local-eeg-system/codes` |
| `subject` | `Patient/{user_id}` | Patient/User reference |
| `device` | `Device/{device_id}` | Hardware device reference |
| **Component 1** | `p-vigilance-drop` | Probability of drop ($P \in [0.0, 1.0]$) |
| **Component 2** | `alpha-theta-ratio` | Spectral ratio ($\frac{\alpha}{\theta}$) |
| **Component 3** | `quality-gate-status` | `"usable"` or `"indeterminate"` |
| **`dataAbsentReason`** | `"unreliable"` | System: `http://terminology.hlth.org/CodeSystem/data-absent-reason` (set when `indeterminate`) |
| **Component 4** | `alert-triggered` | Boolean (`true`/`false`) |
