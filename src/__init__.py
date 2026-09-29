"""Edge-First EEG Vigilance Architecture — Top-level package initialiser.

This package implements a complete edge-computing pipeline for real-time
vigilance-drop detection using single-channel consumer EEG headbands.
The architecture is organised into four layers:

    1. Acquisition Layer   — StreamSimulator (simulator.py)
    2. Edge Gateway Layer  — QualityGate + AlertDecisionEngine
    3. Interoperability    — FHIR R4 builder + MQTT transport
    4. Presentation Layer  — Streamlit real-time dashboard

Importing this package makes the version string available at ``src.__version__``.
"""

__version__ = "1.0.0"
