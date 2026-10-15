"""Test Suite for TC-CLN-001, TC-CLN-002, and TC-CLN-003:
Clinician Review, Referrals, and Notifications
- TC-CLN-001: Review queue creation (Priority: P0)
- TC-CLN-002: Review acknowledgement & clinical decision (Priority: P0)
- TC-CLN-003: Referral lifecycle & state machine enforcement (Priority: P0)
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, ClinicianReviewStatus, TriageUrgency, ReferralStatus
from packages.auth.jwt import create_access_token
from packages.clinical_models.referral import PERMITTED_REFERRAL_TRANSITIONS

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"
CITIZEN_ID = "citizen-cln-test-01"
CLINICIAN_ID = "clinician-dr-kulkarni-01"


@pytest.fixture
def clinician_auth_headers():
    token = create_access_token(
        subject=CLINICIAN_ID,
        tenant_id=TENANT_ID,
        role=UserRole.CLINICIAN,
        scopes=["clinical:read", "clinical:write", "risk:read"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def unauthorized_worker_headers():
    token = create_access_token(
        subject="worker-asha-01",
        tenant_id=TENANT_ID,
        role=UserRole.HEALTH_WORKER,
        scopes=["clinical:read"],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_citizen_and_clear_store():
    """Ensure a synthetic citizen exists in store with assignment and consent, and clear triage/referrals."""
    c = CitizenRecord(
        id=CITIZEN_ID,
        tenant_id=TENANT_ID,
        user_id="user-cln-01",
        abha_id="91-5555-6666-7777",
        first_name="Sunita",
        last_name="ReviewSharma",
        birth_date="1978-08-15",
        gender=Gender.FEMALE,
        phone="+919876543288",
        state="Karnataka",
        district="Bengaluru Urban",
        sub_district="Bengaluru South",
        village_or_ward="Ward 145",
    )
    store.citizens[CITIZEN_ID] = c

    clinician = UserRecord(
        id=CLINICIAN_ID,
        tenant_id=TENANT_ID,
        email="dr.kulkarni@health.karnataka.gov.in",
        hashed_password="mock_hashed_password",
        role=UserRole.CLINICIAN,
        full_name="Dr. Sunita Kulkarni",
        assigned_patients=[CITIZEN_ID],
    )
    store.add_user(clinician)

    consent = ConsentDirective(
        id="consent-cln-01",
        citizen_id=CITIZEN_ID,
        grantee_id=CLINICIAN_ID,
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
        scope=["CLINICAL_REVIEW", "REFERRAL", "OBSERVATIONS", "RISK_ASSESSMENT"],
    )
    store.add_consent(consent)

    store.clear_triage_cases(CITIZEN_ID)
    store.clear_referrals(CITIZEN_ID)


# ==============================================================================
# TC-CLN-001: Review Queue Creation (Priority: P0)
# ==============================================================================

class TestTCCLN001ReviewQueueCreation:
    """TC-CLN-001: Review queue creation
    Steps:
      1. Seed a case meeting an approved review rule (e.g. SEVERE_HYPERTENSION).
      2. Submit the applicable assessment.
      3. Retrieve the clinician queue.
    Expected:
      The case appears with correct reason, priority, timestamp and status (PENDING).
    """

    def test_seed_severe_hypertension_enters_queue_with_emergent_priority(self, clinician_auth_headers):
        # Step 1 & 2: Seed case meeting approved review rule: SEVERE_HYPERTENSION
        payload = {
            "citizen_id": CITIZEN_ID,
            "rule_type": "SEVERE_HYPERTENSION",
            "custom_urgency": "EMERGENT",
            "custom_reason": "Severe Blood Pressure Elevation (164/102 mmHg) exceeding Stage 2 threshold",
        }
        resp = client.post(
            "/api/v1/clinician/triage/seed",
            json=payload,
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        case = resp.json()

        assert case["id"] is not None
        assert case["citizen_id"] == CITIZEN_ID
        assert case["urgency"] == "EMERGENT"
        assert "Severe Blood Pressure Elevation" in case["escalation_reason"]
        assert case["status"] == "PENDING"
        assert case["is_acknowledged"] is False
        assert case["created_at"] is not None
        assert case["soap_note"] is not None
        assert case["soap_note"]["key_observations"]["systolic_bp"] == 164

        # Step 3: Retrieve the clinician queue
        q_resp = client.get(
            "/api/v1/clinician/triage?status=PENDING",
            headers=clinician_auth_headers,
        )
        assert q_resp.status_code == 200, q_resp.text
        queue = q_resp.json()
        assert len(queue) >= 1

        # Locate the seeded case
        found = next((item for item in queue if item["id"] == case["id"]), None)
        assert found is not None, "Seeded case must appear in clinician triage queue"
        assert found["urgency"] == "EMERGENT"
        assert found["status"] == "PENDING"
        assert found["escalation_reason"] == payload["custom_reason"]
        assert found["created_at"] is not None

    def test_seed_critical_glycemic_rule_enters_queue_with_priority(self, clinician_auth_headers):
        payload = {
            "citizen_id": CITIZEN_ID,
            "rule_type": "CRITICAL_GLYCEMIC",
            "custom_urgency": "PRIORITY",
            "custom_reason": "Critical Glycemic Dysregulation (HbA1c 9.2%, Fasting Glucose 214 mg/dL)",
        }
        resp = client.post(
            "/api/v1/clinician/triage/seed",
            json=payload,
            headers=clinician_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        case = resp.json()
        assert case["urgency"] == "PRIORITY"
        assert case["status"] == "PENDING"
        assert case["soap_note"]["key_observations"]["hba1c"] == 9.2

        # Verify queue retrieval with citizen_id filter
        q_resp = client.get(
            f"/api/v1/clinician/triage?citizen_id={CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert q_resp.status_code == 200
        items = q_resp.json()
        matching = [item for item in items if item["id"] == case["id"]]
        assert len(matching) == 1
        assert matching[0]["escalation_reason"] == payload["custom_reason"]


# ==============================================================================
# TC-CLN-002: Review Acknowledgement & Decision (Priority: P0)
# ==============================================================================

class TestTCCLN002ReviewAcknowledgement:
    """TC-CLN-002: Review acknowledgement
    Steps:
      1. Open a review item as an authorized clinician.
      2. Record acknowledgement and a review decision.
      3. Reload the queue.
    Expected:
      The decision and status are persisted and auditable.
    """

    def test_review_item_acknowledgement_and_decision_lifecycle(self, clinician_auth_headers):
        # Seed an emergent case
        seed_resp = client.post(
            "/api/v1/clinician/triage/seed",
            json={
                "citizen_id": CITIZEN_ID,
                "rule_type": "SEVERE_HYPERTENSION",
            },
            headers=clinician_auth_headers,
        )
        assert seed_resp.status_code == 200
        case_id = seed_resp.json()["id"]

        # Step 1: Open the review item as an authorized clinician
        get_resp = client.get(
            f"/api/v1/clinician/triage/{case_id}",
            headers=clinician_auth_headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        case_opened = get_resp.json()
        assert case_opened["id"] == case_id
        assert case_opened["is_acknowledged"] is False
        assert case_opened["status"] == "PENDING"

        # Step 2a: Record acknowledgement
        ack_payload = {"notes": "Physician acknowledges urgent Stage 2 case; initiating tele-consultation."}
        ack_resp = client.post(
            f"/api/v1/clinician/triage/{case_id}/acknowledge",
            json=ack_payload,
            headers=clinician_auth_headers,
        )
        assert ack_resp.status_code == 200, ack_resp.text
        ack_data = ack_resp.json()
        assert ack_data["is_acknowledged"] is True
        assert ack_data["acknowledged_by"] == CLINICIAN_ID
        assert ack_data["acknowledged_at"] is not None
        assert ack_data["status"] == "ACKNOWLEDGED"
        assert len(ack_data["review_history"]) >= 1
        assert ack_data["review_history"][-1]["action"] == "ACKNOWLEDGED"

        # Step 2b: Record a clinical review decision
        decision_payload = {
            "status": "APPROVED",
            "decision": "APPROVED",
            "review_notes": "Anti-hypertensive titration initiated (Amlodipine 5mg to 10mg daily). Advised low-sodium diet and 48hr BP recheck.",
            "updated_care_plan_instructions": "Titrate Amlodipine to 10mg PO daily. Strict DASH diet.",
        }
        dec_resp = client.post(
            f"/api/v1/clinician/review/{case_id}",
            json=decision_payload,
            headers=clinician_auth_headers,
        )
        assert dec_resp.status_code == 200, dec_resp.text
        dec_data = dec_resp.json()
        assert dec_data["status"] == "APPROVED"
        assert dec_data["decision"] == "APPROVED"
        assert dec_data["review_notes"] == decision_payload["review_notes"]
        assert dec_data["reviewed_at"] is not None
        assert dec_data["assigned_clinician_id"] == CLINICIAN_ID
        assert len(dec_data["review_history"]) >= 2
        assert dec_data["review_history"][-1]["action"] == "DECISION_RECORDED"

        # Step 3: Reload the queue and verify persistence
        reload_resp = client.get(
            f"/api/v1/clinician/triage/{case_id}",
            headers=clinician_auth_headers,
        )
        assert reload_resp.status_code == 200
        reloaded = reload_resp.json()
        assert reloaded["status"] == "APPROVED"
        assert reloaded["decision"] == "APPROVED"
        assert reloaded["is_acknowledged"] is True
        assert reloaded["acknowledged_by"] == CLINICIAN_ID
        assert reloaded["review_notes"] == decision_payload["review_notes"]
        assert len(reloaded["review_history"]) == 2

        # Check queue filter behavior: item no longer in PENDING queue
        pending_q = client.get(
            "/api/v1/clinician/triage?status=PENDING",
            headers=clinician_auth_headers,
        ).json()
        assert not any(item["id"] == case_id for item in pending_q)

        # But item is in APPROVED queue
        approved_q = client.get(
            "/api/v1/clinician/triage?status=APPROVED",
            headers=clinician_auth_headers,
        ).json()
        assert any(item["id"] == case_id for item in approved_q)


# ==============================================================================
# TC-CLN-003: Referral Lifecycle & State Machine (Priority: P0)
# ==============================================================================

class TestTCCLN003ReferralLifecycle:
    """TC-CLN-003: Referral lifecycle
    Steps:
      1. Create a referral for a synthetic citizen.
      2. Assign the configured destination or responsible party.
      3. Update its status through the permitted workflow.
      4. Close the referral with an outcome.
    Expected:
      Each transition follows the configured state machine and preserves its history.
    """

    def test_referral_end_to_end_permitted_state_machine(self, clinician_auth_headers):
        # Step 1: Create a referral for synthetic citizen
        create_payload = {
            "citizen_id": CITIZEN_ID,
            "urgency": "URGENT",
            "reason": "Suspected acute coronary syndrome / hypertensive urgency",
            "provisional_diagnosis": "Stage 2 Hypertensive Crisis with symptomatic headache",
            "clinical_notes": "BP 164/102 mmHg; requires urgent secondary care assessment.",
        }
        create_resp = client.post(
            "/api/v1/clinician/referrals",
            json=create_payload,
            headers=clinician_auth_headers,
        )
        assert create_resp.status_code == 200, create_resp.text
        ref = create_resp.json()
        ref_id = ref["id"]
        assert ref["status"] == "PENDING"
        assert ref["referral_slip_id"].startswith("REF-")
        assert len(ref["history"]) == 1
        assert ref["history"][0]["to_status"] == "PENDING"

        # Step 2: Assign configured destination facility and responsible clinician
        assign_payload = {
            "assigned_facility_id": "FAC-BENGALURU-GENERAL-01",
            "assigned_facility_name": "Victoria Hospital Bengaluru",
            "assigned_department": "Cardiology OPD",
            "assigned_clinician_id": "dr-sharma-cardio-01",
            "assigned_clinician_name": "Dr. Ramesh Sharma, MD DM",
            "notes": "Assigned to Cardiology OPD for emergency echocardiogram and monitoring.",
        }
        assign_resp = client.post(
            f"/api/v1/clinician/referrals/{ref_id}/assign",
            json=assign_payload,
            headers=clinician_auth_headers,
        )
        assert assign_resp.status_code == 200, assign_resp.text
        assigned_ref = assign_resp.json()
        assert assigned_ref["status"] == "ASSIGNED"
        assert assigned_ref["assigned_facility_name"] == "Victoria Hospital Bengaluru"
        assert assigned_ref["assigned_clinician_name"] == "Dr. Ramesh Sharma, MD DM"
        assert len(assigned_ref["history"]) == 2
        assert assigned_ref["history"][1]["from_status"] == "PENDING"
        assert assigned_ref["history"][1]["to_status"] == "ASSIGNED"

        # Step 3: Update status through permitted workflow: ASSIGNED -> IN_PROGRESS
        transition_payload = {
            "target_status": "IN_PROGRESS",
            "notes": "Patient Sunita arrived at Victoria Hospital Cardiology OPD; consultation commenced.",
        }
        trans_resp = client.post(
            f"/api/v1/clinician/referrals/{ref_id}/transition",
            json=transition_payload,
            headers=clinician_auth_headers,
        )
        assert trans_resp.status_code == 200, trans_resp.text
        in_prog_ref = trans_resp.json()
        assert in_prog_ref["status"] == "IN_PROGRESS"
        assert len(in_prog_ref["history"]) == 3
        assert in_prog_ref["history"][2]["from_status"] == "ASSIGNED"
        assert in_prog_ref["history"][2]["to_status"] == "IN_PROGRESS"

        # Step 4: Close the referral with a clinical outcome: IN_PROGRESS -> COMPLETED
        close_payload = {
            "outcome_summary": "ECG within normal limits. Secondary hypertension screened; stabilized on dual anti-hypertensive therapy.",
            "outcome_diagnosis": "Essential Stage 2 Hypertension with benign headache; stable post-stabilization",
            "treatment_plan": "Amlodipine 5mg + Telmisartan 40mg once daily. Follow-up at PHC in 14 days.",
            "notes": "Specialist consultation concluded; patient discharged to primary care with complete treatment plan.",
        }
        close_resp = client.post(
            f"/api/v1/clinician/referrals/{ref_id}/close",
            json=close_payload,
            headers=clinician_auth_headers,
        )
        assert close_resp.status_code == 200, close_resp.text
        closed_ref = close_resp.json()
        assert closed_ref["status"] == "COMPLETED"
        assert closed_ref["outcome_summary"] == close_payload["outcome_summary"]
        assert closed_ref["outcome_diagnosis"] == close_payload["outcome_diagnosis"]
        assert closed_ref["treatment_plan"] == close_payload["treatment_plan"]
        assert closed_ref["closed_by"] == CLINICIAN_ID
        assert closed_ref["closed_at"] is not None
        assert len(closed_ref["history"]) == 4

        # Reload referral and inspect historical audit trail
        reload_resp = client.get(
            f"/api/v1/clinician/referrals/{ref_id}",
            headers=clinician_auth_headers,
        )
        assert reload_resp.status_code == 200
        final_ref = reload_resp.json()
        assert final_ref["status"] == "COMPLETED"
        assert len(final_ref["history"]) == 4

        # Assert transition chain: PENDING -> PENDING, PENDING -> ASSIGNED, ASSIGNED -> IN_PROGRESS, IN_PROGRESS -> COMPLETED
        transitions = [(h["from_status"], h["to_status"]) for h in final_ref["history"]]
        expected_chain = [
            ("PENDING", "PENDING"),
            ("PENDING", "ASSIGNED"),
            ("ASSIGNED", "IN_PROGRESS"),
            ("IN_PROGRESS", "COMPLETED"),
        ]
        assert transitions == expected_chain, f"Expected chain {expected_chain}, got {transitions}"

    def test_state_machine_rejects_illegal_transitions(self, clinician_auth_headers):
        """Verify that skipping states (e.g. PENDING -> COMPLETED directly) is strictly rejected with 400 Bad Request."""
        # Create fresh referral in PENDING state
        create_resp = client.post(
            "/api/v1/clinician/referrals",
            json={
                "citizen_id": CITIZEN_ID,
                "urgency": "ROUTINE",
                "reason": "Dietary consultation referral",
            },
            headers=clinician_auth_headers,
        )
        assert create_resp.status_code == 200
        ref_id = create_resp.json()["id"]

        # Attempt illegal transition: PENDING directly to COMPLETED (without ASSIGNED and IN_PROGRESS)
        illegal_resp = client.post(
            f"/api/v1/clinician/referrals/{ref_id}/transition",
            json={"target_status": "COMPLETED", "notes": "Premature completion attempt"},
            headers=clinician_auth_headers,
        )
        assert illegal_resp.status_code == 400
        err_msg = illegal_resp.json()["detail"]
        assert "Invalid referral state transition" in err_msg
        assert "PENDING" in err_msg

        # Attempt closing directly from PENDING
        illegal_close = client.post(
            f"/api/v1/clinician/referrals/{ref_id}/close",
            json={"outcome_summary": "Skipping to close"},
            headers=clinician_auth_headers,
        )
        assert illegal_close.status_code == 400
        assert "Invalid referral state transition" in illegal_close.json()["detail"]

        # Verify state was NOT corrupted and remains PENDING
        check_ref = client.get(
            f"/api/v1/clinician/referrals/{ref_id}",
            headers=clinician_auth_headers,
        ).json()
        assert check_ref["status"] == "PENDING"
        assert len(check_ref["history"]) == 1
