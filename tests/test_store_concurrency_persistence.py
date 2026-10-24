import concurrent.futures
import threading
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest
import sqlite3

from services.store import (
    SevaHealthStore,
    InMemoryHealthcareStore,
    CitizenRecord,
    UserRecord,
    store,
)
from packages.types.enums import UserRole, Gender
from packages.clinical_models.observations import Observation
from packages.clinical_models.screening import ScreeningSession
from packages.observability.metrics import metrics


def test_in_memory_healthcare_store_alias():
    """Verify InMemoryHealthcareStore alias is identical to SevaHealthStore and has RLock."""
    assert InMemoryHealthcareStore is SevaHealthStore
    instance = InMemoryHealthcareStore()
    assert hasattr(instance, "_lock")
    assert isinstance(instance._lock, type(threading.RLock()))


def test_concurrent_observations_ingestion_race_condition_prevention():
    """Verify that multiple concurrent threads appending observations do not drop data.
    
    Addresses: Race Conditions in In-Memory Store (services/store.py)
    """
    test_store = SevaHealthStore()
    citizen_id = f"cit-concurrent-{uuid.uuid4().hex[:6]}"
    total_workers = 20
    observations_per_worker = 10
    total_expected = total_workers * observations_per_worker

    def worker_task(worker_idx: int):
        for i in range(observations_per_worker):
            obs = Observation(
                id=f"obs-{worker_idx}-{i}-{uuid.uuid4().hex[:4]}",
                citizen_id=citizen_id,
                code="8867-4",  # Heart rate
                display_name="Heart Rate",
                value=70 + worker_idx,
                unit="beats/min",
                recorded_at=datetime.now(timezone.utc) - timedelta(seconds=(worker_idx * 100 + i)),
                source="CLINICAL_DEVICE",
                source_event_id=f"evt-{worker_idx}-{i}-{uuid.uuid4().hex[:6]}",
            )
            test_store.add_observation(obs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_task, idx) for idx in range(total_workers)]
        concurrent.futures.wait(futures)
        for f in futures:
            f.result()  # Re-raise any thread exceptions

    preserved = test_store.get_citizen_observations(citizen_id, sort_chronological=False)
    assert len(preserved) == total_expected, f"Expected {total_expected} observations, got {len(preserved)}"


def test_concurrent_citizens_and_screenings_integrity():
    """Verify concurrent check-then-act operations across citizens and screenings are thread-safe."""
    test_store = SevaHealthStore()
    total_citizens = 30

    def create_and_screen(idx: int):
        cit_id = f"cit-con-{idx}-{uuid.uuid4().hex[:4]}"
        citizen = CitizenRecord(
            id=cit_id,
            tenant_id="tenant-karnataka",
            user_id=f"user-{idx}",
            abha_id=f"99-9999-{idx:04d}",
            first_name="Concurrency",
            last_name=f"Patient-{idx}",
            birth_date="1980-01-01",
            gender=Gender.FEMALE,
            phone="9876543210",
            state="Karnataka",
            district="Mysuru",
            sub_district="Mysuru",
            village_or_ward="Ward-1",
        )
        test_store.add_citizen(citizen)

        session = ScreeningSession(
            id=f"scr-{idx}",
            citizen_id=cit_id,
            tenant_id="tenant-karnataka",
            conducted_by_id="worker-01",
            status="COMPLETED",
        )
        test_store.add_screening(session)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(create_and_screen, idx) for idx in range(total_citizens)]
        concurrent.futures.wait(futures)
        for f in futures:
            f.result()

    assert len(test_store.list_citizens("tenant-karnataka")) >= total_citizens


def test_sqlite_persistence_failure_logging_and_metrics():
    """Verify that SQLite persistence failures log error and record metrics rather than silently passing.
    
    Addresses: Silent Persistence Failures in SQLite Sync (services/store.py)
    """
    test_store = SevaHealthStore()
    user = UserRecord(
        id=f"usr-fail-{uuid.uuid4().hex[:4]}",
        tenant_id="tenant-01",
        email=f"fail-{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="hashed_pw_test",
        role=UserRole.CITIZEN,
        full_name="Fail Test",
    )
    citizen = CitizenRecord(
        id=f"cit-fail-{uuid.uuid4().hex[:4]}",
        tenant_id="tenant-01",
        user_id=user.id,
        abha_id=None,
        first_name="Fail",
        last_name="Citizen",
        birth_date="1990-01-01",
        gender=Gender.MALE,
        phone="9999999999",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru",
        village_or_ward="Ward-01",
    )

    initial_error_count = metrics.errors_by_type.get("SQLITE_PERSIST_USER_FAILURE", 0)
    initial_cit_error_count = metrics.errors_by_type.get("SQLITE_PERSIST_CITIZEN_FAILURE", 0)

    # Simulate SQLite lock/disk failure
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
        with patch("services.store.logger.error") as mock_logger_error:
            persist_success = test_store._persist_user_to_sqlite(user)
            assert persist_success is False
            assert mock_logger_error.called
            assert "database is locked" in str(mock_logger_error.call_args)
            assert metrics.errors_by_type.get("SQLITE_PERSIST_USER_FAILURE", 0) > initial_error_count

        with patch("services.store.logger.error") as mock_logger_error:
            persist_success_cit = test_store._persist_citizen_to_sqlite(citizen)
            assert persist_success_cit is False
            assert mock_logger_error.called
            assert "database is locked" in str(mock_logger_error.call_args)
            assert metrics.errors_by_type.get("SQLITE_PERSIST_CITIZEN_FAILURE", 0) > initial_cit_error_count


def test_sqlite_init_and_load_failure_logging_and_metrics():
    """Verify _init_sqlite and _load_from_sqlite log error and record metrics on failure."""
    test_store = SevaHealthStore()

    initial_init_errors = metrics.errors_by_type.get("SQLITE_INIT_FAILURE", 0)
    initial_load_errors = metrics.errors_by_type.get("SQLITE_LOAD_FAILURE", 0)

    with patch("sqlite3.connect", side_effect=sqlite3.DatabaseError("corrupt database header")):
        with patch("services.store.logger.error") as mock_logger_error:
            test_store._init_sqlite()
            assert mock_logger_error.called
            assert metrics.errors_by_type.get("SQLITE_INIT_FAILURE", 0) > initial_init_errors

        with patch("pathlib.Path.exists", return_value=True):
            with patch("services.store.logger.error") as mock_logger_error:
                test_store._load_from_sqlite()
                assert mock_logger_error.called
                assert metrics.errors_by_type.get("SQLITE_LOAD_FAILURE", 0) > initial_load_errors
