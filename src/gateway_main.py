"""Main Edge Orchestrator Pipeline.

Coordinates Acquisition (Simulator), Quality Assessment (QualityGate),
Decision Rules (AlertDecisionEngine), Interoperability (FHIRBuilder),
and Transport (MqttPublisher).
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path if needed when running script directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import load_config
from src.decision_layer import AlertDecisionEngine
from src.fhir_builder import build_fhir_observation, observation_to_json
from src.mqtt_client import MqttPublisher
from src.quality_gate import QualityGate
from src.simulator import StreamSimulator

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("EdgeGateway")


def run_pipeline(config_path: str = "config/config.yaml", max_windows: int = None) -> None:
    """Executes the main edge gateway processing loop.

    Args:
        config_path: Path to configuration YAML file.
        max_windows: Optional maximum windows to process (useful for tests/demos).
    """
    logger.info("Initializing Edge-First EEG Vigilance Architecture...")
    config = load_config(config_path)

    # 1. Instantiate Core Components
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

    # 2. Establish MQTT Broker Connection
    logger.info(f"Connecting to MQTT Broker at {config.mqtt.broker_host}:{config.mqtt.broker_port}...")
    connected = mqtt_publisher.connect(retry_count=2, retry_delay=0.2)
    if not connected:
        logger.warning("Proceeding without active MQTT connection (messages will be logged locally).")

    topic = config.mqtt.topic_template.format(user_id=config.simulator.user_id)
    logger.info(f"Pipeline Active. Publishing FHIR Observations to MQTT Topic: '{topic}'")

    # 3. Stream & Process Loop
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
                user_id=config.simulator.user_id,
                device_id=config.simulator.device_id,
            )

            fhir_json = observation_to_json(fhir_obs)

            # Step D: Publish Payload over MQTT
            pub_status = mqtt_publisher.publish(topic, fhir_json, qos=1)

            # Step E: Telemetry & Logging Output
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
        mqtt_publisher.disconnect()
        logger.info(f"Pipeline shutdown complete. Total windows processed: {processed_count}")


if __name__ == "__main__":
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
