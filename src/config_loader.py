"""Configuration loader module using Pydantic and PyYAML."""

from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class MqttConfig(BaseModel):
    """MQTT connection configuration."""
    broker_host: str = Field(default="localhost", description="MQTT Broker Hostname")
    broker_port: int = Field(default=1883, description="MQTT Broker Port")
    topic_template: str = Field(
        default="study/{user_id}/eeg/observation",
        description="Topic pattern for observation publishing"
    )
    client_id: str = Field(default="edge-gateway-01", description="MQTT Client Identifier")
    keepalive: int = Field(default=60, description="Keepalive interval in seconds")


class DecisionConfig(BaseModel):
    """Decision layer parameters for alert budget & refractory rules."""
    persistence_n_seconds: int = Field(
        default=30,
        description="Required duration of sustained vigilance drop in seconds"
    )
    probability_threshold: float = Field(
        default=0.70,
        description="Probability threshold p(vigilance drop) to qualify drop state"
    )
    refractory_minutes: int = Field(
        default=20,
        description="Minimum refractory period between alerts in minutes"
    )
    max_alerts_per_hour: int = Field(
        default=2,
        description="Strict upper limit of alerts allowed within a rolling 60-minute window"
    )


class SimulatorConfig(BaseModel):
    """Simulator settings for real-time window emulation."""
    stream_interval_seconds: float = Field(
        default=1.0,
        description="Stride interval between published EEG windows (seconds)"
    )
    user_id: str = Field(default="user-01", description="Subject / Patient Identifier")
    device_id: str = Field(default="tgam-headband-01", description="EEG Device Identifier")
    input_file: Optional[str] = Field(
        default=None,
        description="Optional path to Excel (.xlsx) or CSV export file"
    )


class QualityConfig(BaseModel):
    """Signal quality gate thresholds."""
    contact_quality_max_threshold: float = Field(
        default=50.0,
        description="Maximum contact quality score allowed (0 = perfect contact)"
    )
    imu_motion_max_threshold: float = Field(
        default=1.5,
        description="Maximum IMU movement index allowed for usable signal"
    )


class AppConfig(BaseModel):
    """Root Application Configuration."""
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)


def load_config(config_path: str = "config/config.yaml") -> AppConfig:
    """Loads configuration from YAML file and validates using Pydantic.

    Args:
        config_path: Path to YAML configuration file.

    Returns:
        Validated AppConfig instance.
    """
    path = Path(config_path)
    if not path.exists():
        # Return default configuration if file is missing
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return AppConfig(**data)
