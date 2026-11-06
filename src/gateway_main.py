"""Main Edge Orchestrator Pipeline — coordinates all four architecture layers.

WHY THIS MODULE EXISTS
----------------------
The individual modules (simulator, quality gate, decision engine, FHIR
builder, local SQLite store, MQTT publisher) are each responsible for a
single concern. This module is the central orchestrator that wires them
together into a robust, real-time, offline-first processing pipeline.

KEY ARCHITECTURAL RESPONSIBILITIES
----------------------------------
    1. **Offline-First Resilience**: Persists every FHIR Observation to a local
       SQLite database before attempting network transport. If the MQTT broker
       is unreachable, edge inference continues uninterrupted.
    2. **Session FHIR Device Lifecycle**: Instantiates and persists the formal
       FHIR R4 Device resource on session startup so that downstream observation
       references (Observation.device) are anchored to a registered hardware entity.
    3. **Live Demo Orchestration**: Exposes comprehensive CLI controls to run
       continuous demonstrations, switch between synthetic generation and dataset
       file replay, configure playback cadence, and enable cyclic looping.
    4. **Zero-Cloud Raw EEG**: Processes signals strictly at the edge; raw EEG
       time-series are never streamed to remote networks.
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.decision_layer import AlertDecisionEngine
from src.fhir_builder import build_fhir_device, build_fhir_observation, observation_to_json
from src.local_store import LocalFhirStore
from src.mqtt_client import MqttPublisher
from src.privacy import pseudonymize_identifier
from src.quality_gate import QualityGate
from src.simulator import StreamSimulator

# Configure structured logging with human-readable timestamps.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("EdgeGateway")


def run_pipeline(
    config_path: str = "config/config.yaml",
    max_windows: Optional[int] = None,
    input_file: Optional[str] = None,
    loop_playback: Optional[bool] = None,
    stream_interval: Optional[float] = None,
    user_id_override: Optional[str] = None,
    device_id_override: Optional[str] = None,
) -> None:
    """Executes the main edge gateway processing loop.

    Args:
        config_path: Filesystem path to the YAML configuration file.
        max_windows: Optional maximum number of windows to process.
        input_file: Optional override for the dataset/CSV replay file.
        loop_playback: Optional override for continuous loop playback.
        stream_interval: Optional override for window emission interval.
        user_id_override: Optional override for subject user ID.
        device_id_override: Optional override for sensor device ID.
    """
    logger.info("Initializing Edge-First EEG Vigilance Architecture...")
    config = load_config(config_path)

    # Apply runtime CLI overrides
    effective_input_file = input_file if input_file is not None else config.simulator.input_file
    effective_interval = stream_interval if stream_interval is not None else config.simulator.stream_interval_seconds
    effective_loop = loop_playback if loop_playback is not None else getattr(config.simulator, "loop_playback", True)
    raw_user_id = user_id_override if user_id_override is not None else config.simulator.user_id
    effective_device_id = device_id_override if device_id_override is not None else config.simulator.device_id

    # LGPD Privacy: Ensure subject identifier is pseudonymised (Art. 13)
    if config.privacy.enforce_pseudonymization:
        effective_user_id = pseudonymize_identifier(raw_user_id)
        if effective_user_id != raw_user_id:
            logger.info(f"LGPD Pseudonymisation applied: '{raw_user_id}' -> '{effective_user_id}'")
    else:
        effective_user_id = raw_user_id

    # -------------------------------------------------------------------------
    # Step 1: Instantiate Core Components
    # -------------------------------------------------------------------------
    simulator = StreamSimulator(
        input_file=effective_input_file,
        stream_interval_seconds=effective_interval,
        user_id=effective_user_id,
        device_id=effective_device_id,
        loop_playback=effective_loop,
    )

    quality_gate = QualityGate(config.quality)
    decision_engine = AlertDecisionEngine(config.decision)

    mqtt_publisher = MqttPublisher(
        broker_host=config.mqtt.broker_host,
        broker_port=config.mqtt.broker_port,
        client_id=config.mqtt.client_id,
        keepalive=config.mqtt.keepalive,
    )

    # -------------------------------------------------------------------------
    # Step 1b: Initialise Local FHIR Store (Offline-First Persistence)
    # -------------------------------------------------------------------------
    local_store = LocalFhirStore()
    logger.info(f"Local FHIR Store initialised at '{local_store.db_path}'")

    # LGPD Storage Limitation: Purge old published observations on startup (Art. 16)
    if config.privacy.retention_days > 0:
        purged = local_store.purge_old_published_observations(config.privacy.retention_days)
        if purged > 0:
            logger.info(f"LGPD Retention Policy: Cleaned {purged} old published records from edge store.")

    # -------------------------------------------------------------------------
    # Step 1c: Register Session FHIR R4 Device Resource
    # Persists the Device resource locally so that Observation.device references
    # are resolvable. Also publishes to MQTT topic if connected.
    # -------------------------------------------------------------------------
    fhir_device = build_fhir_device(
        device_id=effective_device_id,
        manufacturer="NeuroSky",
        model_name="TGAM Single-Channel EEG Headband",
    )
    device_json = fhir_device.model_dump_json(exclude_none=True)
    local_store.save_observation(
        observation_id=f"device-{effective_device_id}",
        subject_id=effective_user_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        fhir_json=device_json,
        mqtt_published=False,
    )
    logger.info(f"Session FHIR Device resource initialized: '{fhir_device.id}'")

    # -------------------------------------------------------------------------
    # Step 2: Establish MQTT Broker Connection
    # -------------------------------------------------------------------------
    logger.info(f"Connecting to MQTT Broker at {config.mqtt.broker_host}:{config.mqtt.broker_port}...")
    connected = mqtt_publisher.connect(retry_count=2, retry_delay=0.2)
    if not connected:
        logger.warning("Proceeding without active MQTT connection (messages will be persisted locally).")
    else:
        # Publish Device metadata to topic study/<user>/eeg/device
        device_topic = f"study/{effective_user_id}/eeg/device"
        if mqtt_publisher.publish(device_topic, device_json, qos=1):
            local_store.mark_as_published(f"device-{effective_device_id}")

    topic = config.mqtt.topic_template.format(user_id=effective_user_id)
    mode_descr = f"Replay ('{effective_input_file}', loop={effective_loop})" if effective_input_file else "Synthetic Generator"
    logger.info(f"Pipeline Active ({mode_descr}). Publishing FHIR Observations to '{topic}'")

    # -------------------------------------------------------------------------
    # Step 3: Stream & Process Loop
    # -------------------------------------------------------------------------
    processed_count = 0
    try:
        for window in simulator.stream_windows(max_windows=max_windows):
            window_idx = window.get("window_id", processed_count)
            p_drop = window.get("p_vigilance_drop", 0.0)

            # Step A: Evaluate Signal Quality
            quality_eval = quality_gate.evaluate_window(window)

            # Step B: Evaluate Stateful Decision Engine
            ts_iso = window.get("timestamp")
            dt_obj = datetime.fromisoformat(ts_iso) if ts_iso else datetime.now(timezone.utc)
            decision_result = decision_engine.process_window(
                p_vigilance_drop=p_drop,
                is_usable=quality_eval.is_usable,
                timestamp=dt_obj,
            )

            # Step C: Build FHIR R4 Observation Resource
            fhir_obs = build_fhir_observation(
                window=window,
                quality_eval=quality_eval,
                decision_result=decision_result,
                user_id=effective_user_id,
                device_id=effective_device_id,
            )
            fhir_json = observation_to_json(fhir_obs)

            # Step C2: Write-ahead persistence to local SQLite store
            local_store.save_observation(
                observation_id=fhir_obs.id,
                subject_id=effective_user_id,
                timestamp=window.get("timestamp", ""),
                fhir_json=fhir_json,
                mqtt_published=False,
            )

            # Step D: Publish Payload over MQTT
            pub_status = mqtt_publisher.publish(topic, fhir_json, qos=1)
            if pub_status:
                local_store.mark_as_published(fhir_obs.id)

            # Step E: Diagnostic Telemetry Log
            status_label = "[OK]" if quality_eval.is_usable else "[REJECTED: IND]"
            alert_label = "[ALERT DISPATCHED]" if decision_result.trigger_alert else "[NORMAL]"

            p_drop_display = f"{p_drop:.2f}" if quality_eval.is_usable else "IND"
            logger.info(
                f"Window #{window_idx:03d} | Signal: {status_label:<15} | "
                f"P(drop): {p_drop_display:<4} | Sustained: {decision_result.sustained_seconds:02d}s | "
                f"State: {alert_label} | MQTT: {pub_status}"
            )

            if not quality_eval.is_usable:
                logger.debug(f"  Quality Gate Failure Reasons: {', '.join(quality_eval.reasons)}")

            if decision_result.trigger_alert:
                logger.warning(f"  --> ALERT DISPATCHED: {decision_result.reason}")

            processed_count += 1

    except KeyboardInterrupt:
        logger.info("Pipeline stopped by user (KeyboardInterrupt).")
    finally:
        mqtt_publisher.disconnect()
        local_store.close()
        logger.info(f"Pipeline shutdown complete. Total windows processed: {processed_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Edge Gateway EEG Vigilance Orchestrator & Demo Runner")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--max-windows",
        type=int,
        default=None,
        help="Maximum windows to process before exiting (default: infinite loop)",
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default=None,
        help="Path to EEG dataset or app export file (.csv / .xlsx) for live replay",
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        default=False,
        help="Disable continuous loop replay when replaying from file",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Cadence in seconds between window yields (default: 1.0s)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Subject identifier override",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default=None,
        help="Device identifier override",
    )
    args = parser.parse_args()

    run_pipeline(
        config_path=args.config,
        max_windows=args.max_windows,
        input_file=args.input_file,
        loop_playback=(not args.no_loop),
        stream_interval=args.interval,
        user_id_override=args.user_id,
        device_id_override=args.device_id,
    )
