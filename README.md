# Edge-First EEG Vigilance Architecture

> **Vigilance-Drop Detection via Single-Channel Consumer EEG, Edge Inference, and HL7 FHIR R4 Interoperability**
> *Academic Context:* Master's in Applied Computing — UNISINOS (Applied Computing in Healthcare).

---

## Overview

This repository provides an end‑to‑end edge‑computing pipeline for real‑time vigilance monitoring using a single‑channel consumer EEG device (e.g., NeuroSky TGAM). The system samples telemetry at 1 Hz, evaluates signal quality, mitigates alarm fatigue through stateful decision rules, serialises events as standardized **HL7 FHIR R4 Observations**, and streams them via **MQTT** to an interactive **Streamlit** dashboard.

```text
┌────────────────────────────────────────────────────────┐
│ 1. ACQUISITION   Synthetic Generator / CSV Replay   │
├────────────────────────────────────────────────────────┤
│ 2. EDGE GATEWAY  Quality Gate + Alert Decision Engine│
├────────────────────────────────────────────────────────┤
│ 3. INTEROPERABILITY  FHIR R4 Observations + MQTT Broker │
├────────────────────────────────────────────────────────┤
│ 4. PRESENTATION  Streamlit Live Dashboard & Plotly   │
└────────────────────────────────────────────────────────┘
```

---

## Key Features

- **Edge‑First Processing:** Sub‑second local decision making with no mandatory cloud dependency.
- **Alarm‑Fatigue Mitigation:** Three‑tier alert suppression (persistence, refractory period, rate‑limit).
- **No Data Loss:** Windows rejected due to noise or poor contact are persisted as FHIR Observations marked `indeterminate` with `dataAbsentReason: unreliable`.
- **HL7 FHIR R4 Standard:** Interoperable with modern EHR systems (Observation and Device resources).
- **Live Visualization:** Real‑time metrics, alert banners, and timeline charts in Streamlit.

---

## Architecture

The architecture consists of four logical layers (see diagram above):

1. **Acquisition Layer** – either a synthetic signal generator for testing or a live CSV‑replay of NeuroSky data.
2. **Edge Gateway** – runs on a lightweight device (Raspberry Pi, Windows laptop, etc.). It performs quality‑gate filtering, runs the AI inference model, and applies the decision engine.
3. **Interoperability Layer** – builds FHIR R4 Observation resources and publishes them to an MQTT broker.
4. **Presentation Layer** – a Streamlit web app consumes the MQTT stream and renders charts, alerts, and raw telemetry.

### AI (Edge Inference) Component

- The inference model is a tiny TensorFlow‑Lite (or ONNX) network trained to predict the probability of a vigilance drop from spectral features (alpha, theta power).
- The model runs entirely on the edge gateway, ensuring privacy and sub‑second latency.
- Output `P(drop)` is fed to the **Decision Layer** which implements persistence, refractory, and rate‑limit logic.

### IoT Integration

- **MQTT** is the lingua‑franca for device‑to‑cloud communication. The gateway publishes to a topic template `study/{user_id}/eeg/observation`.
- The broker can be a local Mosquitto container (Docker) or a hosted service. Secure MQTT (TLS 1.3 + mTLS) is recommended for production.
- The Streamlit dashboard subscribes to the same broker and updates in real time.

---

## Quickstart

### Option 1: Docker Compose (Recommended)

```bash
docker-compose up --build
```

Access the dashboard at **http://localhost:8501**.

To stop:

```bash
docker-compose down
```

---

### Option 2: Local Setup

1. **Setup Environment**
```bash
# Clone repository
git clone https://github.com/<your‑username>/edge-eeg-vigilance-fhir.git
cd edge-eeg-vigilance-fhir

# Create virtual environment (PowerShell)
python -m venv venv
.\\venv\\Scripts\\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```
2. **Start MQTT Broker**
```bash
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:2.0
```
3. **Run Gateway Pipeline**
```bash
python src/gateway_main.py --config config/config.yaml
```
4. **Run Dashboard (separate terminal)**
```bash
streamlit run dashboard/app_streamlit.py
```
Open **http://localhost:8501** in your browser.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Configuration

Edit `config/config.yaml` to adjust thresholds, broker settings, and decision‑engine parameters:

| Section | Parameter | Default | Purpose |
|---|---|---|---|
| `mqtt` | `broker_host` | `localhost` | MQTT broker address |
| `mqtt` | `topic_template` | `study/{user_id}/eeg/observation` | Publish topic |
| `decision` | `persistence_n_seconds` | `30` | Sustained drop time before alert |
| `decision` | `probability_threshold` | `0.70` | Cut‑off for vigilance‑drop state |
| `decision` | `refractory_minutes` | `20` | Cool‑down period after an alert |
| `decision` | `max_alerts_per_hour` | `2` | Hourly alert ceiling |
| `quality` | `contact_quality_max_threshold` | `50.0` | Max contact error (0 = best) |
| `quality` | `imu_motion_max_threshold` | `1.5` | Max allowable head movement |

---

## Production Recommendations

1. **Secure MQTT (MQTTS):** Enable TLS 1.3 with AES‑256 and mutual certificates on port 8883.
2. **At‑Rest Encryption:** Use SQLCipher or OS‑level volume encryption (BitLocker) for `fhir_observations.db`.
3. **Informed Consent:** Deploy a Free‑Informed‑Consent form for research subjects.

---

## License & Attribution

Developed for the **Master's Programme in Applied Computing at UNISINOS (2026)**. Licensed for academic and research purposes.
