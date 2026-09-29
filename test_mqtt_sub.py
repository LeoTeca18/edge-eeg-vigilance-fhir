"""Quick MQTT subscriber diagnostic tool.

WHY THIS SCRIPT EXISTS
----------------------
During development and Docker Compose debugging, it is useful to have a
minimal, standalone MQTT subscriber that connects to the broker, subscribes
to the EEG observation topic, prints a preview of each incoming message,
and exits after a fixed timeout.

This script is NOT part of the production pipeline — it is a developer
utility for verifying that:
    1. The Mosquitto broker is reachable.
    2. The edge gateway is publishing FHIR R4 JSON payloads.
    3. The MQTT topic pattern is correct.

HOW TO USE
----------
    python test_mqtt_sub.py

The script connects to the MQTT broker at "mosquitto:1883" (Docker Compose
service name), subscribes to "study/+/eeg/observation", prints the first
120 characters of each received message, and disconnects after 5 seconds.

To test outside Docker, change the broker host to "localhost".
"""

import paho.mqtt.client as mqtt
import time


def on_connect(c, u, f, rc, p=None):
    """Callback executed when the MQTT connection is established.
    Subscribes to the wildcard EEG observation topic immediately.
    """
    rc_val = getattr(rc, "value", rc)
    print(f"Connected rc={rc_val}")
    c.subscribe("study/+/eeg/observation")


def on_message(c, u, m):
    """Callback for each received MQTT message.
    Prints the topic and a truncated preview of the payload.
    """
    payload_preview = m.payload[:120].decode("utf-8", errors="replace")
    print(f"MSG on {m.topic}: {payload_preview}")


# Automatic Paho API version detection (v2 vs v1 compatibility).
try:
    cl = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="test-sub")
except (AttributeError, ValueError):
    cl = mqtt.Client(client_id="test-sub")

cl.on_connect = on_connect
cl.on_message = on_message

# Connect to the Mosquitto broker.
# Change "mosquitto" to "localhost" if running outside Docker.
cl.connect("mosquitto", 1883, 60)
cl.loop_start()

# Listen for 5 seconds, then disconnect.
time.sleep(5)
cl.loop_stop()
cl.disconnect()
print("Done")
