"""Acquisition Layer — Real-Time EEG Stream Emulator.

WHY THIS MODULE EXISTS
----------------------
Developing and testing an EEG vigilance monitoring pipeline requires a
continuous data stream — but real EEG hardware is expensive, requires IRB
approval for human subjects, and is impractical for automated CI/CD testing.

This module provides two acquisition modes:

    1. **File replay**: Loads a pre-recorded EEG session from an Excel
       (``.xlsx``) or CSV file and replays it row-by-row with configurable
       inter-window delay.  This is used for reproducible experiments with
       real clinical data.

    2. **Synthetic generation**: When no input file is provided, the simulator
       produces continuous pseudo-random EEG window telemetry with realistic
       characteristics:
           - Sinusoidal vigilance baseline with additive noise.
           - A sustained drop region (windows 40–75) for testing alert logic.
           - 10–12% probability of electrode contact or motion artifacts.
           - Correlated spectral features (α/θ ratio inversely proportional
             to vigilance drop probability).

HOW IT WORKS
------------
``StreamSimulator.stream_windows()`` is a Python **generator** — it yields
one window dictionary per iteration with a ``time.sleep()`` between yields
to emulate real-time 1 Hz streaming.  This design integrates seamlessly with
the gateway's ``for window in simulator.stream_windows()`` loop.

WINDOW DICTIONARY SCHEMA
-------------------------
Each yielded window dictionary contains:
    - ``timestamp``:         ISO 8601 UTC string
    - ``user_id``:           Subject identifier
    - ``device_id``:         EEG device identifier
    - ``window_id``:         Sequential window index
    - ``raw_alpha``:         Alpha band power (µV²)
    - ``raw_theta``:         Theta band power (µV²)
    - ``alpha_theta_ratio``: α/θ spectral ratio
    - ``contact_quality``:   Electrode contact index (0 = perfect)
    - ``imu_motion``:        IMU movement index
    - ``p_vigilance_drop``:  Estimated drop probability [0.0, 1.0]
    - ``blink_count``:       Detected eye blinks in window
    - ``emg_power``:         Electromyography noise power
"""

import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional
import pandas as pd


class StreamSimulator:
    """Emulates a single-channel consumer EEG stream (e.g. NeuroSky TGAM).

    Supports loading pre-recorded session data from Excel/CSV files for
    reproducible experiments, or generating continuous synthetic telemetry
    for development, demos, and automated testing.
    """

    def __init__(
        self,
        input_file: Optional[str] = None,
        stream_interval_seconds: float = 1.0,
        user_id: str = "user-01",
        device_id: str = "tgam-headband-01",
    ):
        """Initialises the stream simulator with acquisition parameters.

        Args:
            input_file: Optional path to an Excel or CSV file containing
                        exported EEG session data.  If provided and the file
                        exists, data is replayed from the file.  Otherwise,
                        synthetic windows are generated.
            stream_interval_seconds: Delay between consecutive window yields
                                     (seconds).  Set to 1.0 to emulate the
                                     TGAM's real-time 1-second stride.
            user_id: Patient/subject identifier embedded in each window and
                     used in FHIR Patient references.
            device_id: EEG headband identifier embedded in each window and
                       used in FHIR Device references.
        """
        self.input_file = input_file
        self.stream_interval_seconds = stream_interval_seconds
        self.user_id = user_id
        self.device_id = device_id
        self.df: Optional[pd.DataFrame] = None
        self._load_file_if_available()

    def _load_file_if_available(self) -> None:
        """Attempts to load the input file into a pandas DataFrame.

        This is called once during initialisation.  If the file path is
        ``None``, doesn't exist, or cannot be parsed, the simulator falls
        back to synthetic generation mode (``self.df`` remains ``None``).

        Supports:
            - ``.xlsx`` / ``.xls`` — read via ``pd.read_excel()``.
            - ``.csv`` — read via ``pd.read_csv()``.
        """
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
            # If the file is corrupted or unreadable, degrade gracefully
            # to synthetic mode rather than crashing the pipeline.
            self.df = None

    def generate_synthetic_window(self, step_idx: int) -> Dict[str, Any]:
        """Generates a single synthetic EEG window with realistic dynamics.

        The synthetic data is designed to exercise the full pipeline:
            - **Vigilance baseline**: A sine wave (period ≈ 126 steps)
              modulated by uniform noise, producing smooth fluctuations
              between alert and drowsy states.
            - **Sustained drop zone**: Between steps 40–75, the drop
              probability is clamped to ≥ 0.75–0.95, ensuring the decision
              engine's persistence and alert logic are exercised.
            - **Artifact injection**: ~12% of windows have poor contact
              quality, and ~8% have excessive motion, simulating real-world
              sensor displacement and user movement.
            - **Correlated spectral features**: Alpha power decreases and
              theta power increases as drop probability rises, matching the
              known neurophysiological relationship.

        Args:
            step_idx: Sequential iteration index used for periodic trend
                      calculation and drop zone boundaries.

        Returns:
            A window dictionary following the standard schema.
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        # --- Vigilance dynamics ---
        # Base vigilance follows a slow sine wave centred around 0.4 with
        # amplitude 0.35, creating natural-looking alert/drowsy transitions.
        base_vigilance = 0.4 + 0.35 * math.sin(step_idx / 20.0)
        noise = random.uniform(-0.1, 0.1)
        p_drop = max(0.0, min(1.0, base_vigilance + noise))

        # Sustained drop region (steps 40–75): ensures the persistence
        # counter reaches 30+ seconds, triggering the decision engine's
        # alert logic.  This is critical for end-to-end demo validation.
        if 40 <= step_idx <= 75:
            p_drop = max(p_drop, random.uniform(0.75, 0.95))

        # --- Signal quality artifacts ---
        # ~12% chance of poor electrode contact (sensor lifted / sweat)
        contact_quality = 0.0 if random.random() > 0.12 else random.uniform(60.0, 200.0)
        # ~8% chance of excessive motion (head turning / walking)
        imu_motion = random.uniform(0.05, 0.6) if random.random() > 0.08 else random.uniform(1.8, 4.5)

        # --- Spectral features ---
        # Alpha power is inversely related to drop probability (drowsiness
        # suppresses the alpha rhythm).  Theta power is positively correlated
        # (theta increases during drowsiness).
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
        """Generator that yields EEG window dictionaries at simulated real-time intervals.

        If a DataFrame was loaded from an input file, windows are replayed
        from the recorded data.  Otherwise, synthetic windows are generated
        indefinitely (or until ``max_windows`` is reached).

        The ``time.sleep()`` between yields emulates the real-time 1-second
        stride of the TGAM headband, making the pipeline behave identically
        to a live hardware stream.

        Args:
            max_windows: Optional upper bound on the number of windows to
                         generate/replay.  ``None`` means infinite streaming
                         (useful for production; stop with Ctrl+C).

        Yields:
            EEG window dictionaries following the standard schema.
        """
        step = 0

        if self.df is not None and not self.df.empty:
            # --- File replay mode ---
            # Iterate over pre-recorded rows, mapping column names to the
            # standard window dictionary schema.  Missing columns fall back
            # to sensible defaults.
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
            # --- Synthetic generation mode ---
            # Produces windows indefinitely, suitable for demos and testing.
            while True:
                if max_windows is not None and step >= max_windows:
                    break
                window = self.generate_synthetic_window(step)
                step += 1
                yield window
                time.sleep(self.stream_interval_seconds)
