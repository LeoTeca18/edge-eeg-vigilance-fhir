"""Configuration loader module — validates and merges YAML config with env vars.

WHY THIS MODULE EXISTS
----------------------
Healthcare IoT systems need a single, auditable source of truth for every
tuneable parameter (broker addresses, alert thresholds, quality limits, etc.).
Hardcoding these values would make the system inflexible and difficult to
reproduce in different deployment environments (local dev, Docker, CI).

This module solves that problem by:
    1. Reading a YAML configuration file (``config/config.yaml``).
    2. Validating every field through **Pydantic models** — guaranteeing type
       safety, default fallback values, and human-readable error messages.
    3. Allowing selective overrides via **environment variables** (e.g.
       ``MQTT_BROKER_HOST``), which is critical when the same image runs in
       Docker Compose with a different broker hostname (``mosquitto`` service
       name) than on the developer's bare-metal machine (``localhost``).

HOW IT WORKS
------------
``load_config(path)`` reads the YAML file, unpacks it into nested Pydantic
models (``MqttConfig``, ``DecisionConfig``, ``SimulatorConfig``,
``QualityConfig``), and returns a fully validated ``AppConfig`` instance.
If the file does not exist, safe defaults are used.  After loading, the
function checks for ``MQTT_BROKER_HOST`` in the process environment and
patches the broker host accordingly.
"""

import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class MqttConfig(BaseModel):
    """MQTT connection parameters used by both the edge gateway publisher
    and the Streamlit dashboard subscriber.

    These defaults target a local development environment where the Mosquitto
    broker runs on ``localhost:1883``.  Inside Docker Compose the broker host
    is overridden to the container service name (``mosquitto``) through the
    ``MQTT_BROKER_HOST`` environment variable — see ``load_config()``.
    """
    broker_host: str = Field(
        default="localhost",
        description="MQTT Broker Hostname (overridable via MQTT_BROKER_HOST env var)",
    )
    broker_port: int = Field(
        default=1883,
        description="MQTT Broker Port — 1883 is the standard unencrypted MQTT port",
    )
    topic_template: str = Field(
        default="study/{user_id}/eeg/observation",
        description=(
            "MQTT topic pattern.  The ``{user_id}`` placeholder is formatted "
            "at publish-time so each subject's data stream lives on a separate "
            "topic, enabling per-patient subscription on the dashboard."
        ),
    )
    client_id: str = Field(
        default="edge-gateway-01",
        description="MQTT client identifier — must be unique per broker session",
    )
    keepalive: int = Field(
        default=60,
        description="Keepalive interval in seconds sent to the broker to maintain the TCP session",
    )


class DecisionConfig(BaseModel):
    """Parameters that govern the alert-budget and refractory decision engine.

    These thresholds exist to **prevent alarm fatigue** — a well-documented
    clinical problem where excessive false positives cause users to ignore or
    disable safety alerts.  The three independent mechanisms are:

    1. **Temporal persistence**: a vigilance drop must be sustained for at
       least ``persistence_n_seconds`` consecutive seconds before qualifying.
    2. **Refractory cooling period**: after an alert fires, no new alert may
       fire for ``refractory_minutes`` minutes, giving the user time to act.
    3. **Hourly rolling budget**: at most ``max_alerts_per_hour`` alerts may
       be dispatched within any 60-minute sliding window.
    """
    persistence_n_seconds: int = Field(
        default=30,
        description=(
            "Required duration of sustained vigilance drop (in seconds) before "
            "the engine qualifies the drop as genuine.  A higher value reduces "
            "false positives but increases detection latency."
        ),
    )
    probability_threshold: float = Field(
        default=0.70,
        description=(
            "Minimum P(vigilance drop) to count a window as 'in drop state'. "
            "Windows below this are considered normal and reset persistence."
        ),
    )
    refractory_minutes: int = Field(
        default=20,
        description=(
            "Minimum cooling period between alerts (minutes).  Prevents "
            "back-to-back alerts that would overwhelm the user."
        ),
    )
    max_alerts_per_hour: int = Field(
        default=2,
        description=(
            "Strict upper limit of alerts allowed within any rolling 60-minute "
            "window.  Acts as a hard budget cap even if persistence and "
            "refractory conditions are met."
        ),
    )


class SimulatorConfig(BaseModel):
    """Settings for the EEG stream simulator / replayer.

    The simulator supports two modes:
    - **File replay**: reads an Excel or CSV export of a real EEG session.
    - **Synthetic generation**: produces continuous pseudo-random windows
      with realistic vigilance dynamics when no input file is provided.
    """
    stream_interval_seconds: float = Field(
        default=1.0,
        description=(
            "Stride interval between published EEG windows (seconds).  Set to "
            "1.0 to emulate the real-time 1-second stride of TGAM windowing."
        ),
    )
    user_id: str = Field(
        default="user-01",
        description="Subject / Patient identifier embedded in FHIR references",
    )
    device_id: str = Field(
        default="tgam-headband-01",
        description="EEG headband device identifier embedded in FHIR Device references",
    )
    input_file: Optional[str] = Field(
        default=None,
        description=(
            "Optional path to an Excel (.xlsx) or CSV export file.  When "
            "provided, the simulator replays the recorded session row-by-row "
            "instead of generating synthetic data."
        ),
    )


class QualityConfig(BaseModel):
    """Signal quality gate thresholds.

    These thresholds determine whether a 2-second EEG window is physically
    trustworthy enough to be used for vigilance inference.  Windows that fail
    are NOT dropped — they are preserved in FHIR with status ``indeterminate``
    and a ``dataAbsentReason`` of ``unreliable``.
    """
    contact_quality_max_threshold: float = Field(
        default=50.0,
        description=(
            "Maximum acceptable electrode contact quality score.  The TGAM "
            "sensor reports 0 for perfect contact; values above this threshold "
            "indicate the headband is poorly seated and the signal is unreliable."
        ),
    )
    imu_motion_max_threshold: float = Field(
        default=1.5,
        description=(
            "Maximum acceptable IMU movement index.  High values indicate "
            "excessive head/body motion that introduces movement artifacts "
            "into the EEG signal."
        ),
    )


class AppConfig(BaseModel):
    """Root configuration object aggregating all subsystem configurations.

    Serves as the single entry point for the entire application's tuneable
    parameters.  Every downstream component receives its relevant sub-config
    via dependency injection rather than accessing global state.
    """
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)


def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    """Loads, validates, and returns the application configuration.

    This function implements a two-stage configuration strategy:

    **Stage 1 — YAML file loading**:
        Reads the YAML file at ``config_path`` and deserialises it into an
        ``AppConfig`` Pydantic model.  If the file does not exist (e.g. first
        run or CI environment), safe built-in defaults are used instead.

    **Stage 2 — Environment variable override**:
        Checks for the ``MQTT_BROKER_HOST`` environment variable and, if set,
        patches ``config.mqtt.broker_host``.  This is essential for Docker
        Compose deployments where the Mosquitto broker is reachable via its
        service name (``mosquitto``) rather than ``localhost``.

    Args:
        config_path: Filesystem path to the YAML configuration file.

    Returns:
        A fully validated ``AppConfig`` instance ready for injection into
        downstream components.
    """
    path = Path(config_path)
    if not path.exists():
        # No config file found — fall back to Pydantic defaults.
        # This keeps the system functional out-of-the-box for quick demos.
        config = AppConfig()
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = AppConfig(**data)

    # Environment override: Docker Compose sets MQTT_BROKER_HOST=mosquitto
    # so the gateway container can reach the broker by service name.
    env_broker_host = os.getenv("MQTT_BROKER_HOST")
    if env_broker_host:
        config.mqtt.broker_host = env_broker_host

    return config
