"""Acquisition Layer — Real-Time EEG Stream Emulator with Adaptive Dataset Replay.

WHY THIS MODULE EXISTS
----------------------
Developing, testing, and demonstrating an EEG vigilance monitoring system
requires continuous physiological telemetry. Real single-channel EEG headbands
(e.g., NeuroSky TGAM) are hardware-dependent, sensitive to ambient electrical
noise, and impractical for automated integration testing or unattended live demos.

This module provides two robust acquisition modes:

    1. **Adaptive File Replay**: Loads pre-recorded EEG session files (.xlsx,
       .xls, or .csv) and replays rows at a configurable cadence (default 1 Hz).
       Crucially, it includes adaptive column mapping supporting exports from
       commercial consumer apps (such as NeuroSky MindWave / ThinkGear with
       columns like 'Poor Signal', 'Attention', 'HighAlpha', 'Theta') as well
       as academic datasets. It also supports continuous loop replay for
       unattended demonstrations.

    2. **Synthetic Waveform & Artifact Generator**: When no input file is
       supplied, it synthesises realistic EEG vigilance dynamics:
           - Sinusoidal baseline vigilance with low-frequency fluctuations.
           - A sustained vigilance-drop zone (epochs 40–75) specifically designed
             to exercise the downstream persistence and alert engines.
           - Intermittent electrode contact impedance and motion artifacts.
           - Neurophysiologically correlated spectral features (alpha attenuation
             and theta elevation during drowsiness).

Both modes yield standardised window dictionaries conforming to the
``AcquisitionSource`` interface.
"""

import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
import pandas as pd

from src.acquisition_base import AcquisitionSource


class StreamSimulator(AcquisitionSource):
    """Emulates a single-channel consumer EEG stream (e.g., NeuroSky TGAM).

    Supports replaying pre-recorded session data from Excel/CSV files with
    intelligent column aliasing and cyclic looping, or producing continuous
    synthetic telemetry for automated demos and testing.
    """

    def __init__(
        self,
        input_file: Optional[str] = None,
        stream_interval_seconds: float = 1.0,
        user_id: str = "user-01",
        device_id: str = "tgam-headband-01",
        loop_playback: bool = True,
    ):
        """Initialises the stream simulator with acquisition parameters.

        Args:
            input_file: Optional path to an Excel or CSV file containing exported
                        EEG session data.
            stream_interval_seconds: Delay in seconds between consecutive window
                                     yields (emulating 1 Hz epoch progression).
            user_id: Monitored subject identifier.
            device_id: Monitored EEG headband identifier.
            loop_playback: If True, loops the input file continuously upon reaching
                           EOF (essential for continuous live demonstrations).
        """
        self.input_file = input_file
        self.stream_interval_seconds = stream_interval_seconds
        self.user_id = user_id
        self.device_id = device_id
        self.loop_playback = loop_playback
        self.df: Optional[pd.DataFrame] = None
        self._load_file_if_available()

    def _load_file_if_available(self) -> None:
        """Loads and normalises an input file if present on disk.

        Normalises column names by stripping whitespace, converting to lowercase,
        and replacing spaces with underscores to enable adaptive alias matching.
        """
        if not self.input_file:
            return

        file_path = Path(self.input_file)
        if not file_path.exists():
            return

        try:
            if file_path.suffix.lower() in [".xlsx", ".xls"]:
                df_raw = pd.read_excel(file_path)
            elif file_path.suffix.lower() == ".csv":
                df_raw = pd.read_csv(file_path)
            else:
                return

            # Clean and normalise column headers
            col_map = {c: str(c).strip().lower().replace(" ", "_").replace("-", "_") for c in df_raw.columns}
            self.df = df_raw.rename(columns=col_map)
        except Exception:
            # Fallback gracefully to synthetic generation if file read fails
            self.df = None

    def _extract_column_value(self, row: pd.Series, aliases: List[str], default: float) -> float:
        """Extracts a metric value from a row using candidate column aliases."""
        for alias in aliases:
            if alias in row.index and pd.notnull(row[alias]):
                try:
                    return float(row[alias])
                except (ValueError, TypeError):
                    continue
        return default

    def generate_synthetic_window(self, step_idx: int) -> Dict[str, Any]:
        """Generates a single synthetic EEG window with realistic vigilance dynamics.

        Args:
            step_idx: Monotonically increasing epoch sequence index.

        Returns:
            Dictionary matching the standard window schema.
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        # Baseline vigilance: smooth sinusoidal drift with stochastic noise
        base_vigilance = 0.4 + 0.35 * math.sin(step_idx / 20.0)
        noise = random.uniform(-0.1, 0.1)
        p_drop = max(0.0, min(1.0, base_vigilance + noise))

        # Sustained drop zone (windows 40–75): guarantees persistence triggers in demos
        if 40 <= step_idx <= 75:
            p_drop = max(p_drop, random.uniform(0.78, 0.95))

        # Stochastic artifacts
        # ~12% probability of poor scalp contact (sensor displacement or sweat)
        contact_quality = 0.0 if random.random() > 0.12 else random.uniform(60.0, 160.0)
        # ~8% probability of head/body motion artifact
        imu_motion = random.uniform(0.05, 0.5) if random.random() > 0.08 else random.uniform(1.8, 4.0)

        # Correlated spectral power:
        # Alpha band power attenuates with drowsiness; theta band power rises.
        raw_alpha = max(1.0, 15.0 - (p_drop * 8.0) + random.uniform(-1.0, 1.0))
        raw_theta = max(1.0, 8.0 + (p_drop * 14.0) + random.uniform(-1.5, 1.5))

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
            "emg_power": round(random.uniform(5.0, 25.0), 2),
        }

    def stream_windows(self, max_windows: Optional[int] = None) -> Generator[Dict[str, Any], None, None]:
        """Generator yielding EEG window telemetry at the configured stream interval.

        In file replay mode, yields rows matching known single-channel EEG app
        exports. If loop_playback is enabled, seamlessly loops back to the start
        upon reaching EOF.

        Args:
            max_windows: Optional cap on the number of yielded windows.

        Yields:
            Standardised EEG window dictionaries.
        """
        step = 0

        while True:
            if self.df is not None and not self.df.empty:
                # --- Adaptive File Replay Mode ---
                for _, row in self.df.iterrows():
                    if max_windows is not None and step >= max_windows:
                        return

                    # Resolve metrics using adaptive aliases
                    contact = self._extract_column_value(
                        row, ["contact_quality", "poor_signal", "poorsignal", "quality", "contact"], 0.0
                    )
                    motion = self._extract_column_value(
                        row, ["imu_motion", "motion", "accel", "movement", "gyro"], 0.1
                    )
                    alpha = self._extract_column_value(
                        row, ["raw_alpha", "alpha", "highalpha", "lowalpha", "alpha_power"], 10.0
                    )
                    theta = self._extract_column_value(
                        row, ["raw_theta", "theta", "theta_power"], 10.0
                    )
                    p_drop = self._extract_column_value(
                        row, ["p_vigilance_drop", "vigilance_drop", "p_drop", "drowsy", "drowsiness"], 0.3
                    )

                    # Adapt commercial TGAM 'attention' index (0-100) if p_vigilance_drop is not directly present
                    if "attention" in row.index and "p_vigilance_drop" not in row.index:
                        attention_val = self._extract_column_value(row, ["attention"], 50.0)
                        p_drop = max(0.0, min(1.0, (100.0 - attention_val) / 100.0))

                    record = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "user_id": self.user_id,
                        "device_id": self.device_id,
                        "window_id": step,
                        "raw_alpha": round(alpha, 3),
                        "raw_theta": round(theta, 3),
                        "alpha_theta_ratio": round(alpha / max(theta, 0.001), 3),
                        "contact_quality": round(contact, 1),
                        "imu_motion": round(motion, 3),
                        "p_vigilance_drop": round(p_drop, 4),
                        "blink_count": int(self._extract_column_value(row, ["blink_count", "blinks", "blink"], 0)),
                        "emg_power": round(self._extract_column_value(row, ["emg_power", "emg", "highbeta", "high_beta"], 15.0), 2),
                    }
                    step += 1
                    yield record
                    time.sleep(self.stream_interval_seconds)

                if not self.loop_playback:
                    break
            else:
                # --- Synthetic Generation Mode ---
                if max_windows is not None and step >= max_windows:
                    break
                window = self.generate_synthetic_window(step)
                step += 1
                yield window
                time.sleep(self.stream_interval_seconds)
