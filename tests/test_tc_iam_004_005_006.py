"""Test Suite for TC-IAM-004, TC-IAM-005, and TC-IAM-006:
Advanced Healthcare Access Control, Care Context, and Least Privilege Enforcement.

Specifications Covered:
- TC-IAM-004: Health-worker program isolation (Priority: P0)
  Steps: Create two screening programs. Assign a health worker only to Program A.
         Attempt to retrieve citizens belonging exclusively to Program B.
  Expected: The worker cannot access Program B's restricted records.
- TC-IAM-005: Clinician authorization (Priority: P0)
  Steps: Authenticate as a clinician. Request a patient outside the clinician's authorized care context.
         Attempt to create a clinical review or referral for that patient.
  Expected: Unauthorized access and actions are denied.
- TC-IAM-006: Administrator least privilege (Priority: P1)
  Steps: Authenticate as a system administrator. Open operational administration screens.
         Attempt to retrieve unrestricted clinical records without an approved authorization.
  Expected: Operational administration does not automatically grant unrestricted clinical-record access.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, UserRecord, CitizenRecord, ConsentDirective
from packages.types.enums import UserRole, Gender, AuditAction, ClinicianReviewStatus, TriageUrgency
from packages.auth.jwt import get_password_hash
from packages.observability.audit import audit_logger
from services.health_worker.models import CommunityProgram
from packages.clinical_models.triage import ClinicalTriageCase, SOAPReport
from scripts.seed_data import seed_all_demo_data

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_iam_environment():
    """Ensure baseline synthetic users and demo data are initialized."""
    seed_all_demo_data()


def get_auth_token(email: str, password: str = "password123") -> str:
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.json()["access_token"]


class TestTCIAM004HealthWorkerProgramIsolation:
    """TC-IAM-004: Health-worker program isolation (Priority: P0)"""

    @pytest.fixture(autouse=True)
    def setup_programs_and_citizens(self):
        # 1. Create two screening programs
        self.prog_a = CommunityProgram(
            id="prog-test-mysuru-a",
            name="Program A - Mysuru Urban Outreach",
            state="Karnataka",
            district="Mysuru",
            communities=["Ward 12", "Ward 14"],
            description="Urban NCD screening program in Mysuru Ward 12",
            target_domains=["Hypertension", "Diabetes"],
            active_workflows=["mvp_comprehensive"],
        )
        self.prog_b = CommunityProgram(
            id="prog-test-mandya-b",
            name="Program B - Mandya Rural Outreach",
            state="Karnataka",
            district="Mandya",
            communities=["Malavalli Village", "Koppa Village"],
            description="Rural agricultural community screening in Mandya",
            target_domains=["Hypertension", "Diabetes", "Renal Health"],
            active_workflows=["mvp_comprehensive"],
        )
        store.add_program(self.prog_a)
        store.add_program(self.prog_b)

        # 2. Worker assigned ONLY to Program A
        self.worker_a_email = "workera@sevahealth.ai"
        self.worker_a_id = "worker-a-user-id"
        store.add_user(UserRecord(
            id=self.worker_a_id,
            tenant_id="karnataka_state_health",
            email=self.worker_a_email,
            hashed_password=get_password_hash("password123"),
            role=UserRole.HEALTH_WORKER,
            full_name="Sunita ASHA (Program A)",
            assigned_jurisdiction="Ward 12",
            assigned_programs=[self.prog_a.id],
        ))

        # 3. Citizen A belongs to Program A
        self.citizen_a_id = "cit-prog-a-001"
        store.add_citizen(CitizenRecord(
            id=self.citizen_a_id,
            tenant_id="karnataka_state_health",
            user_id="user-cit-a",
            abha_id="91-1111-2222-3333",
            first_name="Ramesh",
            last_name="Patel",
            birth_date="1980-01-01",
            gender=Gender.MALE,
            phone="9845012345",
            state="Karnataka",
            district="Mysuru",
            sub_district="Mysuru Urban",
            village_or_ward="Ward 12",
            program_id=self.prog_a.id,
        ))

        # 4. Citizen B belongs exclusively to Program B
        self.citizen_b_id = "cit-prog-b-002"
        store.add_citizen(CitizenRecord(
            id=self.citizen_b_id,
            tenant_id="karnataka_state_health",
            user_id="user-cit-b",
            abha_id="91-4444-5555-6666",
            first_name="Lakshmi",
            last_name="Gowda",
            birth_date="1975-05-15",
            gender=Gender.FEMALE,
            phone="9845098765",
            state="Karnataka",
            district="Mandya",
            sub_district="Malavalli",
            village_or_ward="Malavalli Village",
            program_id=self.prog_b.id,
        ))

    def test_step1_worker_a_can_access_program_a_citizen(self):
        """Worker A assigned to Program A can access Citizen A."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get(f"/api/v1/citizens/{self.citizen_a_id}", headers=headers)
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["id"] == self.citizen_a_id
        assert data["first_name"] == "Ramesh"

    def test_step2_worker_a_cannot_access_program_b_citizen_profile(self):
        """Worker A assigned to Program A cannot access Citizen B (Program B)."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get(f"/api/v1/citizens/{self.citizen_b_id}", headers=headers)
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
        assert "Access denied" in res.json()["detail"]

    def test_step3_worker_a_cannot_access_program_b_observations_and_risks(self):
        """Worker A cannot access observations or risks of Program B citizen."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        res_obs = client.get(f"/api/v1/citizens/{self.citizen_b_id}/observations", headers=headers)
        assert res_obs.status_code == 403, f"Expected 403 for observations, got {res_obs.status_code}"

        res_risk = client.get(f"/api/v1/citizens/{self.citizen_b_id}/risk", headers=headers)
        assert res_risk.status_code == 403, f"Expected 403 for risk, got {res_risk.status_code}"

    def test_step4_worker_a_cannot_trigger_field_risk_or_referral_for_program_b(self):
        """Worker A cannot generate field risk assessment, referral, or followup for Citizen B."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        # Attempt risk assessment recommendation
        res_risk = client.post(f"/api/v1/health-worker/risk-assessment/{self.citizen_b_id}", headers=headers)
        assert res_risk.status_code == 403, f"Expected 403, got {res_risk.status_code}: {res_risk.text}"

        # Attempt referral slip creation
        res_ref = client.post(
            "/api/v1/health-worker/refer",
            headers=headers,
            json={
                "citizen_id": self.citizen_b_id,
                "facility_name": "Mysuru District Hospital",
                "facility_type": "DISTRICT_HOSPITAL",
                "urgency": "PRIORITY",
                "reason": "Unauthorized referral attempt across programs",
            }
        )
        assert res_ref.status_code == 403, f"Expected 403 for referral, got {res_ref.status_code}"

        # Attempt followup creation
        res_fol = client.post(
            "/api/v1/health-worker/followup",
            headers=headers,
            json={
                "citizen_id": self.citizen_b_id,
                "scheduled_date": "2026-10-20",
                "purpose": "Home visit checkup",
            }
        )
        assert res_fol.status_code == 403, f"Expected 403 for followup, got {res_fol.status_code}"

    def test_step5_worker_a_citizen_list_excludes_program_b(self):
        """Worker A querying /citizens/ only receives Program A records."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        res = client.get("/api/v1/citizens/", headers=headers)
        assert res.status_code == 200
        cit_ids = [c["id"] for c in res.json()]
        assert self.citizen_a_id in cit_ids
        assert self.citizen_b_id not in cit_ids

    def test_step6_program_isolation_denials_are_audited(self):
        """Denials due to program isolation are recorded in audit logs with ACCESS_DENIED."""
        token = get_auth_token(self.worker_a_email)
        headers = {"Authorization": f"Bearer {token}"}

        initial_denied = len([
            log for log in audit_logger.list_records(limit=500)
            if log.action == AuditAction.ACCESS_DENIED and log.actor_id == self.worker_a_id
        ])

        res = client.get(f"/api/v1/citizens/{self.citizen_b_id}", headers=headers)
        assert res.status_code == 403

        new_denied = [
            log for log in audit_logger.list_records(limit=500)
            if log.action == AuditAction.ACCESS_DENIED and log.actor_id == self.worker_a_id
        ]
        assert len(new_denied) > initial_denied
        latest_entry = new_denied[-1]
        assert latest_entry.resource_id == self.citizen_b_id
        assert latest_entry.actor_role == UserRole.HEALTH_WORKER


class TestTCIAM005ClinicianAuthorization:
    """TC-IAM-005: Clinician authorization (Priority: P0)"""

    @pytest.fixture(autouse=True)
    def setup_clinician_and_patients(self):
        self.clinician_email = "doctor@sevahealth.ai"
        self.clinician_token = get_auth_token(self.clinician_email)
        self.headers = {"Authorization": f"Bearer {self.clinician_token}"}

        # Authorized patient (Ramesh Patel - assigned panel)
        self.assigned_patient_id = "citizen-ramesh-patel-01"

        # Unauthorized patient outside care context (Priya Sharma - unassigned, no triage, no consent)
        self.unassigned_patient_id = "citizen-priya-sharma-04"

    def test_step1_clinician_can_access_assigned_patient(self):
        """Clinician can access patient in their authorized panel."""
        res = client.get(f"/api/v1/citizens/{self.assigned_patient_id}", headers=self.headers)
        assert res.status_code == 200
        assert res.json()["id"] == self.assigned_patient_id

    def test_step2_clinician_denied_access_to_unassigned_patient_profile(self):
        """Clinician is denied access to patient outside authorized care context."""
        res = client.get(f"/api/v1/citizens/{self.unassigned_patient_id}", headers=self.headers)
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
        assert "Access denied" in res.json()["detail"]
        assert "care context" in res.json()["detail"]

    def test_step3_clinician_denied_unassigned_patient_observations_and_risks(self):
        """Clinician cannot view observations or risks of unassigned patient."""
        res_obs = client.get(f"/api/v1/citizens/{self.unassigned_patient_id}/observations", headers=self.headers)
        assert res_obs.status_code == 403, f"Expected 403 for obs, got {res_obs.status_code}"

        res_risk = client.get(f"/api/v1/citizens/{self.unassigned_patient_id}/risk", headers=self.headers)
        assert res_risk.status_code == 403, f"Expected 403 for risk, got {res_risk.status_code}"

    def test_step4_clinician_denied_copilot_patient_summary_and_synthesis(self):
        """Clinician cannot assemble copilot patient summary or synthesis for unassigned patient."""
        res_summary = client.get(f"/api/v1/clinician/copilot/patient-summary/{self.unassigned_patient_id}", headers=self.headers)
        assert res_summary.status_code == 403, f"Expected 403 for summary, got {res_summary.status_code}"

        res_synth = client.post(f"/api/v1/clinician/copilot/generate/{self.unassigned_patient_id}", headers=self.headers)
        assert res_synth.status_code == 403, f"Expected 403 for synthesis, got {res_synth.status_code}"

    def test_step5_clinician_denied_creating_clinical_action_or_referral(self):
        """Clinician cannot create clinical review, referral, or verify copilot for unassigned patient."""
        res_action = client.post(
            f"/api/v1/clinician/copilot/action/{self.unassigned_patient_id}",
            headers=self.headers,
            json={
                "action": "REFER",
                "referral_facility": "NIMHANS Specialist Clinic",
                "referral_specialty": "Neurology",
                "clinician_notes": "Attempted referral without care context",
            }
        )
        assert res_action.status_code == 403, f"Expected 403 for copilot action, got {res_action.status_code}: {res_action.text}"

    def test_step6_clinician_denied_reviewing_unauthorized_triage_case(self):
        """Attempting to submit clinical review for a patient outside care context is denied."""
        # Create a synthetic triage case for unassigned patient
        triage_id = f"triage-unauth-{uuid.uuid4().hex[:6]}"
        unauth_triage = ClinicalTriageCase(
            id=triage_id,
            tenant_id="karnataka_state_health",
            citizen_id=self.unassigned_patient_id,
            citizen_name="Priya Sharma",
            risk_assessment_id="risk-priya-01",
            urgency=TriageUrgency.ROUTINE,
            escalation_reason="Test case outside doctor care context",
            assigned_clinician_id="doctor-other-99",  # Assigned to another clinician!
            soap_note=SOAPReport(
                subjective="Routine checkup",
                objective="Vitals normal",
                assessment="Low risk",
                plan="Maintain habits",
            )
        )
        store.add_triage_case(unauth_triage)

        res_rev = client.post(
            f"/api/v1/clinician/review/{triage_id}",
            headers=self.headers,
            json={
                "status": "APPROVED",
                "review_notes": "Unauthorized doctor attempt to sign off case",
            }
        )
        assert res_rev.status_code == 403, f"Expected 403, got {res_rev.status_code}: {res_rev.text}"


class TestTCIAM006AdministratorLeastPrivilege:
    """TC-IAM-006: Administrator least privilege (Priority: P1)"""

    @pytest.fixture(autouse=True)
    def setup_admin_tokens(self):
        self.sysadmin_token = get_auth_token("sysadmin@sevahealth.ai")
        self.sysadmin_headers = {"Authorization": f"Bearer {self.sysadmin_token}"}

        self.public_admin_token = get_auth_token("admin@sevahealth.ai")
        self.public_admin_headers = {"Authorization": f"Bearer {self.public_admin_token}"}

        self.patient_id = "citizen-ramesh-patel-01"

    def test_step1_system_admin_can_access_operational_screens(self):
        """System administrator can access operational management and observability endpoints."""
        # Health check
        res_health = client.get("/health")
        assert res_health.status_code == 200

        # Prometheus metrics
        res_metrics = client.get("/metrics")
        assert res_metrics.status_code == 200

        # Current profile
        res_me = client.get("/api/v1/auth/me", headers=self.sysadmin_headers)
        assert res_me.status_code == 200
        assert res_me.json()["role"] == UserRole.SYSTEM_ADMIN.value

        # Observability dashboards & portal
        res_portal = client.get("/observability")
        assert res_portal.status_code == 200

        res_obs = client.get("/api/v1/observability/dashboards/system")
        assert res_obs.status_code == 200

        # Jobs queue operational endpoint
        res_jobs = client.get("/api/v1/jobs/", headers=self.sysadmin_headers)
        assert res_jobs.status_code == 200

        res_q_metrics = client.get("/api/v1/jobs/metrics/document_ocr", headers=self.sysadmin_headers)
        assert res_q_metrics.status_code == 200

    def test_step2_system_admin_denied_unrestricted_clinical_records(self):
        """System administrator cannot retrieve individual clinical health records without approved authorization."""
        # Attempt to access citizen profile
        res_cit = client.get(f"/api/v1/citizens/{self.patient_id}", headers=self.sysadmin_headers)
        assert res_cit.status_code == 403, f"Expected 403 for profile, got {res_cit.status_code}: {res_cit.text}"
        assert "Operational administration does not automatically grant unrestricted clinical-record access" in res_cit.json()["detail"]

        # Attempt to access observations
        res_obs = client.get(f"/api/v1/citizens/{self.patient_id}/observations", headers=self.sysadmin_headers)
        assert res_obs.status_code == 403, f"Expected 403 for observations, got {res_obs.status_code}"

        # Attempt to access risk assessment
        res_risk = client.get(f"/api/v1/citizens/{self.patient_id}/risk", headers=self.sysadmin_headers)
        assert res_risk.status_code == 403, f"Expected 403 for risk, got {res_risk.status_code}"

        # Attempt to access legal clinical record
        res_legal = client.get(f"/api/v1/clinician/copilot/legal-record/{self.patient_id}", headers=self.sysadmin_headers)
        assert res_legal.status_code == 403, f"Expected 403 for legal records, got {res_legal.status_code}"

    def test_step3_public_health_admin_least_privilege(self):
        """Public health admin can access aggregated intelligence but is strictly blocked from identifiable records."""
        # Aggregated intelligence succeeds
        res_pop = client.get("/api/v1/population/overview", headers=self.public_admin_headers)
        assert res_pop.status_code == 200
        assert "population_screened" in res_pop.json()
        assert "CONFIDENTIAL & DE-IDENTIFIED" in res_pop.json()["privacy_notice"]

        # Identifiable records blocked
        res_cit = client.get(f"/api/v1/citizens/{self.patient_id}", headers=self.public_admin_headers)
        assert res_cit.status_code == 403
        assert "Public Health Administrators are restricted to aggregated population intelligence" in res_cit.json()["detail"]

        res_list = client.get("/api/v1/citizens/", headers=self.public_admin_headers)
        assert res_list.status_code == 403

    def test_step4_admin_with_approved_authorization_directive_is_permitted(self):
        """When an approved administrative audit or emergency breakglass directive exists, access is granted."""
        # Grant an approved administrative audit directive
        directive_id = f"dir-audit-{uuid.uuid4().hex[:6]}"
        auth_directive = ConsentDirective(
            id=directive_id,
            citizen_id=self.patient_id,
            grantee_id="sysadmin@sevahealth.ai",
            grantee_role=UserRole.SYSTEM_ADMIN,
            purpose="ADMIN_AUDIT",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        store.add_consent(auth_directive)

        # Now system admin can access with approved authorization
        res_cit = client.get(f"/api/v1/citizens/{self.patient_id}", headers=self.sysadmin_headers)
        assert res_cit.status_code == 200
        assert res_cit.json()["id"] == self.patient_id

        # Clean up directive
        store.revoke_consent(self.patient_id, directive_id)

        # Access is denied again once directive revoked
        res_after = client.get(f"/api/v1/citizens/{self.patient_id}", headers=self.sysadmin_headers)
        assert res_after.status_code == 403
