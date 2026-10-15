"""Section 18: Test Cases — Deployment and Challenge Demo.

Covers:
- TC-DEP-001 (P0): Clean-environment startup (components health checks & application URLs access)
- TC-DEP-002 (P0): Database migration (empty database, migration application, schema verification)
- TC-DEP-003 (P0): Synthetic-data seeding (reproducible personas, clinical relationships, explicitly synthetic)
- TC-DEP-004 (P0): Complete five-minute demonstration (8-step public-health value chain without defects)
- TC-DEP-005 (P1): Demo reset (run demo, reset mechanism, repeat workflow successfully)
"""

import pytest
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store

client = TestClient(app)


def test_tc_dep_001_clean_environment_startup():
    """TC-DEP-001: Verifies clean environment startup, core components health, and documented access URLs."""
    resp = client.post("/api/v1/deployment/startup-audit")
    assert resp.status_code == 200, f"Startup audit failed: {resp.text}"
    data = resp.json()
    assert data["test_case"] == "TC-DEP-001"
    assert data["status"] == "PASSED"
    assert data["components_healthy"] is True
    assert data["all_urls_accessible"] is True

    comps = data["core_components"]
    assert comps["api_gateway"] == "ONLINE"
    assert comps["identity_service"] == "ONLINE"
    assert comps["risk_engine"] == "ONLINE"
    assert comps["intervention_engine"] == "ONLINE"
    assert comps["ai_agent_runtime"] == "ONLINE"
    assert comps["fhir_r4_layer"] == "ONLINE"
    assert comps["wearable_service"] == "ONLINE"
    assert comps["population_intelligence"] == "ONLINE"
    assert comps["risk_trajectory_engine"] == "ONLINE"

    urls = data["application_urls"]
    assert "web_portal" in urls
    assert "swagger_api_docs" in urls
    assert "health_worker_app" in urls
    assert "citizen_mobile_app" in urls
    assert "public_health_dashboard" in urls
    assert "observability_portal" in urls
    assert len(data["access_credentials"]) >= 4


def test_tc_dep_002_database_migration():
    """TC-DEP-002: Verifies database migration application and schema integrity."""
    resp = client.post("/api/v1/deployment/migration-audit")
    assert resp.status_code == 200, f"Migration audit failed: {resp.text}"
    data = resp.json()
    assert data["test_case"] == "TC-DEP-002"
    assert data["status"] == "PASSED"
    assert data["migration_status"] == "APPLIED_AND_VERIFIED"
    assert data["schema_integrity_verified"] is True
    assert data["total_tables_defined"] >= 8

    tables = data["verified_tables"]
    assert "patients" in tables
    assert "profiles" in tables
    assert "organizations" in tables
    assert "care_teams" in tables


def test_tc_dep_003_synthetic_data_seeding():
    """TC-DEP-003: Verifies synthetic data seeding reproducibility and clinical relationships."""
    resp = client.post("/api/v1/deployment/seeding-audit")
    assert resp.status_code == 200, f"Seeding audit failed: {resp.text}"
    data = resp.json()
    assert data["test_case"] == "TC-DEP-003"
    assert data["status"] == "PASSED"
    assert data["seeding_successful"] is True
    assert data["accounts_seeded"] >= 8
    assert data["personas_seeded"] >= 8
    assert data["explicitly_synthetic"] is True
    assert data["relationships_verified"] is True
    assert data["reproducibility_verified"] is True
    assert len(data["seeded_personas_sample"]) >= 3


def test_tc_dep_004_complete_five_minute_demonstration():
    """TC-DEP-004: Verifies complete 8-step five-minute demonstration public-health value chain."""
    resp = client.post("/api/v1/deployment/five-minute-demo")
    assert resp.status_code == 200, f"Five-minute demo failed: {resp.text}"
    data = resp.json()
    assert data["test_case"] == "TC-DEP-004"
    assert data["status"] == "PASSED"
    assert data["blocking_defects_count"] == 0
    assert data["total_execution_seconds"] < data["target_max_seconds"]
    assert data["public_health_value_chain_verified"] is True

    steps = data["workflow_steps"]
    assert len(steps) == 8
    for st in steps:
        assert st["status"] == "COMPLETED"

    # Verify database persistence
    cit_id = data["citizen_id"]
    assert store.get_citizen(cit_id) is not None
    assert len(store.get_citizen_screenings(cit_id)) > 0
    assert store.get_latest_risk(cit_id) is not None
    assert store.get_care_plan(cit_id) is not None


def test_tc_dep_005_demo_reset_and_repeatability():
    """TC-DEP-005: Verifies demo reset mechanism restores clean baseline and workflow repeatability."""
    resp = client.post("/api/v1/deployment/demo-reset-audit")
    assert resp.status_code == 200, f"Demo reset audit failed: {resp.text}"
    data = resp.json()
    assert data["test_case"] == "TC-DEP-005"
    assert data["status"] == "PASSED"
    assert data["demo_run_1_completed"] is True
    assert data["reset_successful"] is True
    assert data["baseline_restored"] is True
    assert data["demo_run_2_completed"] is True
    assert data["determinism_verified"] is True
    assert len(data["step_trace"]) == 4


def test_deployment_status():
    """Verifies deployment status overview endpoint."""
    resp = client.get("/api/v1/deployment/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["environment"] == "DEVELOPMENT_AND_DEMO"
    assert data["demo_ready"] is True
    assert data["is_database_online"] is True
