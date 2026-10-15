"""Test Suite for TC-TRJ-001, TC-TRJ-002, and TC-TRJ-003:
- TC-TRJ-001: Improving trajectory (Priority: P1)
- TC-TRJ-002: Worsening trajectory (Priority: P0)
- TC-TRJ-003: Incomparable model versions & scoring scales (Priority: P1)
"""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, ClinicianReviewStatus, TriageUrgency
from packages.auth.jwt import create_access_token
from services.trajectory.models import (
    RiskSnapshot,
    TrajectoryTrendState,
    CitizenTrajectoryReport,
    TrajectoryComparisonRequest,
    TrajectoryComparisonResponse,
)
from services.trajectory.engine import risk_trajectory_engine

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"
CITIZEN_ID = "citizen-trj-test-01"


@pytest.fixture
def clinician_auth_headers():
    token = create_access_token(
        subject="clinician-dr-kulkarni-01",
        tenant_id=TENANT_ID,
        role=UserRole.CLINICIAN,
        scopes=["clinical:read", "clinical:write", "risk:read", "risk:create"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_citizen_fixture():
    """Ensure a synthetic citizen exists in store with assignment and consent."""
    c = CitizenRecord(
        id=CITIZEN_ID,
        tenant_id=TENANT_ID,
        user_id="user-trj-01",
        abha_id="91-1111-2222-3333",
        first_name="Ravi",
        last_name="TrajectoryKumar",
        birth_date="1980-04-12",
        gender=Gender.MALE,
        phone="+919876543299",
        state="Karnataka",
        district="Bengaluru Urban",
        sub_district="Bengaluru South",
        village_or_ward="Ward 120",
    )
    store.citizens[CITIZEN_ID] = c

    clinician = UserRecord(
        id="clinician-dr-kulkarni-01",
        tenant_id=TENANT_ID,
        email="dr.kulkarni@health.karnataka.gov.in",
        hashed_password="mock_hashed_password",
        role=UserRole.CLINICIAN,
        full_name="Dr. Kulkarni",
        assigned_patients=[CITIZEN_ID],
    )
    store.add_user(clinician)

    consent = ConsentDirective(
        id="consent-trj-01",
        citizen_id=CITIZEN_ID,
        grantee_id="clinician-dr-kulkarni-01",
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
        scope=["OBSERVATIONS", "RISK_ASSESSMENT", "TRAJECTORY"],
    )
    store.add_consent(consent)
    store.clear_trajectory_snapshots(CITIZEN_ID)


# ==============================================================================
# TC-TRJ-001: Improving Trajectory Tests (Priority: P1)
# ==============================================================================

class TestTCTRJ001ImprovingTrajectory:

    def test_improving_trajectory_identified_when_data_supports_conclusion(self, clinician_auth_headers):
        """Step 1 & 2: Seed compatible historical assessments showing an improving pattern
        and request a trajectory.
        Expected: Overall and domain trends are classified as IMPROVING.
        """
        resp = client.post(
            f"/api/v1/trajectory/simulate/improving/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Classification verification
        assert data["overall_trend"] == "IMPROVING"
        assert data["composite_change_percentage"] is not None
        assert data["composite_change_percentage"] < 0, "Composite delta must be negative for improving trajectory"

        # Check key domains
        trajs = data["domain_trajectories"]
        assert trajs["diabetes"]["trend"] == "IMPROVING"
        assert trajs["hypertension"]["trend"] == "IMPROVING"
        assert trajs["metabolic"]["trend"] == "IMPROVING"

        # Review workflow should remain routine for improving trajectory
        assert data["review_required"] is False
        assert data["review_workflow_status"] == "STANDARD"

    def test_inspect_contributing_observations_in_improving_trajectory(self, clinician_auth_headers):
        """Step 3: Inspect the contributing observations in an improving trajectory.
        Expected: Factors clearly detail biomarker reductions (e.g. HbA1c, SBP, BMI)
        strictly using non-causal language.
        """
        # Ensure improving trajectory is seeded
        client.post(
            f"/api/v1/trajectory/simulate/improving/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        resp = client.get(
            f"/api/v1/trajectory/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()

        dm_factors = data["domain_trajectories"]["diabetes"]["contributing_factors"]
        htn_factors = data["domain_trajectories"]["hypertension"]["contributing_factors"]
        lifestyle_factors = data["domain_trajectories"]["lifestyle"]["contributing_factors"]

        assert len(dm_factors) > 0, "Diabetes domain must provide contributing factors"
        assert any("reduction" in f.lower() or "glycemic" in f.lower() for f in dm_factors)
        assert any("reduction" in f.lower() or "vascular" in f.lower() for f in htn_factors)
        assert any("walking" in f.lower() or "steps" in f.lower() for f in lifestyle_factors)

        # Strict linguistic non-causality assertion
        all_factors = [f for d in data["domain_trajectories"].values() for f in d["contributing_factors"]]
        for f in all_factors:
            assert "caused by" not in f.lower(), f"Forbidden phrasing 'caused by' found in: {f}"
            assert any(term in f.lower() for term in ["associated with", "contributing factor", "may be contributing", "reduction", "increase"])

    def test_improving_trend_not_claimed_if_score_delta_below_threshold(self):
        """Expected: System identifies an improving trend ONLY if the configured method
        and available data support that conclusion (e.g. score delta >= 0.04).
        """
        now = datetime.now(timezone.utc)
        snap1 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now - timedelta(days=60),
            domain_scores={"diabetes": 0.50},
            domain_tiers={"diabetes": "MODERATE"},
        )
        # Minor fluctuation: 0.50 -> 0.48 (delta = -0.02, below 0.04 threshold)
        snap2 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now,
            domain_scores={"diabetes": 0.48},
            domain_tiers={"diabetes": "MODERATE"},
        )
        report = risk_trajectory_engine.evaluate_trajectory(CITIZEN_ID, [snap1, snap2])
        assert report.domain_trajectories["diabetes"].trend == TrajectoryTrendState.STABLE
        assert report.domain_trajectories["diabetes"].trend != TrajectoryTrendState.IMPROVING


# ==============================================================================
# TC-TRJ-002: Worsening Trajectory Tests (Priority: P0)
# ==============================================================================

class TestTCTRJ002WorseningTrajectory:

    def test_worsening_trajectory_classification(self, clinician_auth_headers):
        """Step 1 & 2: Seed compatible historical observations showing a worsening pattern
        and request a trajectory.
        Expected: Trend is represented correctly as WORSENING.
        """
        resp = client.post(
            f"/api/v1/trajectory/simulate/worsening/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["overall_trend"] == "WORSENING"
        assert data["composite_change_percentage"] > 0, "Composite delta must be positive for worsening"

        # Check domain trajectory classification
        trajs = data["domain_trajectories"]
        assert trajs["diabetes"]["trend"] == "WORSENING"
        assert trajs["hypertension"]["trend"] == "WORSENING"
        assert trajs["cardiovascular"]["trend"] == "WORSENING"

    def test_worsening_trajectory_explanation_and_review_workflow_behavior(self, clinician_auth_headers):
        """Step 3: Inspect explanation and review-workflow behavior.
        Expected: Explanations detail deteriorating observations, review_required is True,
        review_workflow_status is CLINICIAN_REVIEW_REQUIRED, review_priority is PRIORITY/EMERGENT,
        and automatic triage escalation creates a ClinicalTriageCase with SOAP note.
        """
        # Ensure worsening trajectory is seeded
        client.post(
            f"/api/v1/trajectory/simulate/worsening/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        resp = client.get(
            f"/api/v1/trajectory/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()

        # Explanation check
        dm_factors = data["domain_trajectories"]["diabetes"]["contributing_factors"]
        htn_factors = data["domain_trajectories"]["hypertension"]["contributing_factors"]
        assert any("elevated" in f.lower() or "glycemic" in f.lower() or "hba1c" in f.lower() for f in dm_factors)
        assert any("elevation" in f.lower() or "vascular" in f.lower() or "blood pressure" in f.lower() for f in htn_factors)

        # Review workflow behavior check
        assert data["review_required"] is True, "Worsening trajectory MUST trigger clinician review"
        assert data["review_workflow_status"] == "CLINICIAN_REVIEW_REQUIRED"
        assert data["review_priority"] in ["PRIORITY", "EMERGENT"]
        assert len(data["recommended_review_actions"]) > 0

        # Automatic Clinical Triage Escalation into Store Check
        triage_id = data.get("triage_case_id")
        assert triage_id is not None, "Worsening trajectory must record triage_case_id"

        stored_case = store.get_triage_case(triage_id)
        assert stored_case is not None, f"Triage case {triage_id} must be persisted in database store"
        assert stored_case.citizen_id == CITIZEN_ID
        assert stored_case.status == ClinicianReviewStatus.PENDING
        assert stored_case.urgency in [TriageUrgency.PRIORITY, TriageUrgency.EMERGENT]
        assert stored_case.soap_note is not None
        assert "trajectory" in stored_case.soap_note.subjective.lower()


# ==============================================================================
# TC-TRJ-003: Incomparable Model Versions & Scales Tests (Priority: P1)
# ==============================================================================

class TestTCTRJ003IncomparableModelVersions:

    def test_incompatible_scoring_scales_refusal_and_explanation(self, clinician_auth_headers):
        """Step 1 & 2: Seed historical assessments using incompatible scoring scales
        (PROBABILITY_0_1 vs INTEGER_0_100) and request a comparison.
        Expected: System refuses numerical comparison and clearly explains the limitation.
        """
        resp = client.post(
            f"/api/v1/trajectory/simulate/incomparable/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Core TC-TRJ-003 Assertions
        assert data["is_comparable"] is False
        assert data["numerical_comparison_refused"] is True
        assert data["limitation_reason"] is not None
        assert "Incompatible scoring scales" in data["limitation_reason"]
        assert "Direct numerical comparison is refused" in data["limitation_reason"]

        # Report checks
        report = data["report"]
        assert report["is_comparable"] is False
        assert report["numerical_comparison_refused"] is True
        assert report["overall_trend"] == "INSUFFICIENT_DATA"
        assert report["composite_current_score"] is None, "Incomparable numerical delta must be null"
        assert report["composite_change_percentage"] is None, "Incomparable percentage shift must be null"
        assert len(report["comparison_limitations"]) > 0

        # Review workflow recommends re-baselining
        assert report["review_required"] is True
        assert report["review_workflow_status"] == "CLINICIAN_REVIEW_REQUIRED"
        assert any("re-baseline" in act.lower() for act in report["recommended_review_actions"])

    def test_strict_mode_rejects_incomparable_comparison_with_http_422(self, clinician_auth_headers):
        """Expected: In strict mode, comparison endpoint raises HTTP 422 Unprocessable Entity
        refusing mathematical comparison.
        """
        # Ensure incomparable historical snapshots are seeded
        client.post(
            f"/api/v1/trajectory/simulate/incomparable/{CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        resp = client.post(
            f"/api/v1/trajectory/{CITIZEN_ID}/compare?strict=true",
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 422, f"Expected 422 refusal, got {resp.status_code}"
        assert "Numerical comparison refused" in resp.json()["detail"]

    def test_incompatible_model_major_versions_refusal(self):
        """Expected: Divergence in major model versions (v1.0 vs v2.0) triggers comparability refusal."""
        now = datetime.now(timezone.utc)
        snap1 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now - timedelta(days=90),
            domain_scores={"diabetes": 0.30},
            model_name="NCD_COMPOSITE_RISK_ENGINE",
            model_version="v1.0.0",
            scoring_scale="PROBABILITY_0_1",
        )
        snap2 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now,
            domain_scores={"diabetes": 0.50},
            model_name="NCD_COMPOSITE_RISK_ENGINE",
            model_version="v2.0.0",  # Divergent major version
            scoring_scale="PROBABILITY_0_1",
        )
        is_comp, reason = risk_trajectory_engine.check_snapshot_compatibility(snap1, snap2)
        assert is_comp is False
        assert "Incompatible model versions" in reason
        assert "major version divergence" in reason

    def test_demonstration_vs_validated_model_comparison_refusal(self):
        """Expected: Cannot compare unvalidated demonstration proxy model with clinically validated model."""
        now = datetime.now(timezone.utc)
        snap1 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now - timedelta(days=90),
            domain_scores={"hypertension": 0.30},
            is_demonstration_only=True,
            is_clinically_validated=False,
        )
        snap2 = RiskSnapshot(
            citizen_id=CITIZEN_ID,
            timestamp=now,
            domain_scores={"hypertension": 0.50},
            is_demonstration_only=False,
            is_clinically_validated=True,
        )
        is_comp, reason = risk_trajectory_engine.check_snapshot_compatibility(snap1, snap2)
        assert is_comp is False
        assert "Cannot compare unvalidated demonstration proxy" in reason


# ==============================================================================
# DATABASE INSPECTION TESTS: Verifying Persisted Snapshots in Database
# ==============================================================================

class TestTrajectoryDatabasePersistence:

    def test_database_snapshot_query_and_lifecycle(self, clinician_auth_headers):
        """Inspect stored observations and snapshots in database store."""
        # 1. Clear database snapshots
        del_resp = client.delete(f"/api/v1/trajectory/snapshots/{CITIZEN_ID}", headers=clinician_auth_headers)
        assert del_resp.status_code == 200

        # Verify empty
        query_resp = client.get(f"/api/v1/trajectory/snapshots/{CITIZEN_ID}", headers=clinician_auth_headers)
        assert query_resp.status_code == 200
        assert len(query_resp.json()) == 0

        # 2. Seed improving snapshots into DB
        client.post(f"/api/v1/trajectory/simulate/improving/{CITIZEN_ID}", headers=clinician_auth_headers)

        # 3. Query DB
        query_resp2 = client.get(f"/api/v1/trajectory/snapshots/{CITIZEN_ID}", headers=clinician_auth_headers)
        assert query_resp2.status_code == 200
        snaps = query_resp2.json()
        assert len(snaps) == 3, "Database must store all 3 historical snapshots"
        assert snaps[0]["scoring_scale"] == "PROBABILITY_0_1"
        assert snaps[2]["key_biomarkers"]["HBA1C"] == 5.4
