"""Acquisition Layer: Real-Time EEG Stream Emulator."""

import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional
import pandas as pd


class StreamSimulator:
    """Emulates a single-channel consumer EEG stream (e.g. NeuroSky TGAM).

    Can load pre-recorded session data from an Excel (.xlsx) or CSV file,
    or generate continuous synthetic EEG window telemetry.
    """

    def __init__(
        self,
        input_file: Optional[str] = None,
        stream_interval_seconds: float = 1.0,
        user_id: str = "user-01",
        device_id: str = "tgam-headband-01",
    ):
        """Initializes the stream simulator.

        Args:
            input_file: Path to Excel or CSV file containing raw session data.
            stream_interval_seconds: Delay between simulated windows (seconds).
            user_id: Patient/User identifier.
            device_id: Headband device identifier.
        """
        self.input_file = input_file
        self.stream_interval_seconds = stream_interval_seconds
        self.user_id = user_id
        self.device_id = device_id
        self.df: Optional[pd.DataFrame] = None
        self._load_file_if_available()

    def _load_file_if_available(self) -> None:
        """Loads input Excel or CSV data into pandas DataFrame if path exists."""
        if not self.input_file:
            return

        file_path = Path(self.input_file)
        if not file_path.exists():
            return

        try:
            if file_path.suffix.lower() in [".xlsx", ".xls"]:
                self.df = pd.read_excel(file_path)
            elif file_path.suffix.lower() == ".csv":
                self.df = pd.read_csv(file_path)
        except Exception:
            self.df = None

    def generate_synthetic_window(self, step_idx: int) -> Dict[str, Any]:
        """Generates realistic synthetic EEG window telemetry.

        Simulates fluctuations in vigilance, occasional artifacts (poor contact or movement),
        and continuous alpha/theta spectral band powers.

        Args:
            step_idx: Iteration index used for periodic trend simulation.

        Returns:
            Dictionary containing window parameters.
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        # Inject realistic vigilance dynamics (sine wave with noise + occasional drops)
        base_vigilance = 0.4 + 0.35 * math.sin(step_idx / 20.0)
        noise = random.uniform(-0.1, 0.1)
        p_drop = max(0.0, min(1.0, base_vigilance + noise))

        # Sustained drop simulation around steps 40-75 for testing alert budget
        if 40 <= step_idx <= 75:
            p_drop = max(p_drop, random.uniform(0.75, 0.95))

        # Signal quality artifacts: 10% probability of poor contact or high motion
        contact_quality = 0.0 if random.random() > 0.12 else random.uniform(60.0, 200.0)
        imu_motion = random.uniform(0.05, 0.6) if random.random() > 0.08 else random.uniform(1.8, 4.5)

        # Spectral features
        raw_alpha = max(1.0, 15.0 - (p_drop * 8.0) + random.uniform(-1.5, 1.5))
        raw_theta = max(1.0, 8.0 + (p_drop * 14.0) + random.uniform(-2.0, 2.0))

        return {
            "timestamp": now_utc,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "window_id": step_idx,
            "raw_alpha": round(raw_alpha, 3),
            "raw_theta": round(raw_theta, 3),
            "alpha_theta_ratio": round(raw_alpha / max(raw_theta, 0.001), 3),
            "contact_quality": round(contact_quality, 1),
            "imu_motion": round(imu_motion, 3),
            "p_vigilance_drop": round(p_drop, 4),
            "blink_count": random.choice([0, 0, 0, 1, 2]),
            "emg_power": round(random.uniform(5.0, 35.0), 2),
        }

    def stream_windows(self, max_windows: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
        """Generator streaming window objects at simulated real-time interval.

        Args:
            max_windows: Optional maximum number of windows to generate/replay.

        Yields:
            EEG window data dictionaries.
        """
        step = 0
        if self.df is not None and not self.df.empty:
            for _, row in self.df.iterrows():
                if max_windows is not None and step >= max_windows:
                    break
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "user_id": self.user_id,
                    "device_id": self.device_id,
                    "window_id": step,
                    "raw_alpha": float(row.get("raw_alpha", 10.0)),
                    "raw_theta": float(row.get("raw_theta", 10.0)),
                    "alpha_theta_ratio": float(row.get("alpha_theta_ratio", 1.0)),
                    "contact_quality": float(row.get("contact_quality", 0.0)),
                    "imu_motion": float(row.get("imu_motion", 0.1)),
                    "p_vigilance_drop": float(row.get("p_vigilance_drop", 0.3)),
                    "blink_count": int(row.get("blink_count", 0)),
                    "emg_power": float(row.get("emg_power", 15.0)),
                }
                step += 1
                yield record
                time.sleep(self.stream_interval_seconds)
        else:
            while True:
                if max_windows is not None and step >= max_windows:
                    break
                window = self.generate_synthetic_window(step)
                step += 1
                yield window
                time.sleep(self.stream_interval_seconds)
