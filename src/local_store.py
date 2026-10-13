"""Local Gateway FHIR Store — Offline-First SQLite Persistence.

WHY THIS MODULE EXISTS
----------------------
Healthcare IoT systems must guarantee **zero data loss** — even when the
MQTT broker is unreachable due to network outages, container restarts, or
infrastructure failures.

This module implements a local SQLite-backed store that persists every
generated FHIR R4 Observation as a JSON document **before** it is published
to the MQTT broker.  This write-ahead strategy ensures that:

    1. Every clinical observation exists on disk immediately after creation.
    2. If MQTT publication fails, the observation is marked ``mqtt_published=0``
       and can be retried later by a sync mechanism.
    3. The complete audit trail is preserved locally on the edge gateway,
       satisfying the offline-first requirement of the IoHT architecture.

HOW IT WORKS
------------
``LocalFhirStore`` wraps a SQLite database with a single ``observations``
table.  Each row stores:
    - ``id``:              FHIR Observation resource ID (primary key).
    - ``subject_id``:      Patient / subject identifier.
    - ``timestamp``:       ISO 8601 effective datetime of the observation.
    - ``fhir_json``:       Complete serialised FHIR R4 Observation JSON.
    - ``mqtt_published``:  Boolean flag (0/1) tracking delivery status.
    - ``created_at``:      Row insertion timestamp for auditing.

The ``save_observation()`` method uses ``INSERT OR REPLACE`` for idempotency,
and ``get_unpublished_observations()`` enables future retry/sync workflows.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/fhir_observations.db"


class LocalFhirStore:
    """SQLite-backed local store for FHIR R4 Observation JSON documents.

    Each Observation is stored as a row containing the observation ID,
    subject reference, timestamp, the full FHIR JSON payload, and a
    boolean flag indicating whether it has been successfully forwarded
    to the MQTT broker.
    """

    def __init__(self, db_path: Optional[str] = None):
        """Initialises the store and creates the table if it does not exist.

        Args:
            db_path: Filesystem path for the SQLite database file.
                     Defaults to ``data/fhir_observations.db``.
                     Parent directories are created automatically.
        """
        self.db_path = db_path or _DEFAULT_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._create_table()

    def _create_table(self) -> None:
        """Creates the observations table if it does not already exist.

        The schema is intentionally flat — no foreign keys or joins are
        needed because each row is a self-contained FHIR JSON document.
        """
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id            TEXT PRIMARY KEY,
                subject_id    TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                fhir_json     TEXT NOT NULL,
                mqtt_published INTEGER DEFAULT 0,
                created_at    TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def save_observation(
        self,
        observation_id: str,
        subject_id: str,
        timestamp: str,
        fhir_json: str,
        mqtt_published: bool = False,
    ) -> None:
        """Persists a FHIR R4 Observation JSON document to the local store.

        Uses INSERT OR REPLACE to handle duplicate observation IDs
        gracefully (e.g. during pipeline restarts with overlapping windows).

        Args:
            observation_id: FHIR Observation resource ID (e.g. "obs-user-01-w42").
            subject_id: Patient / subject identifier.
            timestamp: ISO 8601 effective datetime of the observation.
            fhir_json: Complete serialised FHIR R4 Observation JSON string.
            mqtt_published: Whether the observation was successfully published
                            to the MQTT broker.
        """
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO observations
                   (id, subject_id, timestamp, fhir_json, mqtt_published, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    observation_id,
                    subject_id,
                    timestamp,
                    fhir_json,
                    1 if mqtt_published else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to persist observation {observation_id}: {e}")

    def get_unpublished_observations(self, limit: int = 100) -> List[Tuple[str, str]]:
        """Retrieves observations that have not been published to MQTT.

        Used by a future sync/retry mechanism to re-publish observations
        that were stored locally while the broker was offline.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of tuples ``(observation_id, fhir_json)``.
        """
        cursor = self.conn.execute(
            "SELECT id, fhir_json FROM observations WHERE mqtt_published = 0 "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        return cursor.fetchall()

    def mark_as_published(self, observation_id: str) -> None:
        """Marks an observation as successfully published to the MQTT broker.

        Args:
            observation_id: The FHIR Observation resource ID to mark.
        """
        self.conn.execute(
            "UPDATE observations SET mqtt_published = 1 WHERE id = ?",
            (observation_id,),
        )
        self.conn.commit()

    def count_all(self) -> int:
        """Returns the total number of observations stored locally.

        Useful for pipeline telemetry and integration testing.
        """
        cursor = self.conn.execute("SELECT COUNT(*) FROM observations")
        return cursor.fetchone()[0]

    def close(self) -> None:
        """Closes the SQLite database connection."""
        self.conn.close()
