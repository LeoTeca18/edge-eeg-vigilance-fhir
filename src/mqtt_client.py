"""Storage & Interoperability Layer — Paho-MQTT Publisher Wrapper.

WHY THIS MODULE EXISTS
----------------------
The edge gateway pipeline needs a lightweight, reliable mechanism to
transport FHIR R4 Observation payloads from the edge device to downstream
consumers (dashboards, FHIR servers, data lakes).  MQTT was chosen because:

    1. **Minimal overhead**: MQTT is designed for constrained IoT devices and
       low-bandwidth networks.  Its binary protocol header is only 2 bytes,
       making it far more efficient than HTTP REST for high-frequency
       streaming (1 message/second).

    2. **Pub/sub decoupling**: The publisher (edge gateway) and subscribers
       (dashboard, FHIR server) are fully decoupled through the Mosquitto
       broker.  New consumers can be added without modifying the gateway.

    3. **Quality of Service**: MQTT QoS 1 ("at least once") guarantees that
       each observation is delivered to the broker, with automatic retries
       on network interruption.

    4. **Offline resilience**: If the broker is unreachable, the gateway
       continues processing and logging observations locally.  No data is
       lost — it is simply not transported until the connection is restored.

HOW IT WORKS
------------
``MqttPublisher`` wraps the Eclipse Paho MQTT client library with:
    - **Automatic API version detection**: Paho v2.x uses a different callback
      API than v1.x.  The constructor tries v2 first, falls back to v1.
    - **Retry logic**: ``connect()`` attempts multiple connections with delays.
    - **Graceful degradation**: ``publish()`` returns ``False`` when offline
      instead of raising exceptions, allowing the pipeline to continue.
"""

import logging
import time
from typing import Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MqttPublisher:
    """Thread-safe wrapper around Paho-MQTT client for publishing FHIR R4
    JSON payloads to an Eclipse Mosquitto broker.

    The wrapper abstracts away API version differences, connection management,
    and error handling, providing a simple ``connect() → publish() → disconnect()``
    interface to the rest of the pipeline.
    """

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        client_id: str = "edge-gateway-01",
        keepalive: int = 60,
    ):
        """Initialises the MQTT publisher with broker connection parameters.

        Args:
            broker_host: MQTT broker hostname or IP address.
            broker_port: MQTT broker port (default 1883 for unencrypted).
            client_id: MQTT client identifier — must be unique across all
                       clients connected to the same broker.
            keepalive: TCP keepalive interval in seconds.  The client sends
                       a PINGREQ packet if no data has been exchanged within
                       this period, keeping the connection alive through
                       firewalls and NAT.
        """
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id
        self.keepalive = keepalive
        self.is_connected = False

        # Automatic Paho API version detection:
        # Paho-MQTT v2.x introduced CallbackAPIVersion which changes the
        # signature of on_connect/on_disconnect callbacks.  We try v2 first
        # and fall back to v1 for backward compatibility.
        try:
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        except (AttributeError, ValueError):
            # Paho v1.x does not have CallbackAPIVersion.
            self.client = mqtt.Client(client_id=self.client_id)

        # Register connection lifecycle callbacks.
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback executed by Paho when the TCP connection to the broker
        is established and the CONNACK packet is received.

        Updates the ``is_connected`` flag so that ``publish()`` knows
        whether it is safe to send messages.
        """
        # Paho v2 wraps the return code in a ReasonCode object;
        # v1 passes a plain integer.
        rc_val = getattr(rc, "value", rc)
        if rc_val == 0:
            self.is_connected = True
            logger.info(f"Successfully connected to MQTT Broker at {self.broker_host}:{self.broker_port}")
        else:
            self.is_connected = False
            logger.warning(f"MQTT connection attempt returned code {rc_val}")

    def _on_disconnect(self, client, userdata, disconnect_flags, rc=None, properties=None):
        """Callback executed by Paho when the connection to the broker is lost.

        Sets ``is_connected = False`` so subsequent ``publish()`` calls
        degrade gracefully instead of throwing exceptions.
        """
        self.is_connected = False
        logger.warning(f"Disconnected from MQTT Broker (code {rc})")

    def connect(self, retry_count: int = 2, retry_delay: float = 0.5) -> bool:
        """Connects to the MQTT broker with configurable retry logic.

        Multiple attempts are made because the Mosquitto container in Docker
        Compose may take a few seconds to start accepting connections after
        boot.  Without retries, the gateway would fail on its first attempt
        during ``docker-compose up``.

        Args:
            retry_count: Number of connection attempts before giving up.
            retry_delay: Delay between consecutive attempts (seconds).

        Returns:
            ``True`` if the connection was established, ``False`` otherwise.
            A ``False`` return does NOT stop the pipeline — it simply means
            messages will be logged locally instead of being transported.
        """
        for attempt in range(1, retry_count + 1):
            try:
                self.client.connect(self.broker_host, self.broker_port, self.keepalive)
                # Start the Paho network loop in a background thread.
                # This thread handles keepalive pings, incoming ACKs, and
                # reconnection attempts automatically.
                self.client.loop_start()
                time.sleep(0.5)  # Allow the async event loop to settle
                if self.is_connected:
                    return True
            except Exception as e:
                logger.warning(f"MQTT connect attempt {attempt}/{retry_count} failed: {e}")
                time.sleep(retry_delay)

        logger.error(f"Could not establish MQTT connection to {self.broker_host}:{self.broker_port}")
        return False

    def publish(self, topic: str, payload_json: str, qos: int = 1) -> bool:
        """Publishes a JSON payload to the specified MQTT topic.

        Uses QoS 1 ("at least once") by default, which means the broker will
        acknowledge receipt and the client will retry if no ACK is received.
        This ensures observation delivery even on unreliable networks.

        Args:
            topic: MQTT topic string (e.g. ``study/user-01/eeg/observation``).
            payload_json: Serialised FHIR R4 Observation JSON string.
            qos: MQTT Quality of Service level (0, 1, or 2).

        Returns:
            ``True`` if the message was successfully published and acknowledged
            by the broker, ``False`` if the client is offline or an error
            occurred.
        """
        if not self.is_connected:
            logger.debug(f"Attempting publish while disconnected. Payload to topic '{topic}' dropped.")
            return False

        try:
            info = self.client.publish(topic, payload_json, qos=qos)
            # Wait up to 2 seconds for the broker to acknowledge the message.
            info.wait_for_publish(timeout=2.0)
            return info.is_published()
        except Exception as e:
            logger.error(f"Failed to publish MQTT message to topic '{topic}': {e}")
            return False

    def disconnect(self) -> None:
        """Gracefully disconnects from the broker and stops the network loop.

        Should be called during pipeline shutdown to release resources and
        ensure any in-flight messages are flushed.
        """
        try:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False
            logger.info("MQTT Client disconnected cleanly.")
        except Exception as e:
            logger.warning(f"Error during MQTT client disconnect: {e}")
