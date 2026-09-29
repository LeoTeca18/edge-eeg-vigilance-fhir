"""Main Edge Orchestrator Pipeline — coordinates all four architecture layers.

WHY THIS MODULE EXISTS
----------------------
The individual modules (simulator, quality gate, decision engine, FHIR
builder, MQTT publisher) are each responsible for a single concern.  This
module is the **orchestrator** that wires them together into a coherent,
real-time processing pipeline.

Without a central orchestrator, each module would need to know about the
others, creating tight coupling and making the system difficult to test,
modify, or extend.

HOW IT WORKS
------------
``run_pipeline()`` executes the following loop at approximately 1 Hz:

    1. **Acquire** a window from ``StreamSimulator`` (synthetic or file).
    2. **Assess quality** via ``QualityGate`` → USABLE / INDETERMINATE.
    3. **Evaluate decision** via ``AlertDecisionEngine`` → trigger or suppress.
    4. **Build FHIR R4 Observation** via ``build_fhir_observation()``.
    5. **Publish** the JSON payload to MQTT via ``MqttPublisher``.
    6. **Log** telemetry (quality status, drop probability, alert state).

The loop runs indefinitely (production mode) or for a fixed number of
windows (test/demo mode via ``--max-windows``).  It handles graceful
shutdown on ``Ctrl+C`` (KeyboardInterrupt).

COMMAND-LINE INTERFACE
----------------------
    python src/gateway_main.py --config config/config.yaml --max-windows 100
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to Python path when running the script directly
# (e.g. ``python src/gateway_main.py``).  This ensures that ``from src.xxx``
# imports resolve correctly regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.decision_layer import AlertDecisionEngine
from src.fhir_builder import build_fhir_observation, observation_to_json
from src.mqtt_client import MqttPublisher
from src.quality_gate import QualityGate
from src.simulator import StreamSimulator

# Configure structured logging with human-readable timestamps.
# All pipeline output goes to stdout so Docker Compose can aggregate logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("EdgeGateway")


def run_pipeline(config_path: str = "config/config.yaml", max_windows: int = None) -> None:
    """Executes the main edge gateway processing loop.

    This function is the entry point for the entire pipeline.  It:
        1. Loads and validates the YAML configuration.
        2. Instantiates all core components via dependency injection.
        3. Connects to the MQTT broker (with graceful degradation if offline).
        4. Enters the stream-process-publish loop.
        5. Handles shutdown cleanly on interruption.

    Args:
        config_path: Filesystem path to the YAML configuration file.
        max_windows: Optional maximum number of windows to process before
                     exiting.  ``None`` means infinite (stop with Ctrl+C).
    """
    logger.info("Initializing Edge-First EEG Vigilance Architecture...")
    config = load_config(config_path)

    # -------------------------------------------------------------------------
    # Step 1: Instantiate Core Components
    # Each component receives only its relevant sub-configuration, enforcing
    # separation of concerns and making unit testing straightforward.
    # -------------------------------------------------------------------------
    simulator = StreamSimulator(
        input_file=config.simulator.input_file,
        stream_interval_seconds=config.simulator.stream_interval_seconds,
        user_id=config.simulator.user_id,
        device_id=config.simulator.device_id,
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
    # Step 2: Establish MQTT Broker Connection
    # The pipeline continues even if the broker is unreachable — observations
    # are still processed and logged locally; only MQTT transport is skipped.
    # -------------------------------------------------------------------------
    logger.info(f"Connecting to MQTT Broker at {config.mqtt.broker_host}:{config.mqtt.broker_port}...")
    connected = mqtt_publisher.connect(retry_count=2, retry_delay=0.2)
    if not connected:
        logger.warning("Proceeding without active MQTT connection (messages will be logged locally).")

    # Format the MQTT topic with the subject's user_id.
    topic = config.mqtt.topic_template.format(user_id=config.simulator.user_id)
    logger.info(f"Pipeline Active. Publishing FHIR Observations to MQTT Topic: '{topic}'")

    # -------------------------------------------------------------------------
    # Step 3: Stream & Process Loop
    # Each iteration processes one EEG window through the full pipeline:
    #   Acquire → Quality Gate → Decision Engine → FHIR Build → MQTT Publish
    # -------------------------------------------------------------------------
    processed_count = 0
    try:
        for window in simulator.stream_windows(max_windows=max_windows):
            window_idx = window.get("window_id", processed_count)
            p_drop = window.get("p_vigilance_drop", 0.0)

            # Step A: Evaluate Signal Quality
            # Determines if the window's EEG signal is physically trustworthy.
            quality_eval = quality_gate.evaluate_window(window)

            # Step B: Evaluate Stateful Decision Engine
            # Checks persistence, refractory, and budget rules.
            ts_iso = window.get("timestamp")
            dt_obj = datetime.fromisoformat(ts_iso) if ts_iso else datetime.now(timezone.utc)
            decision_result = decision_engine.process_window(
                p_vigilance_drop=p_drop,
                is_usable=quality_eval.is_usable,
                timestamp=dt_obj,
            )

            # Step C: Build FHIR R4 Observation Resource
            # Converts all telemetry + quality + decision data into a
            # standardised FHIR Observation with four components.
            fhir_obs = build_fhir_observation(
                window=window,
                quality_eval=quality_eval,
                decision_result=decision_result,
                user_id=config.simulator.user_id,
                device_id=config.simulator.device_id,
            )

            fhir_json = observation_to_json(fhir_obs)

            # Step D: Publish Payload over MQTT
            # Returns True if delivered, False if offline (pipeline continues).
            pub_status = mqtt_publisher.publish(topic, fhir_json, qos=1)

            # Step E: Telemetry & Logging Output
            # Provides a concise per-window summary for debugging and
            # operational monitoring.
            status_symbol = "[OK]" if quality_eval.is_usable else "[REJECTED]"
            alert_symbol = "[ALERT TRIGGERED]" if decision_result.trigger_alert else "[NORMAL]"

            logger.info(
                f"Window #{window_idx:03d} | Status: {status_symbol:<10} | "
                f"P(drop): {p_drop:.2f} | Sustained: {decision_result.sustained_seconds:02d}s | "
                f"State: {alert_symbol} | Published: {pub_status}"
            )

            if not quality_eval.is_usable:
                logger.debug(f"  Reason: {', '.join(quality_eval.reasons)}")

            if decision_result.trigger_alert:
                logger.warning(f"  --> ALERT DISPATCHED: {decision_result.reason}")

            processed_count += 1

    except KeyboardInterrupt:
        logger.info("Pipeline stopped by user (KeyboardInterrupt).")
    finally:
        # Ensure clean shutdown: stop MQTT background thread and close socket.
        mqtt_publisher.disconnect()
        logger.info(f"Pipeline shutdown complete. Total windows processed: {processed_count}")


if __name__ == "__main__":
    # Command-line argument parser for standalone execution.
    parser = argparse.ArgumentParser(description="Edge Gateway EEG Vigilance Orchestrator")
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
    args = parser.parse_args()

    run_pipeline(config_path=args.config, max_windows=args.max_windows)
