"""Presentation Layer: Real-Time Streamlit Dashboard.

Subscribes to MQTT topic 'study/+/eeg/observation', parses FHIR R4 Observations,
and renders real-time session telemetry, quality metrics, timeline charts,
and alert notifications.
"""

import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add parent directory to system path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashboardApp")

# Initialize global thread-safe message queue
if "message_queue" not in st.session_state:
    st.session_state["message_queue"] = queue.Queue()

if "observations" not in st.session_state:
    st.session_state["observations"] = []

if "mqtt_connected" not in st.session_state:
    st.session_state["mqtt_connected"] = False

if "last_alert" not in st.session_state:
    st.session_state["last_alert"] = None


def parse_fhir_observation(payload_str: str) -> dict:
    """Parses incoming FHIR R4 Observation JSON into a flat dict for plotting.

    Args:
        payload_str: Raw JSON string from MQTT message.

    Returns:
        Flattened observation dictionary.
    """
    try:
        data = json.loads(payload_str)
        effective_time = data.get("effectiveDateTime", datetime.now(timezone.utc).isoformat())
        subject = data.get("subject", {}).get("reference", "Patient/unknown")
        user_id = subject.replace("Patient/", "")

        components = data.get("component", [])
        p_drop = 0.0
        at_ratio = 1.0
        quality_status = "usable"
        alert_triggered = False

        for comp in components:
            coding_list = comp.get("code", {}).get("coding", [])
            for c in coding_list:
                code_val = c.get("code")
                if code_val == "p-vigilance-drop":
                    p_drop = comp.get("valueQuantity", {}).get("value", 0.0)
                elif code_val == "alpha-theta-ratio":
                    at_ratio = comp.get("valueQuantity", {}).get("value", 1.0)
                elif code_val == "quality-gate-status":
                    quality_status = comp.get("valueString", "usable")
                elif code_val == "alert-triggered":
                    alert_triggered = comp.get("valueBoolean", False)

        return {
            "timestamp": effective_time,
            "user_id": user_id,
            "p_vigilance_drop": p_drop,
            "alpha_theta_ratio": at_ratio,
            "quality_status": quality_status,
            "is_usable": (quality_status == "usable"),
            "alert_triggered": alert_triggered,
        }
    except Exception as e:
        logger.error(f"Error parsing FHIR observation payload: {e}")
        return {}


def start_mqtt_client(broker_host: str, broker_port: int, topic: str):
    """Starts background MQTT client listening for FHIR Observations."""
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, rc, properties=None):
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            logger.info("Dashboard MQTT Client connected.")
            client.subscribe(topic)
            st.session_state["mqtt_connected"] = True

    def on_message(client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
            parsed = parse_fhir_observation(payload)
            if parsed:
                st.session_state["message_queue"].put(parsed)
        except Exception as e:
            logger.error(f"MQTT on_message error: {e}")

    try:
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="streamlit-dashboard-listener",
            )
        except (AttributeError, ValueError):
            client = mqtt.Client(client_id="streamlit-dashboard-listener")

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(broker_host, broker_port, 60)
        client.loop_start()
    except Exception as e:
        logger.warning(f"Could not connect to MQTT broker in dashboard thread: {e}")


def main():
    st.set_page_config(
        page_title="EEG Vigilance Edge Dashboard",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Load configuration
    config = load_config()

    # Sidebar settings
    st.sidebar.title("⚙️ System Control")
    broker_host = st.sidebar.text_input("MQTT Broker Host", config.mqtt.broker_host)
    broker_port = st.sidebar.number_input("MQTT Broker Port", value=config.mqtt.broker_port)
    topic_sub = st.sidebar.text_input("Subscribe Topic Pattern", "study/+/eeg/observation")
    auto_refresh = st.sidebar.checkbox("Auto-refresh UI (1s interval)", value=True)

    # Launch background MQTT listener once
    if "mqtt_started" not in st.session_state:
        start_mqtt_client(broker_host, int(broker_port), topic_sub)
        st.session_state["mqtt_started"] = True

    # Empty queue into session state observations list
    q = st.session_state["message_queue"]
    while not q.empty():
        item = q.get_nowait()
        st.session_state["observations"].append(item)
        if item.get("alert_triggered"):
            st.session_state["last_alert"] = item

    obs_list = st.session_state["observations"]

    # Header section
    col_title, col_conn = st.columns([3, 1])
    with col_title:
        st.title("🧠 Single-Channel EEG Vigilance Monitor")
        st.caption("Edge-First IoHT Architecture | FHIR R4 Interoperability & MQTT Stream")
    with col_conn:
        status_text = "🟢 MQTT Connected" if st.session_state["mqtt_connected"] else "🟡 Offline / Simulating"
        st.subheader(status_text)
        if obs_list:
            st.text(f"Subject: {obs_list[-1]['user_id']}")

    st.markdown("---")

    # Alert Banner Display
    last_alert = st.session_state["last_alert"]
    if last_alert:
        st.warning(
            f"🔔 **Vigilance Alert Triggered** ({last_alert['timestamp'][:19]}): "
            f"Sustained vigilance drop detected! **Consider taking a short pause.**",
            icon="⚠️",
        )

    # Metrics Row
    m1, m2, m3, m4 = st.columns(4)

    total_count = len(obs_list)
    usable_count = sum(1 for o in obs_list if o.get("is_usable"))
    usable_pct = (usable_count / max(total_count, 1)) * 100.0
    alerts_count = sum(1 for o in obs_list if o.get("alert_triggered"))
    latest_p = obs_list[-1]["p_vigilance_drop"] if obs_list else 0.0

    m1.metric("Total Windows", f"{total_count}")
    m2.metric("Signal Quality (Usable)", f"{usable_pct:.1f}%")
    m3.metric("Alerts Dispatched", f"{alerts_count}")
    m4.metric("Latest P(Vigilance Drop)", f"{latest_p:.2f}")

    st.markdown("---")

    # Real-Time Session Timeline Plot
    st.subheader("📈 Real-Time Vigilance Drop & Quality Timeline")

    if obs_list:
        df = pd.DataFrame(obs_list)
        df["time_idx"] = range(1, len(df) + 1)

        # Plotly figure construction
        fig = go.Figure()

        # Separate usable vs indeterminate windows
        df_usable = df[df["is_usable"] == True]
        df_indet = df[df["is_usable"] == False]

        # Usable Windows (Solid blue line + markers)
        fig.add_trace(
            go.Scatter(
                x=df_usable["time_idx"],
                y=df_usable["p_vigilance_drop"],
                mode="lines+markers",
                name="Usable EEG Window",
                line=dict(color="#1f77b4", width=2),
                marker=dict(size=6, color="#1f77b4"),
                hovertemplate="Window #%{x}<br>P(Drop): %{y:.2f}<br>Quality: Usable",
            )
        )

        # Indeterminate Windows (Gray markers / translucent)
        if not df_indet.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_indet["time_idx"],
                    y=df_indet["p_vigilance_drop"],
                    mode="markers",
                    name="Indeterminate (Rejected)",
                    marker=dict(size=8, color="#808080", symbol="x", opacity=0.6),
                    hovertemplate="Window #%{x}<br>P(Drop): %{y:.2f}<br>Quality: Indeterminate",
                )
            )

        # Threshold Line (0.70)
        fig.add_shape(
            type="line",
            x0=1,
            x1=len(df),
            y0=config.decision.probability_threshold,
            y1=config.decision.probability_threshold,
            line=dict(color="Red", width=2, dash="dash"),
        )
        fig.add_annotation(
            x=len(df),
            y=config.decision.probability_threshold,
            text="Drop Threshold (0.70)",
            showarrow=False,
            yshift=10,
            font=dict(color="Red"),
        )

        fig.update_layout(
            title="Sustained Vigilance Probability P(drop) over Time",
            xaxis_title="Window Sequence Index (1s stride)",
            yaxis_title="P(vigilance drop)",
            yaxis=dict(range=[0, 1.05]),
            template="plotly_dark",
            height=450,
        )

        st.plotly_chart(fig, use_container_width=True)

        # Recent FHIR JSON Inspector
        with st.expander("🔍 Inspect Latest FHIR R4 Observation Payload"):
            st.json(obs_list[-1])
    else:
        st.info("Waiting for incoming MQTT FHIR Observation stream... Ensure edge gateway pipeline is running.")

    # Auto-refresh mechanism
    if auto_refresh:
        time.sleep(1.0)
        st.rerun()


if __name__ == "__main__":
    main()
