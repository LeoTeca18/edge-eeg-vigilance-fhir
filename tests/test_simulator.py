"""Unit tests for the enhanced StreamSimulator (adaptive replay and looping)."""

import tempfile
from pathlib import Path
import pandas as pd
import pytest
from src.simulator import StreamSimulator


def test_synthetic_window_generation():
    """Verifies synthetic window generation schema and characteristics."""
    simulator = StreamSimulator(user_id="test-user", device_id="test-device")
    window = simulator.generate_synthetic_window(step_idx=0)

    assert window["user_id"] == "test-user"
    assert window["device_id"] == "test-device"
    assert "p_vigilance_drop" in window
    assert "raw_alpha" in window
    assert "raw_theta" in window
    assert "contact_quality" in window
    assert "imu_motion" in window


def test_adaptive_column_aliasing_csv():
    """Verifies that commercial app export headers (e.g. 'Poor Signal', 'Attention')
    are properly mapped to pipeline metrics.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "app_export.csv"
        # Simulate an export from NeuroSky MindWave Mobile
        df_export = pd.DataFrame([
            {"Poor Signal": 25.0, "Attention": 30.0, "HighAlpha": 12.5, "Theta": 14.0, "Motion": 0.2},
            {"Poor Signal": 80.0, "Attention": 20.0, "HighAlpha": 8.0, "Theta": 18.0, "Motion": 1.9},
        ])
        df_export.to_csv(csv_path, index=False)

        sim = StreamSimulator(input_file=str(csv_path), stream_interval_seconds=0.01, loop_playback=False)
        windows = list(sim.stream_windows(max_windows=2))

        assert len(windows) == 2
        # First window: Attention 30 -> p_drop = (100 - 30) / 100 = 0.70
        assert windows[0]["contact_quality"] == 25.0
        assert windows[0]["p_vigilance_drop"] == 0.70
        assert windows[0]["raw_alpha"] == 12.5

        # Second window: Poor Signal 80.0
        assert windows[1]["contact_quality"] == 80.0
        assert windows[1]["imu_motion"] == 1.9


def test_continuous_loop_replay():
    """Verifies that loop_playback=True cycles through the dataset without stopping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "short_session.csv"
        df_export = pd.DataFrame([
            {"raw_alpha": 10.0, "raw_theta": 8.0, "p_vigilance_drop": 0.2},
            {"raw_alpha": 9.0, "raw_theta": 9.0, "p_vigilance_drop": 0.4},
        ])
        df_export.to_csv(csv_path, index=False)

        sim = StreamSimulator(input_file=str(csv_path), stream_interval_seconds=0.001, loop_playback=True)
        # Request 5 windows from a 2-row file
        windows = list(sim.stream_windows(max_windows=5))

        assert len(windows) == 5
        assert windows[0]["window_id"] == 0
        assert windows[2]["window_id"] == 2  # looped
        assert windows[4]["window_id"] == 4  # looped again
