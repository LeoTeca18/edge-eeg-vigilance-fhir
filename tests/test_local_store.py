"""Unit tests for the LocalFhirStore (SQLite offline-first persistence).

These tests verify that the local FHIR store correctly:

    1. Persists observations to SQLite and retrieves them.
    2. Tracks MQTT publication status (published vs unpublished).
    3. Supports idempotent upserts (INSERT OR REPLACE).
    4. Returns correct counts and unpublished observation lists.
"""

import json
import os
import tempfile
import pytest
from src.local_store import LocalFhirStore


@pytest.fixture
def temp_store():
    """Creates a LocalFhirStore backed by a temporary SQLite file.

    The database file is automatically cleaned up after the test completes.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_observations.db")
        store = LocalFhirStore(db_path=db_path)
        yield store
        store.close()


def test_save_and_count(temp_store):
    """Verifies that saving an observation increments the total count."""
    assert temp_store.count_all() == 0

    temp_store.save_observation(
        observation_id="obs-user-01-w0",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:00Z",
        fhir_json='{"resourceType": "Observation", "id": "obs-user-01-w0"}',
        mqtt_published=False,
    )

    assert temp_store.count_all() == 1


def test_unpublished_observations(temp_store):
    """Verifies that unpublished observations are returned correctly."""
    temp_store.save_observation(
        observation_id="obs-01",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:00Z",
        fhir_json='{"id": "obs-01"}',
        mqtt_published=False,
    )
    temp_store.save_observation(
        observation_id="obs-02",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:01Z",
        fhir_json='{"id": "obs-02"}',
        mqtt_published=True,
    )

    unpublished = temp_store.get_unpublished_observations()
    assert len(unpublished) == 1
    assert unpublished[0][0] == "obs-01"


def test_mark_as_published(temp_store):
    """Verifies that marking an observation as published updates its status."""
    temp_store.save_observation(
        observation_id="obs-03",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:00Z",
        fhir_json='{"id": "obs-03"}',
        mqtt_published=False,
    )

    assert len(temp_store.get_unpublished_observations()) == 1

    temp_store.mark_as_published("obs-03")

    assert len(temp_store.get_unpublished_observations()) == 0


def test_idempotent_upsert(temp_store):
    """Verifies that saving the same observation ID twice does not duplicate rows."""
    temp_store.save_observation(
        observation_id="obs-04",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:00Z",
        fhir_json='{"id": "obs-04", "version": 1}',
        mqtt_published=False,
    )
    temp_store.save_observation(
        observation_id="obs-04",
        subject_id="user-01",
        timestamp="2026-01-01T10:00:00Z",
        fhir_json='{"id": "obs-04", "version": 2}',
        mqtt_published=True,
    )

    assert temp_store.count_all() == 1
    # The latest insert should have overwritten the first.
    unpublished = temp_store.get_unpublished_observations()
    assert len(unpublished) == 0  # Now marked as published
