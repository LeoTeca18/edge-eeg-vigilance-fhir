"""Abstract base class defining the acquisition source contract.

WHY THIS MODULE EXISTS
----------------------
The edge gateway pipeline must support multiple acquisition modes:

    1. **File replay**: Pre-recorded EEG session data (Excel / CSV).
    2. **Synthetic generation**: Continuous pseudo-random telemetry for demos.
    3. **Live SDK / BLE**: Real-time ingestion from a NeuroSky TGAM headband
       via Bluetooth Low Energy (BLE) or the ThinkGear SDK.

Without a formal interface, adding a new acquisition source would require
modifying the gateway orchestrator (``gateway_main.py``) and risk breaking
existing modes.  This module defines an abstract base class that all
acquisition sources must implement, enabling the gateway to accept any
source through **dependency injection** without code changes.

HOW IT WORKS
------------
``AcquisitionSource`` defines a single abstract method:
``stream_windows(max_windows)`` → Generator[Dict, None, None].

Concrete implementations (``StreamSimulator``, ``TgamBleStub``) inherit
from this base class and provide their own window generation logic.  The
gateway instantiates the appropriate source based on configuration and
passes it to the processing loop.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Optional


class AcquisitionSource(ABC):
    """Abstract base for all EEG window acquisition sources.

    Any class that implements this interface can be plugged into the
    gateway pipeline's ``for window in source.stream_windows()`` loop.
    """

    @abstractmethod
    def stream_windows(
        self, max_windows: Optional[int] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Yields EEG window dictionaries at the configured cadence.

        Each yielded dictionary must contain at minimum:
            - ``timestamp``:         ISO 8601 UTC string.
            - ``user_id``:           Subject identifier.
            - ``device_id``:         EEG device identifier.
            - ``window_id``:         Sequential window index.
            - ``raw_alpha``:         Alpha band power (µV²).
            - ``raw_theta``:         Theta band power (µV²).
            - ``alpha_theta_ratio``: α/θ spectral ratio.
            - ``contact_quality``:   Electrode contact index.
            - ``imu_motion``:        IMU movement index.
            - ``p_vigilance_drop``:  Estimated drop probability [0.0, 1.0].
            - ``blink_count``:       Detected eye blinks in window.
            - ``emg_power``:         Electromyography noise power.

        Args:
            max_windows: Optional upper bound on the number of windows to
                         produce.  ``None`` means infinite streaming.

        Yields:
            EEG window dictionaries following the standard schema.
        """
        ...


class TgamBleStub(AcquisitionSource):
    """Placeholder for live TGAM headband BLE/SDK ingestion.

    This stub documents the planned integration point for real NeuroSky
    TGAM hardware.  It raises ``NotImplementedError`` when called,
    serving as a clear marker for future implementation.

    When hardware becomes available, replace the body of
    ``stream_windows()`` with actual ThinkGear SDK / BLE GATT calls
    to read raw EEG data, compute spectral features, and yield
    standard window dictionaries.
    """

    def __init__(self, user_id: str = "user-01", device_id: str = "tgam-headband-01"):
        """Initialises the BLE stub with subject and device identifiers.

        Args:
            user_id: Patient / subject identifier.
            device_id: EEG headband device identifier.
        """
        self.user_id = user_id
        self.device_id = device_id

    def stream_windows(
        self, max_windows: Optional[int] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """Raises NotImplementedError — live BLE ingestion is not yet available.

        This method exists to satisfy the abstract interface and to provide
        a clear error message when someone attempts to use live hardware
        mode before it has been implemented.
        """
        raise NotImplementedError(
            "Live TGAM BLE ingestion is not yet implemented. "
            "Use StreamSimulator for file replay or synthetic generation."
        )
