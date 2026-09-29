"""Presentation Layer — Real-Time Streamlit Dashboard.

WHY THIS MODULE EXISTS
----------------------
Clinical monitoring systems need a human-facing visualisation layer that
allows operators, researchers, and clinicians to observe the pipeline's
behaviour in real time.  Without a dashboard, the only output would be
terminal log lines — which are neither actionable nor intuitive for
non-technical users.

This Streamlit dashboard provides:
    1. **Live connection status** to the MQTT broker.
    2. **Metric cards** showing total windows, signal quality percentage,
       number of alerts dispatched, and the latest P(vigilance drop).
    3. **Real-time timeline chart** plotting P(drop) over time with
       visual distinction between usable (blue markers) and indeterminate
       (gray X markers) windows, plus a dashed threshold line.
    4. **Alert banners** that appear when the decision engine dispatches
       a vigilance alert.
    5. **FHIR JSON inspector** for debugging and auditing raw payloads.

HOW IT WORKS
------------
The dashboard runs as a Streamlit application and connects to the same
MQTT broker as the edge gateway, subscribing to the wildcard topic
``study/+/eeg/observation``.  Incoming FHIR R4 Observation payloads are:

    1. Received by a Paho-MQTT background thread.
    2. Parsed from nested FHIR JSON into flat dictionaries.
    3. Pushed into a thread-safe ``queue.Queue``.
    4. Drained by the Streamlit main thread on each 1-second rerun cycle.
    5. Rendered as interactive Plotly charts and metric cards.

THREAD SAFETY
-------------
Streamlit reruns the entire script on every interaction / timer tick.  To
prevent the MQTT client from being recreated on each rerun, we use
``@st.cache_resource`` to maintain a singleton shared state (``_MqttSharedState``)
that persists across reruns.  The MQTT background thread writes to the
shared queue, and the Streamlit main thread reads from it — the
``queue.Queue`` class is inherently thread-safe.
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

# Add parent directory to system path so ``from src.xxx`` imports work
# when Streamlit runs this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config

# Configure logging for the dashboard process.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DashboardApp")


# ---------------------------------------------------------------------------
# Shared state that survives Streamlit reruns via @st.cache_resource
# ---------------------------------------------------------------------------
class _MqttSharedState:
    """Thread-safe container for data shared between the MQTT background
    thread and the Streamlit main thread.

    This class is instantiated exactly once via ``@st.cache_resource`` and
    persists for the entire Streamlit session.  It holds:
        - ``message_queue``: Thread-safe FIFO queue for parsed observations.
        - ``is_connected``: Boolean flag updated by MQTT callbacks.
        - ``client``: Reference to the Paho MQTT client instance.
    """

    def __init__(self):
        self.message_queue: queue.Queue = queue.Queue()
        self.is_connected: bool = False
        self.client = None


@st.cache_resource
def _get_shared_state() -> _MqttSharedState:
    """Returns the singleton shared state instance.

    ``@st.cache_resource`` ensures this is called only once per Streamlit
    server process, preventing duplicate MQTT connections on reruns.
    """
    return _MqttSharedState()


def parse_fhir_observation(payload_str: str) -> dict:
    """Parses an incoming FHIR R4 Observation JSON payload into a flat
    dictionary suitable for Plotly charts and Streamlit metric cards.

    The FHIR Observation uses a component-based structure where each
    measurement (P(drop), α/θ ratio, quality status, alert triggered) is
    nested inside a ``component[]`` array with its own ``code.coding[]``.
    This function flattens that structure into simple key-value pairs.

    Args:
        payload_str: Raw JSON string received from the MQTT message.

    Returns:
        A flat dictionary with keys: ``timestamp``, ``user_id``,
        ``p_vigilance_drop``, ``alpha_theta_ratio``, ``quality_status``,
        ``is_usable``, and ``alert_triggered``.
        Returns an empty dict if parsing fails.
    """
    try:
        data = json.loads(payload_str)
        effective_time = data.get("effectiveDateTime", datetime.now(timezone.utc).isoformat())

        # Extract the patient identifier from the FHIR subject reference.
        subject = data.get("subject", {}).get("reference", "Patient/unknown")
        user_id = subject.replace("Patient/", "")

        # Walk through the FHIR components to extract individual measurements.
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


@st.cache_resource
def _start_mqtt_client(broker_host: str, broker_port: int, topic: str):
    """Starts a single background MQTT subscriber client.

    This function is cached with ``@st.cache_resource`` so that it executes
    only once per unique parameter combination.  Subsequent Streamlit reruns
    reuse the existing MQTT connection instead of creating duplicates.

    The client subscribes to the specified topic (typically a wildcard like
    ``study/+/eeg/observation``) and pushes parsed observations into the
    shared thread-safe queue.

    DNS resolution is attempted first for the configured broker host.  If
    it fails (common when running outside Docker), the function falls back
    to ``localhost``.
    """
    import socket
    import paho.mqtt.client as mqtt

    shared = _get_shared_state()

    # Resolve hostname — fall back to localhost when running outside Docker.
    # Inside Docker Compose, the broker hostname is the service name
    # ("mosquitto"), which is only resolvable within the Docker network.
    target_host = broker_host
    try:
        socket.gethostbyname(target_host)
    except socket.gaierror:
        target_host = "localhost"

    def on_connect(client, userdata, flags, rc, properties=None):
        """Callback when the MQTT connection is established.
        Subscribes to the observation topic immediately after connecting.
        """
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            logger.info(f"Dashboard MQTT Client connected to {target_host}:{broker_port}.")
            client.subscribe(topic)
            shared.is_connected = True

    def on_disconnect(client, userdata, *args, **kwargs):
        """Callback when the MQTT connection is lost."""
        shared.is_connected = False
        logger.warning("Dashboard MQTT Client disconnected.")

    def on_message(client, userdata, msg):
        """Callback for each incoming MQTT message.
        Parses the FHIR payload and pushes it into the shared queue
        for the Streamlit main thread to consume.
        """
        try:
            payload = msg.payload.decode("utf-8")
            parsed = parse_fhir_observation(payload)
            if parsed:
                shared.message_queue.put(parsed)
        except Exception as e:
            logger.error(f"MQTT on_message error: {e}")

    try:
        # Automatic Paho API version detection (same pattern as mqtt_client.py).
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="streamlit-dashboard-listener",
            )
        except (AttributeError, ValueError):
            client = mqtt.Client(client_id="streamlit-dashboard-listener")

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        shared.client = client

        # connect_async + loop_start runs the MQTT network loop in a
        # background daemon thread that persists across Streamlit reruns.
        client.connect_async(target_host, broker_port, 60)
        client.loop_start()
        logger.info(f"MQTT background thread started for {target_host}:{broker_port}")
    except Exception as e:
        logger.warning(f"Could not connect to MQTT broker ({target_host}:{broker_port}): {e}")


def main():
    """Main entry point for the Streamlit dashboard application.

    This function is re-executed on every Streamlit rerun cycle (approximately
    every 1 second when auto-refresh is enabled).  It:
        1. Configures the page layout and sidebar controls.
        2. Starts the cached MQTT subscriber (once).
        3. Drains new observations from the shared queue into session state.
        4. Renders metric cards, timeline charts, and alert banners.
        5. Triggers a 1-second delayed rerun for real-time updates.
    """
    st.set_page_config(
        page_title="EEG Vigilance Edge Dashboard",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Load configuration to get default broker settings and thresholds.
    config = load_config()
    default_broker_host = config.mqtt.broker_host

    # --- Sidebar: System Control Panel ---
    st.sidebar.title("⚙️ System Control")
    broker_host = st.sidebar.text_input("MQTT Broker Host", default_broker_host)
    broker_port = st.sidebar.number_input("MQTT Broker Port", value=config.mqtt.broker_port)
    topic_sub = st.sidebar.text_input("Subscribe Topic Pattern", "study/+/eeg/observation")
    auto_refresh = st.sidebar.checkbox("Auto-refresh UI (1s interval)", value=True)

    # Start the MQTT listener (cached — only runs once per parameter set).
    _start_mqtt_client(broker_host, int(broker_port), topic_sub)

    # Get the singleton shared state that persists across reruns.
    shared = _get_shared_state()

    # --- Initialise Streamlit session state containers ---
    # session_state persists across reruns but is reset when the user
    # refreshes the browser tab.
    if "observations" not in st.session_state:
        st.session_state["observations"] = []
    if "last_alert" not in st.session_state:
        st.session_state["last_alert"] = None

    # --- Drain the shared queue into session state ---
    # The MQTT background thread pushes parsed observations into the queue;
    # here we pull them out and append them to the session-state list.
    drained = 0
    while not shared.message_queue.empty():
        try:
            item = shared.message_queue.get_nowait()
            st.session_state["observations"].append(item)
            if item.get("alert_triggered"):
                st.session_state["last_alert"] = item
            drained += 1
        except queue.Empty:
            break

    obs_list = st.session_state["observations"]

    # --- Header Section ---
    col_title, col_conn = st.columns([3, 1])
    with col_title:
        st.title("🧠 Single-Channel EEG Vigilance Monitor")
        st.caption("Edge-First IoHT Architecture | FHIR R4 Interoperability & MQTT Stream")
    with col_conn:
        status_text = "🟢 MQTT Connected" if shared.is_connected else "🟡 Offline / Simulating"
        st.subheader(status_text)
        if obs_list:
            st.text(f"Subject: {obs_list[-1]['user_id']}")

    st.markdown("---")

    # --- Alert Banner Display ---
    # Shows a prominent warning when the decision engine has dispatched
    # a vigilance alert, prompting the user to take action.
    last_alert = st.session_state["last_alert"]
    if last_alert:
        st.warning(
            f"🔔 **Vigilance Alert Triggered** ({last_alert['timestamp'][:19]}): "
            f"Sustained vigilance drop detected! **Consider taking a short pause.**",
            icon="⚠️",
        )

    # --- Metrics Row ---
    # Four KPI cards summarising the current session state.
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

    # --- Real-Time Session Timeline Plot ---
    st.subheader("📈 Real-Time Vigilance Drop & Quality Timeline")

    if obs_list:
        df = pd.DataFrame(obs_list)
        df["time_idx"] = range(1, len(df) + 1)

        # Build an interactive Plotly figure with two trace types:
        #   1. Usable windows: solid blue line with circle markers.
        #   2. Indeterminate windows: gray X markers (translucent).
        fig = go.Figure()

        df_usable = df[df["is_usable"] == True]
        df_indet = df[df["is_usable"] == False]

        # Usable windows trace
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

        # Indeterminate windows trace
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

        # Threshold reference line (dashed red at P = 0.70)
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

        # FHIR JSON Inspector — expandable section for debugging.
        with st.expander("🔍 Inspect Latest FHIR R4 Observation Payload"):
            st.json(obs_list[-1])
    else:
        st.info("Waiting for incoming MQTT FHIR Observation stream... Ensure edge gateway pipeline is running.")

    # --- Auto-refresh mechanism ---
    # When enabled, the UI reruns every 1 second to pull new observations
    # from the shared queue and update charts / metrics.
    if auto_refresh:
        time.sleep(1.0)
        st.rerun()


if __name__ == "__main__":
    main()
