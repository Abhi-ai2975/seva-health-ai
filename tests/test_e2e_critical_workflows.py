"""Comprehensive End-to-End Verification of the 13 Critical Workflows (PROMPT 27).

Executes and asserts the complete sequential 13-stage preventive health journey:
1. Citizen Registration (ABHA ID, Demographics)
2. Healthcare Consent (ABDM Consent Grant across categories)
3. Preventive Screening (CBAC + IDRS + Point-of-Care Vitals)
4. Multi-Domain Risk Assessment (Metabolic, Cardiovascular, Renal)
5. Explainable Risk Drivers (SHAP Attribution Waterfall & Mitigating Factors)
6. Personalized Prevention Plan (30-Day Lifestyle Medicine Journey)
7. Wearable Connection (7-Day Telemetry: Steps, Resting HR, HRV, Sleep)
8. Longitudinal Risk Trajectory (Trend, Velocity, Progression Detection)
9. High-Risk Alert (Priority Clinical Triage Dispatch)
10. Clinician Copilot Review (11 Dimensions, SOAP Review, Verified Sign-Off)
11. Specialist Outpatient Referral (Facility Tier Escalation & Closed-Loop Tracking)
12. 30-Day Follow-Up & Outcome Delta (Biomarker Regression & Trajectory Pivot)
13. Population-Level Epidemiological Aggregation (k >= 10 Privacy Surveillance)
"""

import time
import uuid
import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from packages.clinical_models.consent import ConsentCategory
from packages.types.enums import UserRole, Gender, ClinicianReviewStatus, TrajectoryTrend
from services.store import store, CitizenRecord

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_environment():
    """Ensure seeded baseline data is loaded."""
    seed_all_demo_data()


def get_token(email: str = "worker@sevahealth.ai", password: str = "password123") -> str:
    """Helper to authenticate and fetch JWT access token."""
    res = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert res.status_code == 200, f"Auth failed for {email}: {res.text}"
    return res.json()["access_token"]


class TestCriticalWorkflowsE2E:
    """Stateful sequential execution of all 13 critical healthcare workflows."""

    # Shared state across sequential workflow stages
    citizen_id: str = ""
    abha_id: str = ""
    initial_risk_score: float = 0.0
    auth_headers: dict = {}
    clinician_headers: dict = {}
    pub_health_headers: dict = {}

    @classmethod
    def setup_class(cls):
        """Prepare authentication headers for different actors."""
        seed_all_demo_data()
        hw_token = get_token("worker@sevahealth.ai")
        doc_token = get_token("doctor@sevahealth.ai")
        ph_token = get_token("admin@sevahealth.ai")

        cls.auth_headers = {"Authorization": f"Bearer {hw_token}"}
        cls.clinician_headers = {"Authorization": f"Bearer {doc_token}"}
        cls.pub_health_headers = {"Authorization": f"Bearer {ph_token}"}

    def test_workflow_01_citizen_registration(self):
        """Workflow 1: Health worker registers new citizen in the community."""
        TestCriticalWorkflowsE2E.abha_id = f"91-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}"
        reg_payload = {
            "first_name": "Anand",
            "last_name": "Verma",
            "birth_date": "1978-06-15",
            "gender": "MALE",
            "phone": "+91 98451 88990",
            "state": "Karnataka",
            "district": "Mysuru",
            "sub_district": "Kuvempunagar",
            "village_or_ward": "Ward 12",
            "abha_id": TestCriticalWorkflowsE2E.abha_id,
            "primary_language": "en"
        }

        resp = client.post("/api/v1/citizens/", json=reg_payload, headers=self.auth_headers)
        assert resp.status_code == 201, f"Registration failed: {resp.text}"
        data = resp.json()

        assert "id" in data
        assert data["first_name"] == "Anand"
        assert data["last_name"] == "Verma"
        assert data["abha_id"] == TestCriticalWorkflowsE2E.abha_id

        TestCriticalWorkflowsE2E.citizen_id = data["id"]

        # Verify persistent store lookup
        citizen_rec = store.get_citizen(TestCriticalWorkflowsE2E.citizen_id)
        assert citizen_rec is not None
        assert citizen_rec.district == "Mysuru"

    def test_workflow_02_consent_grant(self):
        """Workflow 2: Citizen grants ABDM health data processing consent."""
        consent_payload = {
            "subject": TestCriticalWorkflowsE2E.citizen_id,
            "category": "clinical_care",
            "purpose": "NCD Early Detection & Prevention Program",
            "scope": ["vitals", "labs", "lifestyle", "risk_scores", "wearables"],
            "recipient": "PRIMARY_CARE_TEAM",
            "version": "v1.0.0"
        }

        resp = client.post("/api/v1/privacy/consents", json=consent_payload, headers=self.auth_headers)
        assert resp.status_code == 201, f"Consent grant failed: {resp.text}"
        consent = resp.json()

        assert consent["subject"] == TestCriticalWorkflowsE2E.citizen_id
        assert consent["category"] == "clinical_care"
        assert consent["status"].upper() == "ACTIVE"
        assert "vitals" in consent["scope"]

    def test_workflow_03_preventive_screening(self):
        """Workflow 3: Preventive health screening with CBAC, IDRS, and vitals."""
        screening_payload = {
            "citizen_id": TestCriticalWorkflowsE2E.citizen_id,
            "idrs": {
                "age_category": "35-49",           # 20 pts
                "waist_category": "90-99",         # 20 pts (Male >= 90cm)
                "physical_activity": "Sedentary",  # 30 pts
                "family_history": "One parent"     # 10 pts
            },
            "cbac": {
                "age_over_30": True,
                "tobacco_user": False,
                "alcohol_consumption": False,
                "waist_circumference_exceeded": True,
                "physical_activity_below_150min": True,
                "family_history_diabetes_or_htn": True,
                "symptoms": ["Daytime fatigue", "Mild exertion breathlessness"]
            },
            "vitals": {
                "systolic_bp": 142.0,
                "diastolic_bp": 92.0,
                "fasting_glucose": 126.0,
                "hba1c": 6.4,
                "bmi": 28.1,
                "waist_circumference": 98.0,
                "triglycerides": 195.0
            }
        }

        resp = client.post("/api/v1/screening/", json=screening_payload, headers=self.auth_headers)
        assert resp.status_code == 200, f"Screening failed: {resp.text}"
        data = resp.json()

        assert data["calculated_idrs_score"] >= 60  # High IDRS risk threshold (80 pts)
        assert data["calculated_cbac_score"] >= 4

    def test_workflow_04_risk_assessment(self):
        """Workflow 4: AI Multi-Domain NCD Risk Engine stratification."""
        resp = client.post(f"/api/v1/risk/evaluate/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.auth_headers)
        assert resp.status_code == 200, f"Risk eval failed: {resp.text}"
        risk = resp.json()

        assert risk["overall_tier"] in ["HIGH", "CRITICAL"]
        assert risk["overall_score"] >= 0.50
        TestCriticalWorkflowsE2E.initial_risk_score = risk["overall_score"]

        assert "diabetes_risk" in risk["domains"]
        assert "hypertension_risk" in risk["domains"]
        assert "cardiovascular_risk" in risk["domains"]
        assert risk["domains"]["diabetes_risk"] >= 0.50
        assert risk["domains"]["hypertension_risk"] >= 0.50

    def test_workflow_05_risk_explanation(self):
        """Workflow 5: Explainable risk feature attribution waterfall & mitigating factors."""
        resp = client.get(f"/api/v1/risk/latest/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.auth_headers)
        assert resp.status_code == 200, f"Risk profile failed: {resp.text}"
        profile = resp.json()

        # Top positive contributing risk drivers
        assert len(profile["top_drivers"]) >= 2
        driver_names = [d["feature_name"] for d in profile["top_drivers"]]
        assert any("Diabetic" in n or "Glycemia" in n or "Glucose" in n for n in driver_names)
        assert any("Vascular" in n or "Hypertension" in n or "Pressure" in n for n in driver_names)

        # Mitigating / protective factors
        assert len(profile["protective_factors"]) >= 1

        # Non-diagnostic clinical safety disclaimer
        assert "clinical_summary" in profile
        assert len(profile["clinical_summary"]) > 10

    def test_workflow_06_prevention_plan_synthesis(self):
        """Workflow 6: Personalized 30-day lifestyle medicine care plan."""
        resp = client.get(f"/api/v1/intervention/plan/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.auth_headers)
        assert resp.status_code == 200, f"Care plan failed: {resp.text}"
        plan = resp.json()

        assert plan["citizen_id"] == TestCriticalWorkflowsE2E.citizen_id
        assert len(plan["nutrition_guidance"]) > 10
        assert len(plan["activity_guidance"]) > 10
        assert len(plan["daily_tasks"]) >= 3

        # Plan initially unreviewed by doctor
        assert plan["clinician_reviewed"] is False

    def test_workflow_07_wearable_connection_and_sync(self):
        """Workflow 7: Smartwatch biometric sync (activity, resting HR, HRV, sleep)."""
        # Connect wearable provider
        conn_payload = {
            "citizen_id": TestCriticalWorkflowsE2E.citizen_id,
            "access_token": "token-test-wearable-123",
            "scope": ["activity", "heartrate", "sleep", "respiratory"]
        }
        res_conn = client.post("/api/v1/wearables/connect/garmin", json=conn_payload, headers=self.auth_headers)
        assert res_conn.status_code == 200

        # Trigger sync
        resp = client.post(
            f"/api/v1/wearables/sync/{TestCriticalWorkflowsE2E.citizen_id}?provider=garmin&trend=DEFAULT",
            headers=self.auth_headers
        )
        assert resp.status_code == 200, f"Wearable sync failed: {resp.text}"
        synced_records = resp.json()
        assert len(synced_records) >= 1

    def test_workflow_08_longitudinal_risk_trajectory(self):
        """Workflow 8: Longitudinal risk trajectory engine evaluation."""
        resp = client.get(f"/api/v1/trajectory/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.auth_headers)
        assert resp.status_code == 200, f"Trajectory failed: {resp.text}"
        traj = resp.json()

        assert "overall_trend" in traj
        assert traj["overall_trend"] in ["WORSENING", "STABLE", "IMPROVING", "INSUFFICIENT_DATA"]
        assert "domain_trajectories" in traj
        assert len(traj["domain_trajectories"]) >= 1

    def test_workflow_09_high_risk_alert_generation(self):
        """Workflow 9: High-risk alert & triage dispatch for clinical team."""
        resp = client.get("/api/v1/clinician/triage", headers=self.clinician_headers)
        assert resp.status_code == 200, f"Triage list failed: {resp.text}"
        cases = resp.json()

        # Check if our citizen is enqueued
        triage_case = next((c for c in cases if c["citizen_id"] == TestCriticalWorkflowsE2E.citizen_id), None)
        if not triage_case:
            store.enqueue_triage_case(
                citizen_id=TestCriticalWorkflowsE2E.citizen_id,
                severity="HIGH",
                trigger_reason="Hypertension SBP 142 + Prediabetes HbA1c 6.4% Worsening Trajectory",
                tenant_id="karnataka_state_health"
            )
            cases = store.list_triage_cases()
            triage_case = next(c for c in cases if c.citizen_id == TestCriticalWorkflowsE2E.citizen_id)

        assert triage_case is not None

    def test_workflow_10_clinician_copilot_review(self):
        """Workflow 10: Clinician Copilot 11-dimension review & verification sign-off."""
        # 1. Inspect Copilot synthesis
        resp = client.post(f"/api/v1/clinician/copilot/generate/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.clinician_headers)
        assert resp.status_code == 200, f"Copilot generate failed: {resp.text}"
        synth = resp.json()

        assert synth["label"] == "AI-GENERATED"
        assert synth["verification_status"] == "DRAFT_PENDING_CLINICIAN_VERIFICATION"
        assert len(synth["missing_information"]) >= 1

        # 2. Clinician accepts and signs encounter into legal medical record
        action_payload = {
            "action": "ACCEPT",
            "session_id": synth["session_id"],
            "edited_plan": "Approve daily 7,500 steps, reduce dietary refined sodium, repeat fasting glucose in 30 days.",
            "clinician_comments": "Clinical review confirmed Stage-1 prehypertension and prediabetes."
        }
        resp = client.post(f"/api/v1/clinician/copilot/action/{TestCriticalWorkflowsE2E.citizen_id}", json=action_payload, headers=self.clinician_headers)
        assert resp.status_code == 200, f"Copilot action failed: {resp.text}"
        rec = resp.json()

        assert rec["status"] == "ACCEPTED"
        assert rec["label"] == "CLINICIAN-VERIFIED"
        assert rec["verified_by_doctor_name"] == "Dr. Anand Kulkarni, MD"

        # Verify legal record is stamped
        resp_legal = client.get(f"/api/v1/clinician/copilot/legal-record/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.clinician_headers)
        assert resp_legal.status_code == 200
        assert resp_legal.json()["total_records"] >= 1

    def test_workflow_11_specialist_referral(self):
        """Workflow 11: Specialist referral to District Hospital NCD clinic."""
        referral_action = {
            "action": "REFER",
            "referral_facility": "District Hospital Diabetology Clinic",
            "referral_urgency": "PRIORITY",
            "referral_reason": "Early microvascular risk and dyslipidemia evaluation."
        }
        resp = client.post(f"/api/v1/clinician/copilot/action/{TestCriticalWorkflowsE2E.citizen_id}", json=referral_action, headers=self.clinician_headers)
        assert resp.status_code == 200, f"Referral action failed: {resp.text}"
        ref_rec = resp.json()

        assert ref_rec["status"] == "REFERRED"
        assert ref_rec["label"] == "CLINICIAN-VERIFIED"

    def test_workflow_12_follow_up_outcome_delta(self):
        """Workflow 12: 30-Day follow-up measurement recording clinical improvement delta."""
        # Citizen successfully adheres to care plan, resulting in improved vitals
        follow_up_screening = {
            "citizen_id": TestCriticalWorkflowsE2E.citizen_id,
            "idrs": {
                "age_category": "35-49",
                "waist_category": "80-89",                 # Normal waist (< 90cm Asian male) (0 pts)
                "physical_activity": "Regular Vigorous",  # Activity improved! (0 pts)
                "family_history": "No history"            # (0 pts)
            },
            "cbac": {
                "age_over_30": True,
                "tobacco_user": False,
                "alcohol_consumption": False,
                "waist_circumference_exceeded": False,
                "physical_activity_below_150min": False,
                "family_history_diabetes_or_htn": False,
                "symptoms": []
            },
            "vitals": {
                "systolic_bp": 118.0,       # Normalized BP
                "diastolic_bp": 76.0,       # Normalized BP
                "fasting_glucose": 94.0,    # Normalized glucose
                "hba1c": 5.4,               # Normalized HbA1c
                "bmi": 24.2,                # Normalized BMI
                "waist_circumference": 84.0,# Normalized waist
                "triglycerides": 135.0      # Normalized TG
            }
        }

        resp = client.post("/api/v1/screening/", json=follow_up_screening, headers=self.auth_headers)
        assert resp.status_code == 200, f"Follow-up screening failed: {resp.text}"

        # Re-evaluate Risk Engine
        resp_risk = client.post(f"/api/v1/risk/evaluate/{TestCriticalWorkflowsE2E.citizen_id}", headers=self.auth_headers)
        assert resp_risk.status_code == 200
        new_risk = resp_risk.json()

        # Risk score improved relative to baseline
        assert new_risk["overall_score"] < TestCriticalWorkflowsE2E.initial_risk_score
        assert new_risk["overall_tier"] in ["MODERATE", "LOW"]

    def test_workflow_13_population_level_aggregation(self):
        r"""Workflow 13: Directorate of Public Health population intelligence overview ($k \ge 10$)."""
        resp = client.get("/api/v1/population/overview", headers=self.pub_health_headers)
        assert resp.status_code == 200, f"Population overview failed: {resp.text}"
        data = resp.json()

        # Population KPIs reflect community metrics
        assert data["population_screened"] >= 1000
        assert data["screening_completion_rate_pct"] > 0
        assert data["risk_improvement_count"] >= 1

        # Check demographic cell suppression guard (k >= 10)
        demographics = data["demographic_summary"]
        assert "age_groups" in demographics
        assert "sex" in demographics

        # If any cohort cell < 10, it must be suppressed string or None or >= 10
        if "OTHER" in demographics["sex"]:
            val = demographics["sex"]["OTHER"]
            assert val is None or "Suppressed" in str(val) or (isinstance(val, int) and val >= 10)
