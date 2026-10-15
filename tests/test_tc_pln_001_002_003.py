"""Test Suite for Section 11: Prevention Plans and AI Assistant.

Test Cases:
- TC-PLN-001: Prevention-plan generation (Priority: P0)
- TC-PLN-002: Plan modification by clinician (Priority: P0)
- TC-PLN-003: Citizen check-in (Priority: P1)
"""

import pytest
from datetime import datetime, timezone, date
from pathlib import Path
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from services.store import store
from packages.types.enums import UserRole
from packages.auth.jwt import create_access_token
from packages.clinical_models.care_plan import (
    CarePlan,
    CitizenCheckIn,
    PlanModificationRecord,
    DEFAULT_CARE_PLAN_LIMITATIONS,
)
from packages.clinical_models.observations import Observation
from packages.clinical_models.screening import ScreeningSession
from packages.clinical_models.risk import RiskAssessment, DomainRiskScores
from packages.types.enums import RiskTier, TrajectoryTrend

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_environment():
    """Seed baseline data for testing."""
    seed_all_demo_data()


@pytest.fixture
def clinician_token():
    return create_access_token("clinician-dr-anand", "karnataka_state_health", UserRole.CLINICIAN)


@pytest.fixture
def clinician_headers(clinician_token):
    return {"Authorization": f"Bearer {clinician_token}"}


@pytest.fixture
def citizen_token():
    return create_access_token("citizen-ramesh-patel-01", "karnataka_state_health", UserRole.CITIZEN)


@pytest.fixture
def citizen_headers(citizen_token):
    return {"Authorization": f"Bearer {citizen_token}"}


@pytest.fixture
def worker_token():
    return create_access_token("hw-worker-sunita", "karnataka_state_health", UserRole.HEALTH_WORKER)


@pytest.fixture
def worker_headers(worker_token):
    return {"Authorization": f"Bearer {worker_token}"}


# =============================================================================
# TC-PLN-001: Prevention-plan generation (Priority: P0)
# =============================================================================

class TestTCPLN001PreventionPlanGeneration:
    """TC-PLN-001: Prevention-plan generation
    Priority: P0
    Steps:
      1. Complete a supported risk assessment.
      2. Request a prevention plan.
      3. Retrieve the plan.
    Expected result:
      The plan contains configured actions, goals, frequency, follow-up timing,
      and appropriate limitations.
    """

    def test_step1_complete_supported_risk_assessment(self, clinician_headers):
        """Step 1: Ensure citizen has a supported risk assessment in the system."""
        cid = "citizen-ramesh-patel-01"
        risk = store.get_latest_risk(cid)
        assert risk is not None, "Citizen must have a completed risk assessment"
        assert risk.overall_score is not None
        assert risk.overall_tier in [RiskTier.LOW, RiskTier.MODERATE, RiskTier.HIGH, RiskTier.CRITICAL]

    def test_step2_request_prevention_plan(self, clinician_headers):
        """Step 2: Request prevention plan generation via API.
        Verifies both backward-compatible CarePlan and ComprehensivePreventionPlan generation.
        """
        cid = "citizen-ramesh-patel-01"
        
        # 2.1 Generate standard care plan
        resp = client.post(f"/api/v1/intervention/generate/{cid}", headers=clinician_headers)
        assert resp.status_code == 200, f"Plan generation failed: {resp.text}"
        plan_data = resp.json()
        assert plan_data["citizen_id"] == cid
        assert plan_data["id"] is not None
        assert len(plan_data["daily_tasks"]) == 30

        # 2.2 Generate comprehensive prevention plan
        resp_comp = client.post(f"/api/v1/intervention/generate-comprehensive/{cid}", headers=clinician_headers)
        assert resp_comp.status_code == 200, f"Comprehensive plan generation failed: {resp_comp.text}"
        comp_data = resp_comp.json()
        assert comp_data["citizen_id"] == cid
        assert len(comp_data["goals"]) >= 3
        assert len(comp_data["actions"]) >= 4

    def test_step3_retrieve_plan_and_verify_required_fields(self, clinician_headers):
        """Step 3: Retrieve the plan and verify all required elements:
        configured actions, goals, frequency, follow-up timing, and appropriate limitations.
        """
        cid = "citizen-ramesh-patel-01"
        resp = client.get(f"/api/v1/intervention/plan/{cid}", headers=clinician_headers)
        assert resp.status_code == 200
        plan = resp.json()

        # 1. Configured Actions
        assert "actions" in plan and len(plan["actions"]) > 0, "Plan must contain configured actions"
        for act in plan["actions"]:
            assert "title" in act and act["title"], "Action must have a title"
            assert "description" in act and act["description"], "Action must have a description"
            assert "frequency" in act and act["frequency"], "Action must have an explicit frequency"
            assert "category" in act and act["category"], "Action must have a category"

        # 2. Goals
        assert "goals" in plan and len(plan["goals"]) > 0, "Plan must contain configured goals"
        assert any("glycemic" in g.lower() or "steps" in g.lower() or "sugar" in g.lower() or "millet" in g.lower() or "stabil" in g.lower() for g in plan["goals"])

        # 3. Frequency
        assert "frequency" in plan and plan["frequency"], "Plan must contain frequency specification"
        assert "daily" in plan["frequency"].lower() or "weekly" in plan["frequency"].lower()

        # 4. Follow-up Timing
        assert "follow_up_timing" in plan and plan["follow_up_timing"], "Plan must specify follow-up timing"
        assert "day" in plan["follow_up_timing"].lower() or "week" in plan["follow_up_timing"].lower()

        # 5. Appropriate Limitations (Critical Safety)
        assert "limitations" in plan and len(plan["limitations"]) > 0, "Plan must specify clinical limitations"
        limitations_text = " ".join(plan["limitations"]).lower()
        assert "medication" in limitations_text or "prescribe" in limitations_text, "Plan must contain non-autonomous medication limitation"
        assert "emergency" in limitations_text or "distress" in limitations_text or "108" in limitations_text, "Plan must contain emergency limitation"

    def test_comprehensive_plan_structural_completeness(self, clinician_headers):
        """Verify ComprehensivePreventionPlan contains full multi-modal schedules."""
        cid = "citizen-ramesh-patel-01"
        resp = client.get(f"/api/v1/intervention/comprehensive/{cid}", headers=clinician_headers)
        assert resp.status_code == 200
        comp = resp.json()

        assert len(comp["goals"]) >= 3
        assert len(comp["actions"]) >= 4
        assert len(comp["daily_tasks"]) == 30
        assert len(comp["reminders"]) >= 2
        assert len(comp["educational_content"]) >= 2
        assert len(comp["measurements"]) >= 2
        assert len(comp["follow_up_schedule"]) >= 2
        assert "frequency" in comp and comp["frequency"]
        assert "follow_up_timing" in comp and comp["follow_up_timing"]
        assert "limitations" in comp and len(comp["limitations"]) > 0
        assert "CRITICAL MEDICATION SAFETY" in comp["clinician_medication_disclaimer"]


# =============================================================================
# TC-PLN-002: Plan modification by clinician (Priority: P0)
# =============================================================================

class TestTCPLN002PlanModificationByClinician:
    """TC-PLN-002: Plan modification by clinician
    Priority: P0
    Steps:
      1. Generate a plan.
      2. Authenticate as an authorized clinician.
      3. Modify an allowed plan field.
      4. Save and retrieve the plan history.
    Expected result:
      The approved change is persisted with actor, timestamp and version history.
    """

    def test_step1_and_unauthorized_modification_denied(self, citizen_headers, worker_headers):
        """Unauthorized roles (citizens, health workers) must be denied modification."""
        cid = "citizen-ramesh-patel-01"
        payload = {
            "nutrition_guidance": "Unauthorized modification attempt.",
            "reason": "Tamper test"
        }

        # Citizen attempt -> 403 Forbidden
        res_citizen = client.put(f"/api/v1/intervention/plan/{cid}", json=payload, headers=citizen_headers)
        assert res_citizen.status_code == 403, "Citizen must not be allowed to modify clinical plan"
        assert "Only authorized clinicians" in res_citizen.json()["detail"]

        # Health worker attempt -> 403 Forbidden
        res_worker = client.put(f"/api/v1/intervention/plan/{cid}", json=payload, headers=worker_headers)
        assert res_worker.status_code == 403, "Health worker must not be allowed to modify clinical plan"

    def test_step2_and_step3_authorized_clinician_modifies_allowed_fields(self, clinician_headers):
        """Steps 2 & 3: Authenticate as authorized clinician and modify allowed fields."""
        cid = "citizen-ramesh-patel-01"

        # Baseline plan check
        base_resp = client.get(f"/api/v1/intervention/plan/{cid}", headers=clinician_headers)
        assert base_resp.status_code == 200
        initial_plan = base_resp.json()
        initial_version = initial_plan.get("version", 1)

        # Clinician modifications: update nutrition guidance, goals, and clinical notes
        custom_goals = [
            "Maintain fasting blood glucose < 100 mg/dL",
            "Achieve 8,500 brisk walking steps daily",
            "Substitute refined carbohydrates with sorghum/ragi",
        ]
        custom_nutrition = "Strict low-glycemic Mediterranean-Indian diet: increase fenugreek and millet intake; zero added table sugar."

        update_payload = {
            "goals": custom_goals,
            "nutrition_guidance": custom_nutrition,
            "notes": "Reviewed and tailored during routine tele-consultation.",
            "reason": "Metabolic glycemic optimization",
        }

        # Step 4: Save modification
        put_resp = client.put(f"/api/v1/intervention/plan/{cid}", json=update_payload, headers=clinician_headers)
        assert put_resp.status_code == 200, f"Plan modification failed: {put_resp.text}"
        updated_plan = put_resp.json()

        # Verify persisted fields
        assert updated_plan["goals"] == custom_goals
        assert updated_plan["nutrition_guidance"] == custom_nutrition
        assert updated_plan["version"] == initial_version + 1
        assert updated_plan["clinician_reviewed"] is True
        assert updated_plan["clinician_id"] == "clinician-dr-anand"

    def test_step4_save_and_retrieve_plan_history(self, clinician_headers):
        """Step 4: Save and retrieve the plan history.
        Verify actor, timestamp, version_before, version_after, and modified fields are recorded.
        """
        cid = "citizen-ramesh-patel-01"

        # 4.1 Retrieve history endpoint
        hist_resp = client.get(f"/api/v1/intervention/plan/{cid}/history", headers=clinician_headers)
        assert hist_resp.status_code == 200
        history = hist_resp.json()
        assert len(history) >= 1, "Plan history must contain at least one modification record"

        latest_record = history[-1]
        assert latest_record["actor_id"] == "clinician-dr-anand"
        assert latest_record["actor_role"] == "CLINICIAN"
        assert latest_record["version_after"] >= 2
        assert "nutrition_guidance" in latest_record["fields_modified"]
        assert "goals" in latest_record["fields_modified"]
        assert latest_record["timestamp"] is not None
        assert latest_record["reason"] == "Metabolic glycemic optimization"

        # 4.2 Perform a second modification to verify cumulative version history
        second_payload = {
            "activity_guidance": "Increase daily post-dinner walk duration to 20 minutes.",
            "reason": "Post-prandial glycemic spike reduction",
        }
        res_v3 = client.put(f"/api/v1/intervention/plan/{cid}", json=second_payload, headers=clinician_headers)
        assert res_v3.status_code == 200
        plan_v3 = res_v3.json()
        assert plan_v3["version"] == latest_record["version_after"] + 1

        # Check full history trail
        hist_v3_resp = client.get(f"/api/v1/intervention/plan/{cid}/history", headers=clinician_headers)
        assert hist_v3_resp.status_code == 200
        hist_v3 = hist_v3_resp.json()
        assert len(hist_v3) >= 2
        assert hist_v3[-1]["version_after"] == plan_v3["version"]
        assert "activity_guidance" in hist_v3[-1]["fields_modified"]


# =============================================================================
# TC-PLN-003: Citizen check-in (Priority: P1)
# =============================================================================

class TestTCPLN003CitizenCheckIn:
    """TC-PLN-003: Citizen check-in
    Priority: P1
    Steps:
      1. Open an active plan.
      2. Complete a check-in.
      3. Retrieve the plan and check-in history.
    Expected result:
      The check-in is associated with the correct citizen and plan.
      Frontend UI updated and database verified.
    """

    def test_step1_open_active_plan(self, citizen_headers):
        """Step 1: Open an active plan for the authenticated citizen."""
        cid = "citizen-ramesh-patel-01"
        resp = client.get(f"/api/v1/intervention/plan/{cid}", headers=citizen_headers)
        assert resp.status_code == 200
        plan = resp.json()
        assert plan["citizen_id"] == cid
        assert plan["id"] is not None
        assert len(plan["id"]) > 0

    def test_step2_complete_check_in(self, citizen_headers):
        """Step 2: Complete a citizen daily check-in."""
        cid = "citizen-ramesh-patel-01"

        # Clean checkins for deterministic test verification
        store.clear_checkins(cid)

        # Retrieve active plan ID
        plan = client.get(f"/api/v1/intervention/plan/{cid}", headers=citizen_headers).json()
        expected_plan_id = plan["id"]

        checkin_payload = {
            "feeling": "Energetic",
            "medication_adherence": True,
            "symptoms": ["None"],
            "notes": "Completed 20-min post-lunch walk. Feeling light and energetic.",
            "tasks_completed_count": 2,
        }

        resp = client.post(f"/api/v1/intervention/check-in/{cid}", json=checkin_payload, headers=citizen_headers)
        assert resp.status_code == 200, f"Check-in failed: {resp.text}"
        data = resp.json()

        assert data["id"] is not None
        assert data["citizen_id"] == cid
        assert data["plan_id"] == expected_plan_id, "Check-in must be associated with the active plan ID"
        assert data["feeling"] == "Energetic"
        assert data["medication_adherence"] is True
        assert data["notes"] == "Completed 20-min post-lunch walk. Feeling light and energetic."

    def test_step3_retrieve_plan_and_checkin_history(self, citizen_headers):
        """Step 3: Retrieve the plan and check-in history.
        Verifies correct association with citizen and plan.
        """
        cid = "citizen-ramesh-patel-01"

        # 3.1 Retrieve active plan
        plan_resp = client.get(f"/api/v1/intervention/plan/{cid}", headers=citizen_headers)
        assert plan_resp.status_code == 200
        active_plan = plan_resp.json()

        # 3.2 Retrieve check-in history
        hist_resp = client.get(f"/api/v1/intervention/check-in/{cid}", headers=citizen_headers)
        assert hist_resp.status_code == 200
        checkins = hist_resp.json()

        assert len(checkins) >= 1
        latest = checkins[-1]
        assert latest["citizen_id"] == cid
        assert latest["plan_id"] == active_plan["id"]

        # 3.3 Add a second check-in to verify chronology and retention
        second_payload = {
            "feeling": "Normal",
            "medication_adherence": True,
            "symptoms": ["Mild fatigue"],
            "notes": "Work shift busy, rested for 15 mins.",
            "tasks_completed_count": 1,
        }
        res2 = client.post(f"/api/v1/intervention/check-in/{cid}", json=second_payload, headers=citizen_headers)
        assert res2.status_code == 200

        # Retrieve again
        hist_resp2 = client.get(f"/api/v1/intervention/check-in/{cid}", headers=citizen_headers)
        assert hist_resp2.status_code == 200
        all_checks = hist_resp2.json()
        assert len(all_checks) == 2
        assert all_checks[0]["feeling"] == "Energetic"
        assert all_checks[1]["feeling"] == "Normal"
        assert all(c["citizen_id"] == cid for c in all_checks)
        assert all(c["plan_id"] == active_plan["id"] for c in all_checks)

    def test_database_verification(self):
        """Database verification: Direct inspection of persistent store records."""
        cid = "citizen-ramesh-patel-01"
        stored_checkins = store.get_checkins(cid)
        assert len(stored_checkins) >= 2, "Database must store check-ins"

        active_plan = store.get_care_plan(cid)
        assert active_plan is not None, "Care plan must exist in database"

        for entry in stored_checkins:
            entry_cid = entry.get("citizen_id") if isinstance(entry, dict) else entry.citizen_id
            entry_plan = entry.get("plan_id") if isinstance(entry, dict) else entry.plan_id
            assert entry_cid == cid, f"Database check-in belongs to wrong citizen: {entry_cid}"
            assert entry_plan == active_plan.id, f"Database check-in linked to wrong plan: {entry_plan}"

    def test_frontend_ui_verification(self):
        """Frontend verification: Verify apps/citizen-mobile/index.html UI components.
        Ensures the UI contains the daily check-in form, connects to /api/v1/intervention/check-in/,
        displays active plan association, and renders check-in history.
        """
        html_path = Path("apps/citizen-mobile/index.html")
        assert html_path.exists(), "Citizen mobile app index.html must exist"
        html_content = html_path.read_text(encoding="utf-8")

        # 1. Screen 14 daily check-in title & plan association
        assert "30-Second Daily Check-in" in html_content
        assert "Linked Active Prevention Plan" in html_content or "checkinPlanBanner" in html_content
        assert "checkinPlanTitle" in html_content

        # 2. Feeling and Medication options
        assert "How are you feeling today?" in html_content
        assert "btnFeelEnergetic" in html_content
        assert "btnFeelNormal" in html_content
        assert "btnFeelTired" in html_content
        assert "btnFeelUnwell" in html_content
        assert "Did you take your prescribed medicines today?" in html_content
        assert "btnMedYes" in html_content
        assert "btnMedNo" in html_content

        # 3. Check-in submission button and endpoint binding
        assert "submitDailyCheckin" in html_content
        assert "/api/v1/intervention/check-in/" in html_content

        # 4. Check-in history list rendering
        assert "checkinHistoryList" in html_content
        assert "renderCheckinHistoryItems" in html_content
        assert "loadActivePlanAndCheckins" in html_content
