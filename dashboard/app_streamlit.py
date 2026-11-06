"""Presentation Layer — Real-Time Ethical Streamlit Dashboard.

WHY THIS MODULE EXISTS
----------------------
Clinical and occupational monitoring of cognitive vigilance requires a human-facing
interface that respects human factors, cognitive ergonomics, and ethics:

ETHICAL UI & HUMAN FACTORS DIRECTIVES
-------------------------------------
    1. **Strict Suppression of Absolute Scores**: Displaying continuous decimal
       probabilities (e.g., P(drop) = 0.85) induces user anxiety, distraction, and
       dangerous "score-watching" compensation. All metrics and charts are strictly
       quantized into calm, qualitative clinical states (Nominal, Low Vigilance,
       Signal Artifact).
    2. **Minimalist, Non-Alarmist Intervention**: Displays exclusively the minimalist
       prompt "Consider a pause." without sirens, aggressive warnings, or clinical panic.
    3. **Silent by Default**: Zero audio cues or intrusive sounds.
    4. **Dynamic Greyed-Out Mode**: When signal quality is degraded (IND status due
       to poor electrode contact or head movement), the interface automatically dims
       and desaturates to indicate that telemetry is untrusted and sensor adjustment
       is required.
    5. **Asynchronous MQTT Telemetry**: Ingests FHIR R4 Observations in background
       threads via Paho-MQTT, maintaining high UI responsiveness.
"""

import json
import logging
import queue
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config

logger = logging.getLogger("EthicalDashboard")


class _MqttSharedState:
    """Thread-safe singleton container for MQTT state surviving Streamlit reruns."""

    def __init__(self):
        self.message_queue: queue.Queue = queue.Queue()
        self.is_connected: bool = False
        self.client = None


@st.cache_resource
def _get_shared_state() -> _MqttSharedState:
    """Instantiates and caches the shared MQTT background state."""
    return _MqttSharedState()


def parse_fhir_observation(payload_str: str) -> Dict[str, Any]:
    """Parses incoming FHIR R4 Observation JSON payloads into qualitative states.

    Upholds ethical privacy by extracting qualitative vigilance states while
    suppressing absolute floating-point probability values.
    """
    try:
        data = json.loads(payload_str)
        effective_time = data.get("effectiveDateTime", datetime.now(timezone.utc).isoformat())
        subject = data.get("subject", {}).get("reference", "Patient/unknown").replace("Patient/", "")

        components = data.get("component", [])
        is_usable = True
        quality_code = "usable"
        alert_triggered = False
        p_drop_val: Optional[float] = None

        for comp in components:
            coding_list = comp.get("code", {}).get("coding", [])
            for c in coding_list:
                code_name = c.get("code")
                if code_name == "quality-gate-status":
                    # Check for canonical 'IND' in CodeableConcept or fallback to text/string
                    val_cc = comp.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code")
                    val_text = comp.get("valueCodeableConcept", {}).get("text")
                    val_str = comp.get("valueString")
                    quality_code = val_cc or val_text or val_str or "usable"
                    if str(quality_code).upper() in ["IND", "INDETERMINATE"]:
                        is_usable = False
                    if "dataAbsentReason" in comp:
                        is_usable = False

                elif code_name == "alert-triggered":
                    alert_triggered = bool(comp.get("valueBoolean", False))

                elif code_name == "p-vigilance-drop":
                    val_qty = comp.get("valueQuantity", {}).get("value")
                    if val_qty is not None:
                        p_drop_val = float(val_qty)
                    if "dataAbsentReason" in comp:
                        is_usable = False

        # Classify into calm, ethical qualitative states
        if not is_usable:
            state = "Signal Artifact (IND)"
        elif alert_triggered or (p_drop_val is not None and p_drop_val >= 0.70):
            state = "Low Vigilance"
        else:
            state = "Nominal"

        return {
            "timestamp": effective_time,
            "user_id": subject,
            "quality_status": "IND" if not is_usable else "USABLE",
            "is_usable": is_usable,
            "vigilance_state": state,
            "alert_triggered": alert_triggered,
        }
    except Exception as e:
        logger.error(f"Error parsing FHIR observation payload: {e}")
        return {}


@st.cache_resource
def _start_mqtt_client(broker_host: str, broker_port: int, topic: str):
    """Starts the cached MQTT subscriber background loop."""
    import socket
    import paho.mqtt.client as mqtt

    shared = _get_shared_state()
    target_host = broker_host
    try:
        socket.gethostbyname(target_host)
    except socket.gaierror:
        target_host = "localhost"

    def on_connect(client, userdata, flags, rc, properties=None):
        if getattr(rc, "value", rc) == 0:
            client.subscribe(topic)
            shared.is_connected = True
            logger.info(f"Connected to MQTT broker at {target_host}:{broker_port}, subscribed to '{topic}'")

    def on_disconnect(client, userdata, *args, **kwargs):
        shared.is_connected = False
        logger.warning("Disconnected from MQTT broker.")

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            parsed = parse_fhir_observation(payload)
            if parsed:
                shared.message_queue.put(parsed)
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    try:
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="streamlit-ethical-dashboard",
            )
        except (AttributeError, ValueError):
            client = mqtt.Client(client_id="streamlit-ethical-dashboard")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        shared.client = client
        client.connect_async(target_host, broker_port, 60)
        client.loop_start()
    except Exception as e:
        logger.warning(f"Could not initialize MQTT subscriber ({target_host}:{broker_port}): {e}")


def main():
    """Main dashboard entry point."""
    st.set_page_config(
        page_title="Vigilance Rhythm Monitor",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    config = load_config()
    shared = _get_shared_state()

    # Sidebar: Minimalist Connectivity Settings
    st.sidebar.title("⚙️ Telemetry Settings")
    broker_host = st.sidebar.text_input("Broker Host", config.mqtt.broker_host)
    broker_port = st.sidebar.number_input("Broker Port", value=config.mqtt.broker_port)
    topic_sub = st.sidebar.text_input("Topic Pattern", "study/+/eeg/observation")
    auto_refresh = st.sidebar.checkbox("Live Update (1s cadence)", value=True)

    _start_mqtt_client(broker_host, int(broker_port), topic_sub)

    if "observations" not in st.session_state:
        st.session_state["observations"] = []
    if "pause_prompt_active" not in st.session_state:
        st.session_state["pause_prompt_active"] = False

    # Drain message queue into session state
    while not shared.message_queue.empty():
        try:
            item = shared.message_queue.get_nowait()
            st.session_state["observations"].append(item)
            if item.get("alert_triggered"):
                st.session_state["pause_prompt_active"] = True
        except queue.Empty:
            break

    obs_list = st.session_state["observations"]
    latest = obs_list[-1] if obs_list else None
    signal_usable = latest.get("is_usable", True) if latest else True

    # -------------------------------------------------------------------------
    # Ethical UI Requirement: Dynamic Greyed-Out Mode for Poor Signal Quality
    # -------------------------------------------------------------------------
    if not signal_usable:
        st.markdown(
            """
            <style>
            .stApp {
                filter: grayscale(85%);
                opacity: 0.70;
                transition: all 0.4s ease-in-out;
            }
            .stMetric {
                opacity: 0.6;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    # -------------------------------------------------------------------------
    # Header & Status Indicators
    # -------------------------------------------------------------------------
    col_title, col_conn = st.columns([3, 1])
    with col_title:
        st.title("🧠 Cognitive Vigilance Assistant")
        st.caption("Edge-First IoHT Architecture | Calm Technology & Privacy-Compliant Monitoring")

    with col_conn:
        conn_badge = "🟢 Connected" if shared.is_connected else "🟡 Offline / Simulating"
        st.subheader(conn_badge)
        if latest and not signal_usable:
            st.warning("⚠️ Sensor contact low. Please adjust headband.", icon="📡")

    st.markdown("---")

    # -------------------------------------------------------------------------
    # Ethical UI Requirement: Minimalist Prompt "Consider a pause"
    # Silent by default, strictly minimalist, dismissible without panic.
    # -------------------------------------------------------------------------
    if st.session_state.get("pause_prompt_active"):
        col_p_text, col_p_btn = st.columns([4, 1])
        with col_p_text:
            st.info("💡 **Consider a pause.**", icon="☕")
        with col_p_btn:
            if st.button("Acknowledge", help="Dismiss prompt and reset notification"):
                st.session_state["pause_prompt_active"] = False
                st.rerun()

    # -------------------------------------------------------------------------
    # Qualitative Session Metrics (Strictly NO absolute probability scores)
    # -------------------------------------------------------------------------
    m1, m2, m3, m4 = st.columns(4)
    total_count = len(obs_list)
    usable_count = sum(1 for o in obs_list if o.get("is_usable"))
    usable_pct = (usable_count / max(total_count, 1)) * 100.0
    alerts_count = sum(1 for o in obs_list if o.get("alert_triggered"))
    current_state = latest["vigilance_state"] if latest else "Standby"

    m1.metric("Monitored Epochs", f"{total_count}")
    m2.metric("Signal Reliability", f"{usable_pct:.0f}%")
    m3.metric("Current State", current_state)
    m4.metric("Pauses Recommended", f"{alerts_count}")

    st.markdown("---")

    # -------------------------------------------------------------------------
    # Qualitative Vigilance Timeline (Nominal vs Low Vigilance vs Signal Artifact)
    # -------------------------------------------------------------------------
    st.subheader("📈 Session Vigilance Rhythm")

    if obs_list:
        df = pd.DataFrame(obs_list)
        df["epoch_idx"] = range(1, len(df) + 1)

        def map_qualitative_level(row):
            if not row["is_usable"]:
                return 0  # Signal Artifact / IND
            return 2 if row["vigilance_state"] == "Low Vigilance" else 1

        df["level"] = df.apply(map_qualitative_level, axis=1)

        fig = go.Figure()

        # Trace 1: Nominal attention epochs (Calm Green)
        df_nom = df[df["level"] == 1]
        if not df_nom.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_nom["epoch_idx"],
                    y=[1] * len(df_nom),
                    mode="markers",
                    name="Nominal Vigilance",
                    marker=dict(size=7, color="#2b8a3e"),
                    hovertemplate="Epoch #%{x}<br>Status: Nominal<extra></extra>",
                )
            )

        # Trace 2: Low vigilance epochs (Amber)
        df_low = df[df["level"] == 2]
        if not df_low.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_low["epoch_idx"],
                    y=[2] * len(df_low),
                    mode="markers",
                    name="Vigilance Decline",
                    marker=dict(size=8, color="#e67700"),
                    hovertemplate="Epoch #%{x}<br>Status: Vigilance Decline<extra></extra>",
                )
            )

        # Trace 3: Indeterminate / Signal Artifact epochs (Muted Gray 'X')
        df_ind = df[df["level"] == 0]
        if not df_ind.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_ind["epoch_idx"],
                    y=[0] * len(df_ind),
                    mode="markers",
                    name="Signal Artifact (IND)",
                    marker=dict(size=8, color="#868e96", symbol="x", opacity=0.7),
                    hovertemplate="Epoch #%{x}<br>Status: Signal Artifact (IND)<extra></extra>",
                )
            )

        fig.update_layout(
            yaxis=dict(
                tickvals=[0, 1, 2],
                ticktext=["Signal Noise (IND)", "Nominal", "Low Vigilance"],
                range=[-0.5, 2.5],
            ),
            xaxis_title="Epoch Sequence (1s window)",
            template="plotly_dark",
            height=340,
            margin=dict(l=20, r=20, t=20, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        st.plotly_chart(fig, use_container_width=True)

        with st.expander("🔍 Clinical Interoperability Inspector (FHIR R4 JSON)"):
            st.json(obs_list[-1])
    else:
        st.info("Awaiting telemetry stream from edge gateway...")

    # Real-time update tick
    if auto_refresh:
        time.sleep(1.0)
        st.rerun()


if __name__ == "__main__":
    main()
