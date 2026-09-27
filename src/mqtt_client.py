"""Storage & Interoperability Layer: Paho-MQTT Publisher Wrapper."""

import logging
import time
from typing import Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Wrapper around Paho-MQTT client for publishing FHIR R4 JSON payloads."""

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        client_id: str = "edge-gateway-01",
        keepalive: int = 60,
    ):
        """Initializes MQTT publisher client.

        Args:
            broker_host: MQTT broker hostname or IP address.
            broker_port: MQTT broker port.
            client_id: MQTT client identifier.
            keepalive: Keepalive interval in seconds.
        """
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id
        self.keepalive = keepalive
        self.is_connected = False

        # Initialize Paho MQTT client (compatibility with v1 & v2 API)
        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        except (AttributeError, ValueError):
            self.client = mqtt.Client(client_id=self.client_id)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback executed upon connecting to broker."""
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            self.is_connected = True
            logger.info(f"Successfully connected to MQTT Broker at {self.broker_host}:{self.broker_port}")
        else:
            self.is_connected = False
            logger.warning(f"MQTT connection attempt returned code {rc_val}")

    def _on_disconnect(self, client, userdata, disconnect_flags, rc=None, properties=None):
        """Callback executed upon disconnection from broker."""
        self.is_connected = False
        logger.warning(f"Disconnected from MQTT Broker (code {rc})")

    def connect(self, retry_count: int = 2, retry_delay: float = 0.5) -> bool:
        """Connects to the MQTT broker with retry logic.

        Args:
            retry_count: Number of connection attempts before failing gracefully.
            retry_delay: Delay between attempts (seconds).

        Returns:
            True if connected, False otherwise.
        """
        for attempt in range(1, retry_count + 1):
            try:
                self.client.connect(self.broker_host, self.broker_port, self.keepalive)
                self.client.loop_start()
                time.sleep(0.5)  # Allow async loop to settle
                if self.is_connected:
                    return True
            except Exception as e:
                logger.warning(f"MQTT connect attempt {attempt}/{retry_count} failed: {e}")
                time.sleep(retry_delay)

        logger.error(f"Could not establish MQTT connection to {self.broker_host}:{self.broker_port}")
        return False

    def publish(self, topic: str, payload_json: str, qos: int = 1) -> bool:
        """Publishes JSON string payload to specified topic.

        Args:
            topic: MQTT destination topic string.
            payload_json: Serialized JSON payload string.
            qos: Quality of Service level (0, 1, or 2).

        Returns:
            True if published successfully, False if offline or error.
        """
        if not self.is_connected:
            logger.debug(f"Attempting publish while disconnected. Payload to topic '{topic}' dropped.")
            return False

        try:
            info = self.client.publish(topic, payload_json, qos=qos)
            info.wait_for_publish(timeout=2.0)
            return info.is_published()
        except Exception as e:
            logger.error(f"Failed to publish MQTT message to topic '{topic}': {e}")
            return False

    def disconnect(self) -> None:
        """Gracefully disconnects client and stops loop background thread."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False
            logger.info("MQTT Client disconnected cleanly.")
        except Exception as e:
            logger.warning(f"Error during MQTT client disconnect: {e}")
