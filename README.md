# Edge-First EEG Vigilance Architecture

> **Vigilance-Drop Detection via Single-Channel Consumer EEG, Edge Inference, and HL7 FHIR R4 Interoperability**  
> *Academic Context:* Master's in Applied Computing — UNISINOS (Applied Computing in Healthcare).

---

## Overview

This repository provides an end-to-end edge-computing pipeline for real-time vigilance monitoring using single-channel consumer EEG (e.g., NeuroSky TGAM). 

The system analyzes telemetry at 1 Hz, evaluates signal quality, mitigates alarm fatigue through stateful decision rules, serializes events as standardized **HL7 FHIR R4 Observations**, and streams them via **MQTT** to an interactive **Streamlit dashboard**.

```
┌────────────────────────────────────────────────────────┐
│ 1. ACQUISITION     Synthetic Generator / CSV Replay   │
├────────────────────────────────────────────────────────┤
│ 2. EDGE GATEWAY    Quality Gate + Alert Decision Engine│
├────────────────────────────────────────────────────────┤
│ 3. INTEROPERABILITY FHIR R4 Observations + MQTT Broker │
├────────────────────────────────────────────────────────┤
│ 4. PRESENTATION    Streamlit Live Dashboard & Plotly  │
└────────────────────────────────────────────────────────┘
```

---

## Key Features

- **Edge-First Processing:** Sub-second local decision making with no mandatory cloud dependency.
- **Alarm Fatigue Mitigation:** Three-tier alert suppression:
  - *Persistence:* Requires 30s sustained low vigilance ($P(\text{drop}) \ge 0.70$).
  - *Refractory Period:* 20-minute cooldown after triggering an alert.
  - *Rate Limit:* Maximum 2 alerts per rolling hour.
- **No Data Loss:** Windows rejected due to noise or poor contact are preserved as FHIR Observations marked `indeterminate` with `dataAbsentReason: unreliable`.
- **HL7 FHIR R4 Standard:** Interoperable with modern EHR systems (Observation and Device resources).
- **Live Visualization:** Real-time metrics, alert banners, and timeline charts in Streamlit.

---

## Quickstart

### Option 1: Docker Compose (Recommended)

Starts the MQTT broker, Edge Gateway, and Streamlit dashboard in isolated containers:

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

#### 1. Setup Environment
```bash
# Clone and enter repo
git clone https://github.com/<your-username>/edge-eeg-vigilance-fhir.git
cd edge-eeg-vigilance-fhir

# Create virtual environment
python -m venv venv
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux/macOS:
# source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### 2. Start MQTT Broker
```bash
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:2.0
```

#### 3. Run Gateway Pipeline
```bash
python src/gateway_main.py --config config/config.yaml
```

#### 4. Run Dashboard (Separate Terminal)
```bash
streamlit run dashboard/app_streamlit.py
```
Open **http://localhost:8501** in your browser.

---

## Running Tests

Run the unit test suite covering the Quality Gate, FHIR Builder, and Decision Engine:

```bash
pytest tests/ -v
```

---

## Configuration

Adjust thresholds and broker settings in `config/config.yaml`:

| Section | Parameter | Default | Purpose |
|---|---|---|---|
| `mqtt` | `broker_host` | `localhost` | MQTT broker address (or `mosquitto` in Docker) |
| `mqtt` | `topic_template` | `study/{user_id}/eeg/observation` | Target publish topic |
| `decision` | `persistence_n_seconds` | `30` | Sustained drop time before alert |
| `decision` | `probability_threshold`| `0.70` | Cutoff for vigilance-drop state |
| `decision` | `refractory_minutes` | `20` | Cooling period after an alert |
| `decision` | `max_alerts_per_hour` | `2` | Hourly alert ceiling |
| `quality` | `contact_quality_max_threshold` | `50.0` | Max contact error (0 = best) |
| `quality` | `imu_motion_max_threshold` | `1.5` | Max allowable head movement |

---

## License & Attribution

Developed for the **Master's Programme in Applied Computing at UNISINOS (2026)**.
Licensed for academic and research purposes.
