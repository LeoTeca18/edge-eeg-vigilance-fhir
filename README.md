# Edge-First EEG Vigilance Architecture

> **Research Title:** *Vigilance-Drop Detection via Single-Channel Consumer EEG and Edge Inference*
>
> **Academic Context:** Master's Programme in Applied Computing — Universidade do Vale do Rio dos Sinos (UNISINOS), II Semester, Applied Computing in Healthcare — Project Seminar.

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [Problem Statement & Motivation](#2-problem-statement--motivation)
3. [4-Layer IoHT System Architecture](#3-4-layer-ioht-system-architecture)
4. [Key Design Decisions](#4-key-design-decisions)
5. [Technology Stack](#5-technology-stack)
6. [Directory Structure](#6-directory-structure)
7. [Module Reference](#7-module-reference)
8. [Prerequisites](#8-prerequisites)
9. [Installation & Setup](#9-installation--setup)
10. [Running the System](#10-running-the-system)
11. [Running Tests](#11-running-tests)
12. [Configuration Reference](#12-configuration-reference)
13. [FHIR R4 Specification & Component Mapping](#13-fhir-r4-specification--component-mapping)
14. [MQTT Topic Structure](#14-mqtt-topic-structure)
15. [Dashboard Overview](#15-dashboard-overview)
16. [Troubleshooting](#16-troubleshooting)
17. [License](#17-license)

---

## 1. Executive Overview

This repository implements an **end-to-end, production-ready software architecture** for real-time vigilance-drop monitoring using **single-channel consumer EEG headbands** (e.g., NeuroSky TGAM).

The system processes continuous EEG telemetry at 1 Hz (one 2-second window per second), evaluates signal quality, applies stateful alert-decision rules to prevent alarm fatigue, encodes every window as a standardised **HL7 FHIR R4 Observation** resource, transports it over **MQTT** (Eclipse Mosquitto), and visualises the session in real time through a **Streamlit dashboard**.

### Core Capabilities

| Capability | Description |
|---|---|
| **No Data Loss** | Rejected / noisy windows are **never** silently dropped. They are preserved as FHIR Observations with `status="preliminary"`, `valueString="indeterminate"`, and `dataAbsentReason="unreliable"`. |
| **Alarm Fatigue Mitigation** | Three independent suppression mechanisms: temporal persistence (30 s), refractory cooling (20 min), and hourly rolling budget (≤ 2 alerts/hr). |
| **Standards Compliance** | Every window is serialised as an HL7 FHIR R4 `Observation` linked to a FHIR `Device`, enabling integration with any FHIR-compliant EHR or research platform. |
| **Edge-First Processing** | All inference and decision logic runs locally on the edge gateway — no cloud dependency for real-time alerting. |
| **Dual Acquisition Modes** | Synthetic EEG stream generation for development/testing, or file replay from recorded Excel/CSV sessions. |

---

## 2. Problem Statement & Motivation

### The Clinical Challenge

Vigilance — the sustained capacity to attend to a task — degrades over time due to fatigue, monotonous environments, or sleep deprivation. In safety-critical domains (driving, surgery, air traffic control), undetected vigilance drops can lead to catastrophic outcomes.

### Why Consumer EEG?

Clinical-grade EEG systems with 32–256 channels are accurate but impractical for continuous, real-world monitoring. Single-channel consumer headbands (NeuroSky TGAM, Muse S, etc.) offer:
- **Low cost** (~$100 vs. $10,000+).
- **Wearable form factor** suitable for ambulatory use.
- **Single dry electrode** — no conductive gel required.

The trade-off is **lower signal quality**, requiring robust quality gates and conservative decision logic.

### Why Edge Computing?

Cloud-based inference introduces latency, bandwidth costs, and privacy concerns (PHI/PII in transit). Edge processing:
- Delivers **sub-second alert latency** (local inference).
- **Reduces bandwidth** by publishing compact FHIR JSON (~2 KB) instead of raw EEG streams (~50 KB/s).
- Keeps **patient data local** until explicitly transmitted.

### Why FHIR?

HL7 FHIR R4 is the international standard for healthcare data exchange. Encoding observations as FHIR resources ensures:
- **Interoperability** with any FHIR-compliant EHR (Epic, Cerner, HAPI FHIR Server, Google Cloud Healthcare API).
- **Self-describing data** — each observation carries its own codes, units, and quality metadata.
- **Audit trail completeness** — every window is accounted for, including rejected ones.

---

## 3. 4-Layer IoHT System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  LAYER 1: ACQUISITION (StreamSimulator)                                     │
│  ┌─────────────────────┐  ┌────────────────────────────────────────────┐   │
│  │ Excel / CSV Replay  │  │ Synthetic EEG Window Generator             │   │
│  │ (real session data)  │  │ (sinusoidal vigilance + artifacts + noise) │   │
│  └──────────┬──────────┘  └──────────────────┬─────────────────────────┘   │
│             └─────────────┬──────────────────┘                              │
│                           ▼                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  LAYER 2: EDGE GATEWAY (Processing & Decision)                              │
│  ┌──────────────────┐  ┌─────────────────────────────────────────────────┐ │
│  │   Quality Gate    │  │   Alert Decision Engine                         │ │
│  │  Contact ≤ 50     │  │  Persistence: 30s sustained P(drop) ≥ 0.70    │ │
│  │  Motion  ≤ 1.5    │  │  Refractory:  20 min cooling after alert      │ │
│  │  → USABLE /       │  │  Budget:      ≤ 2 alerts per rolling hour     │ │
│  │    INDETERMINATE   │  │  → TRIGGER / SUPPRESS                        │ │
│  └────────┬─────────┘  └────────────────────┬────────────────────────────┘ │
│           └──────────────┬──────────────────┘                               │
│                          ▼                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  LAYER 3: STORAGE & INTEROPERABILITY (FHIR R4 + MQTT)                       │
│  ┌──────────────────────────────┐  ┌───────────────────────────────────┐   │
│  │   FHIR R4 Builder            │  │   Eclipse Mosquitto MQTT Broker   │   │
│  │  Observation + Device JSON   │  │   Topic: study/{user}/eeg/obs.   │   │
│  │  dataAbsentReason: unreliable│  │   QoS 1 (at-least-once)          │   │
│  └──────────────┬───────────────┘  └───────────────┬───────────────────┘   │
│                 └──────────────┬─────────────────┘                          │
│                                ▼                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  LAYER 4: PRESENTATION (Streamlit Dashboard)                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Real-time Plotly timeline    │  Metric cards (quality %, alerts)   │   │
│  │  Alert banner notifications   │  FHIR JSON inspector               │   │
│  │  MQTT connection status       │  Auto-refresh @ 1 Hz               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Summary

1. The **Simulator** generates or replays EEG windows at 1 Hz.
2. The **Quality Gate** classifies each window as USABLE or INDETERMINATE.
3. The **Decision Engine** updates persistence, checks refractory/budget, and decides whether to trigger an alert.
4. The **FHIR Builder** constructs a standardised Observation resource with four components.
5. The **MQTT Publisher** transports the JSON payload to the Mosquitto broker.
6. The **Streamlit Dashboard** subscribes to the topic and renders the session in real time.

---

## 4. Key Design Decisions

### 4.1 No Data Loss — Indeterminate Windows Are Preserved

When the quality gate rejects a window (poor contact or excessive motion), it is **not** discarded. Instead, the FHIR builder encodes it with:
- `quality-gate-status → valueString = "indeterminate"`
- `dataAbsentReason → coding = "unreliable"`

This ensures a complete audit trail with no gaps.

### 4.2 Alarm Fatigue Prevention — Three Independent Mechanisms

| Mechanism | Default | Purpose |
|---|---|---|
| Temporal Persistence | 30 seconds | Filters out transient dips (blinks, noise) |
| Refractory Period | 20 minutes | Prevents rapid-fire alert sequences |
| Hourly Rolling Budget | ≤ 2 alerts/hr | Absolute worst-case frequency guarantee |

### 4.3 Graceful Degradation

If the MQTT broker is unreachable, the pipeline continues processing and logging observations locally. No crash, no data loss — MQTT transport is simply skipped.

### 4.4 Environment-Aware Configuration

The same Docker image runs in both development (broker at `localhost`) and Docker Compose (broker at service name `mosquitto`) thanks to the `MQTT_BROKER_HOST` environment variable override.

---

## 5. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11+ | Core application language |
| Data Validation | Pydantic | ≥ 2.0 | Config & model validation with type safety |
| FHIR Resources | fhir.resources | ≥ 8.0 | FHIR R4 Observation & Device generation |
| MQTT Client | paho-mqtt | ≥ 2.0 | Message transport (pub/sub) |
| MQTT Broker | Eclipse Mosquitto | 2.0 | Lightweight MQTT message broker |
| Dashboard | Streamlit | ≥ 1.30 | Real-time web dashboard |
| Charts | Plotly | ≥ 5.18 | Interactive timeline visualisations |
| Data Processing | pandas | ≥ 2.0 | DataFrame operations & file I/O |
| Configuration | PyYAML | ≥ 6.0 | YAML config file parsing |
| Excel Support | openpyxl | ≥ 3.1 | Reading `.xlsx` session exports |
| Testing | pytest | ≥ 8.0 | Unit test framework |
| Containerisation | Docker & Compose | - | Multi-container orchestration |

---

## 6. Directory Structure

```
edge-eeg-vigilance-fhir/
├── .gitignore                    # Git ignore rules (secrets, caches, data files)
├── Dockerfile                    # Container image for gateway & dashboard
├── docker-compose.yml            # Multi-container orchestration (3 services)
├── requirements.txt              # Python dependency manifest
├── README.md                     # This file — full project documentation
│
├── config/
│   └── config.yaml               # Central YAML configuration (thresholds, broker, etc.)
│
├── src/                          # Core application source code
│   ├── __init__.py               # Package initialiser with version string
│   ├── config_loader.py          # Pydantic + PyYAML configuration loader
│   ├── simulator.py              # EEG stream simulator (synthetic or file replay)
│   ├── quality_gate.py           # Signal quality assessment (USABLE / INDETERMINATE)
│   ├── decision_layer.py         # Stateful alert decision engine (3 suppression rules)
│   ├── fhir_builder.py           # FHIR R4 Observation & Device resource generator
│   ├── mqtt_client.py            # Paho-MQTT publisher wrapper with retry logic
│   └── gateway_main.py           # Main orchestrator pipeline (entry point)
│
├── dashboard/
│   └── app_streamlit.py          # Streamlit real-time dashboard & Plotly charts
│
├── tests/                        # Unit test suite
│   ├── test_quality_gate.py      # Tests for quality gate thresholds
│   ├── test_fhir_builder.py      # Tests for FHIR resource generation & dataAbsentReason
│   └── test_decision_layer.py    # Tests for persistence, refractory & budget logic
│
├── mosquitto/
│   └── mosquitto.conf            # Eclipse Mosquitto broker configuration
│
└── test_mqtt_sub.py              # Developer utility — quick MQTT subscriber diagnostic
```

---

## 7. Module Reference

### 7.1 `src/config_loader.py` — Configuration Loader

| Element | Type | Purpose |
|---|---|---|
| `MqttConfig` | Pydantic model | MQTT broker connection parameters |
| `DecisionConfig` | Pydantic model | Alert persistence, refractory & budget thresholds |
| `SimulatorConfig` | Pydantic model | Stream interval, user/device IDs, input file path |
| `QualityConfig` | Pydantic model | Contact quality & IMU motion thresholds |
| `AppConfig` | Pydantic model | Root config aggregating all sub-configs |
| `load_config()` | Function | Loads YAML, validates via Pydantic, applies env overrides |

### 7.2 `src/simulator.py` — EEG Stream Simulator

| Element | Type | Purpose |
|---|---|---|
| `StreamSimulator` | Class | Manages file replay or synthetic EEG generation |
| `generate_synthetic_window()` | Method | Produces a single synthetic window with realistic dynamics |
| `stream_windows()` | Generator | Yields windows at configurable real-time intervals |

### 7.3 `src/quality_gate.py` — Signal Quality Gate

| Element | Type | Purpose |
|---|---|---|
| `QualityGateResult` | Enum | USABLE or INDETERMINATE |
| `QualityEvaluation` | Pydantic model | Gate result with status, reasons & raw metrics |
| `QualityGate` | Class | Evaluates contact quality & IMU motion thresholds |
| `evaluate_window()` | Method | Runs both checks, collects rejection reasons |

### 7.4 `src/decision_layer.py` — Alert Decision Engine

| Element | Type | Purpose |
|---|---|---|
| `AlertDecisionResult` | Pydantic model | Decision outcome with trigger, reason & state metrics |
| `AlertDecisionEngine` | Class | Stateful engine enforcing 3 suppression mechanisms |
| `process_window()` | Method | Evaluates one window against persistence, refractory & budget |

### 7.5 `src/fhir_builder.py` — FHIR R4 Resource Generator

| Element | Type | Purpose |
|---|---|---|
| `build_fhir_device()` | Function | Creates a FHIR Device resource for the EEG headband |
| `build_fhir_observation()` | Function | Converts a window + quality + decision into a FHIR Observation |
| `observation_to_json()` | Function | Serialises FHIR Observation to compact JSON |

### 7.6 `src/mqtt_client.py` — MQTT Publisher

| Element | Type | Purpose |
|---|---|---|
| `MqttPublisher` | Class | Paho-MQTT wrapper with retry, version detection & graceful degradation |
| `connect()` | Method | Connects to broker with configurable retries |
| `publish()` | Method | Publishes JSON payload with QoS 1 delivery guarantee |
| `disconnect()` | Method | Clean shutdown of MQTT background thread |

### 7.7 `src/gateway_main.py` — Orchestrator Pipeline

| Element | Type | Purpose |
|---|---|---|
| `run_pipeline()` | Function | Main loop: acquire → quality → decision → FHIR → publish |

### 7.8 `dashboard/app_streamlit.py` — Streamlit Dashboard

| Element | Type | Purpose |
|---|---|---|
| `_MqttSharedState` | Class | Thread-safe singleton for MQTT ↔ Streamlit communication |
| `parse_fhir_observation()` | Function | Flattens FHIR JSON into chart-friendly dictionary |
| `_start_mqtt_client()` | Function | Cached MQTT subscriber (runs once, persists across reruns) |
| `main()` | Function | Dashboard entry point — metrics, charts, alerts, auto-refresh |

---

## 8. Prerequisites

### Required Software

| Software | Minimum Version | Installation |
|---|---|---|
| **Python** | 3.11+ | [python.org/downloads](https://www.python.org/downloads/) |
| **pip** | Latest | Comes with Python; upgrade with `python -m pip install --upgrade pip` |
| **Git** | Any | [git-scm.com](https://git-scm.com/) |

### Optional Software (for Docker deployment)

| Software | Installation |
|---|---|
| **Docker Desktop** | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| **Docker Compose** | Included with Docker Desktop on Windows/macOS |

---

## 9. Installation & Setup

### Step 1: Clone the Repository

```bash
git clone https://github.com/<your-username>/edge-eeg-vigilance-fhir.git
cd edge-eeg-vigilance-fhir
```

### Step 2: Create a Python Virtual Environment (Recommended)

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**macOS / Linux (Bash):**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs all required packages: `fhir.resources`, `paho-mqtt`, `streamlit`, `pydantic`, `pandas`, `pyyaml`, `openpyxl`, `plotly`, and `pytest`.

### Step 4: Verify Installation

```bash
python -c "from src.config_loader import load_config; print(load_config())"
```

You should see a printed `AppConfig` object with default values.

---

## 10. Running the System

### Option A: Local Execution (Without Docker)

This option runs each component separately on your machine.

#### 1. Start the MQTT Broker

You need an MQTT broker running on `localhost:1883`. The easiest way is via Docker:

```bash
docker run -d --name mosquitto -p 1883:1883 -p 9001:9001 eclipse-mosquitto:2.0
```

Alternatively, install Mosquitto natively: [mosquitto.org/download](https://mosquitto.org/download/).

> **Note:** If you skip this step, the pipeline will still run but MQTT messages will not be transported (they are logged locally instead).

#### 2. Launch the Edge Gateway Pipeline

```bash
python src/gateway_main.py --config config/config.yaml
```

You will see per-window log output like:

```
17:30:01 [INFO] EdgeGateway - Window #000 | Status: [OK]       | P(drop): 0.42 | Sustained: 01s | State: [NORMAL] | Published: True
17:30:02 [INFO] EdgeGateway - Window #001 | Status: [REJECTED]  | P(drop): 0.38 | Sustained: 00s | State: [NORMAL] | Published: True
```

To limit the number of windows (useful for demos):
```bash
python src/gateway_main.py --config config/config.yaml --max-windows 100
```

Press `Ctrl+C` to stop the pipeline gracefully.

#### 3. Launch the Streamlit Dashboard

In a **separate terminal** (with the virtual environment activated):

```bash
streamlit run dashboard/app_streamlit.py
```

Open your browser at **http://localhost:8501**.

The dashboard will:
- Show a 🟢 green indicator if connected to the MQTT broker.
- Display real-time metrics and a Plotly timeline chart.
- Show alert banners when the decision engine dispatches a vigilance alert.

---

### Option B: Docker Compose (Recommended for Full Stack)

Docker Compose launches all three services (Mosquitto broker, edge gateway, Streamlit dashboard) in isolated containers with automatic networking.

#### 1. Build and Start All Services

```bash
docker-compose up --build
```

This will:
- Pull the `eclipse-mosquitto:2.0` image.
- Build the Python application image from the `Dockerfile`.
- Start three containers:
  - `eeg-mosquitto-broker` — MQTT broker on port 1883.
  - `eeg-edge-gateway` — Edge processing pipeline.
  - `eeg-streamlit-dashboard` — Dashboard on port 8501.

#### 2. Access the Dashboard

Open your browser at **http://localhost:8501**.

#### 3. View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f edge-gateway
```

#### 4. Stop All Services

```bash
docker-compose down
```

---

## 11. Running Tests

The project includes 11 unit tests covering the quality gate, FHIR builder, and decision engine.

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_quality_gate.py -v

# Run with coverage report (requires pytest-cov)
pip install pytest-cov
pytest tests/ --cov=src --cov-report=term-missing
```

### Test Coverage Summary

| Module | Tests | What is Tested |
|---|---|---|
| `test_quality_gate.py` | 4 | Usable/indeterminate classification, contact & motion thresholds, multi-artifact |
| `test_fhir_builder.py` | 3 | Device resource, usable observation, indeterminate + dataAbsentReason encoding |
| `test_decision_layer.py` | 4 | 30s persistence, counter reset, 20-min refractory, hourly budget cap |

---

## 12. Configuration Reference

All tuneable parameters are defined in `config/config.yaml`:

```yaml
# ---------- MQTT Broker Connection ----------
mqtt:
  broker_host: "localhost"          # Broker hostname (override with MQTT_BROKER_HOST env var)
  broker_port: 1883                 # Standard unencrypted MQTT port
  topic_template: "study/{user_id}/eeg/observation"  # Per-patient topic pattern
  client_id: "edge-gateway-01"      # Unique client identifier
  keepalive: 60                     # TCP keepalive interval (seconds)

# ---------- Decision Engine Thresholds ----------
decision:
  persistence_n_seconds: 30         # Sustained drop duration required before alert
  probability_threshold: 0.70       # Minimum P(drop) to count as "in drop state"
  refractory_minutes: 20            # Cooling period after alert dispatch (minutes)
  max_alerts_per_hour: 2            # Maximum alerts in any rolling 60-minute window

# ---------- Simulator Settings ----------
simulator:
  stream_interval_seconds: 1.0      # Delay between windows (seconds) — 1.0 = real-time
  user_id: "user-01"                # Patient/subject identifier
  device_id: "tgam-headband-01"     # EEG headband device identifier
  input_file: null                  # Set to a file path to replay recorded data

# ---------- Quality Gate Thresholds ----------
quality:
  contact_quality_max_threshold: 50.0   # Max acceptable contact quality (0 = perfect)
  imu_motion_max_threshold: 1.5         # Max acceptable motion index
```

### Environment Variable Overrides

| Variable | Overrides | Used By |
|---|---|---|
| `MQTT_BROKER_HOST` | `mqtt.broker_host` | Docker Compose (set to `mosquitto` service name) |

---

## 13. FHIR R4 Specification & Component Mapping

Every EEG window is encoded as a FHIR R4 `Observation` with the following structure:

| FHIR Element | Value / System | Description |
|---|---|---|
| `resourceType` | `"Observation"` | FHIR resource type |
| `status` | `"preliminary"` | All windows — not clinician-reviewed |
| `category` | `activity` | `http://terminology.hlth.org/CodeSystem/observation-category` |
| `code` | `vigilance-window` | `http://local-eeg-system/codes` |
| `subject` | `Patient/{user_id}` | Patient/subject reference |
| `device` | `Device/{device_id}` | EEG headband device reference |
| `effectiveDateTime` | ISO 8601 UTC | Window acquisition timestamp |

### Observation Components

| # | Code | Type | Description |
|---|---|---|---|
| 1 | `p-vigilance-drop` | `valueQuantity` | Probability of vigilance drop ∈ [0.0, 1.0] |
| 2 | `alpha-theta-ratio` | `valueQuantity` | Spectral ratio α/θ (higher = more alert) |
| 3 | `quality-gate-status` | `valueString` | `"usable"` or `"indeterminate"` |
| 4 | `alert-triggered` | `valueBoolean` | `true` if decision engine dispatched alert |

### Indeterminate Window Encoding

When `quality-gate-status = "indeterminate"`, the component includes:

```json
{
  "dataAbsentReason": {
    "coding": [{
      "system": "http://terminology.hlth.org/CodeSystem/data-absent-reason",
      "code": "unreliable",
      "display": "Unreliable"
    }]
  }
}
```

### Example FHIR R4 Observation (Usable Window)

```json
{
  "resourceType": "Observation",
  "id": "obs-user-01-w42",
  "status": "preliminary",
  "category": [{
    "coding": [{
      "system": "http://terminology.hlth.org/CodeSystem/observation-category",
      "code": "activity",
      "display": "Activity"
    }]
  }],
  "code": {
    "coding": [{
      "system": "http://local-eeg-system/codes",
      "code": "vigilance-window",
      "display": "EEG Vigilance Window Assessment"
    }]
  },
  "subject": { "reference": "Patient/user-01" },
  "device": { "reference": "Device/tgam-headband-01" },
  "effectiveDateTime": "2026-01-15T14:30:42.123456+00:00",
  "component": [
    {
      "code": { "coding": [{ "system": "http://local-eeg-system/codes", "code": "p-vigilance-drop" }] },
      "valueQuantity": { "value": 0.8234, "unit": "probability", "system": "http://unitsofmeasure.org", "code": "1" }
    },
    {
      "code": { "coding": [{ "system": "http://local-eeg-system/codes", "code": "alpha-theta-ratio" }] },
      "valueQuantity": { "value": 0.651, "unit": "ratio" }
    },
    {
      "code": { "coding": [{ "system": "http://local-eeg-system/codes", "code": "quality-gate-status" }] },
      "valueString": "usable"
    },
    {
      "code": { "coding": [{ "system": "http://local-eeg-system/codes", "code": "alert-triggered" }] },
      "valueBoolean": true
    }
  ]
}
```

---

## 14. MQTT Topic Structure

| Topic Pattern | Example | Publisher | Subscriber |
|---|---|---|---|
| `study/{user_id}/eeg/observation` | `study/user-01/eeg/observation` | Edge Gateway | Streamlit Dashboard |

The dashboard subscribes to the wildcard `study/+/eeg/observation` to receive observations from all subjects.

**QoS Level:** 1 (at-least-once delivery) — ensures no messages are lost in transit.

---

## 15. Dashboard Overview

The Streamlit dashboard (`dashboard/app_streamlit.py`) provides:

| Section | Description |
|---|---|
| **Connection Status** | 🟢 MQTT Connected or 🟡 Offline indicator |
| **Metric Cards** | Total Windows, Signal Quality %, Alerts Dispatched, Latest P(drop) |
| **Alert Banner** | ⚠️ Warning notification when a vigilance alert is triggered |
| **Timeline Chart** | Interactive Plotly plot showing P(drop) over time, with blue markers for usable windows and gray X markers for rejected windows |
| **Threshold Line** | Dashed red line at P = 0.70 (configurable) |
| **FHIR Inspector** | Expandable panel showing the raw parsed FHIR payload |
| **Sidebar Controls** | MQTT broker host/port, topic pattern, auto-refresh toggle |

The dashboard auto-refreshes every 1 second when enabled, providing near-real-time updates.

---

## 16. Troubleshooting

### MQTT Connection Issues

| Symptom | Cause | Solution |
|---|---|---|
| Dashboard shows 🟡 Offline | Mosquitto broker not running | Start broker: `docker run -d -p 1883:1883 eclipse-mosquitto:2.0` |
| Gateway shows "Proceeding without MQTT" | Broker unreachable | Check broker host/port in `config.yaml` |
| Docker containers can't connect | DNS resolution | Ensure `MQTT_BROKER_HOST=mosquitto` is set in `docker-compose.yml` |

### Pipeline Issues

| Symptom | Cause | Solution |
|---|---|---|
| `ModuleNotFoundError: No module named 'src'` | PYTHONPATH not set | Run from project root, or set `PYTHONPATH=.` |
| No alerts firing | Persistence threshold too high | Check `persistence_n_seconds` and `probability_threshold` in config |
| Too many alerts | Thresholds too aggressive | Increase `persistence_n_seconds` or decrease `max_alerts_per_hour` |

### Dashboard Issues

| Symptom | Cause | Solution |
|---|---|---|
| Dashboard blank / no data | Gateway not running | Start the gateway: `python src/gateway_main.py` |
| Charts not updating | Auto-refresh disabled | Enable "Auto-refresh UI" checkbox in sidebar |
| Port 8501 in use | Another Streamlit instance | Kill existing process or use `--server.port=8502` |

### Docker Issues

| Symptom | Cause | Solution |
|---|---|---|
| Build fails | Missing Docker | Install Docker Desktop |
| Containers exit immediately | Port conflict | Stop conflicting services on ports 1883/8501 |
| Slow builds | No cache | Use `docker-compose up --build` only for first run |

---

## 17. License

This project was developed as part of the Master's Programme in Applied Computing at UNISINOS. Please refer to the institutional guidelines for usage and distribution terms.

---

> **Developed by:** Master's Programme Student — Applied Computing in Healthcare, UNISINOS, 2026.
