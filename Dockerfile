# ==============================================================================
# Dockerfile — Edge-First EEG Vigilance Architecture
# ==============================================================================
# Containerises both the Edge Gateway pipeline and the Streamlit Dashboard.
# Uses python:3.11-slim as a minimal, security-hardened base image.
# ==============================================================================

FROM python:3.11-slim

WORKDIR /app

# Install minimal OS dependencies required for compiling Python C-extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Layer cache optimisation: copy requirements first to prevent reinstalling
# packages when only application source code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code, configuration files, and assets
COPY . .

# Set PYTHONPATH so absolute imports from 'src' resolve correctly within container
ENV PYTHONPATH=/app

# Default command runs the edge gateway pipeline
CMD ["python", "src/gateway_main.py"]
