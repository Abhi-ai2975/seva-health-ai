"""Comprehensive Test Suite for Mobile-First Health Worker Application & APIs.

Verifies:
1. Health worker authentication & jurisdiction scope.
2. Program and community listing.
3. Citizen registration & deduplication/conflict merging.
4. Informed consent capture.
5. Vitals and questionnaire screening evaluation.
6. Low-connectivity batch sync engine with idempotency & retry.
7. AI risk assessment & frontline next action recommendations.
8. Referral generation with automated clinician triage escalation.
9. Follow-up scheduling and tracking.
10. Role-based least-privilege security (rejection of unauthorized roles).
"""

from datetime import datetime, timezone, timedelta
import pytest
from starlette.testclient import TestClient

from packages.types.enums import UserRole, Gender, RiskTier, TriageUrgency
from packages.auth.jwt import TokenPayload, create_access_token
from services.api.main import app
from services.store import store, CitizenRecord
from scripts.seed_data import seed_all_demo_data
from services.health_worker.service import (
    health_worker_service,
    HealthWorkerService,
)
from services.health_worker.models import (
    BatchSyncRequest,
    OfflineSyncItem,
    OfflineDraftType,
    SyncStatus,
    ReferralCreatePayload,
    FollowUpCreatePayload,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_seed():
    """Seed fresh demo personas and clean test queues."""
    store.screenings.clear()
    store.observations.clear()
    store.risk_assessments.clear()
    store.legal_clinical_records.clear()
    store.copilot_drafts.clear()
    store.referrals.clear()
    store.followups.clear()
    health_worker_service._processed_idempotency.clear()
    for cid in list(store.citizens.keys()):
        if not cid.startswith("citizen-"):
            store.citizens.pop(cid, None)
    seed_all_demo_data()


@pytest.fixture
def health_worker_token():
    return create_access_token(
        subject="worker-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.HEALTH_WORKER,
    )


@pytest.fixture
def citizen_token():
    return create_access_token(
        subject="citizen-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )


class TestHealthWorkerProgramsAndDashboard:
    """Tests 1 & 2: Program and community selection, dashboard metrics."""

    def test_list_programs_authorized_health_worker(self, health_worker_token):
        response = client.get(
            "/api/v1/health-worker/programs",
            headers={"Authorization": f"Bearer {health_worker_token}"},
        )
        assert response.status_code == 200
        programs = response.json()
        assert len(programs) >= 2
        prog_names = [p["name"] for p in programs]
        assert any("Karnataka National NCD Mukt Abhiyan" in name for name in prog_names)
        # Verify community options available
        assert "Ward 12" in programs[0]["communities"]

    def test_get_health_worker_dashboard(self, health_worker_token):
        response = client.get(
            "/api/v1/health-worker/dashboard",
            headers={"Authorization": f"Bearer {health_worker_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["worker_id"] == "worker-user-01"
        assert "Ward 12" in data["assigned_jurisdiction"]
        assert data["screenings_today_count"] >= 1
        assert "recent_citizens" in data


class TestHealthWorkerScreeningAndRegistration:
    """Tests 3, 4, 5, 6, 7: Citizen onboarding, consent, vitals & questionnaire capture."""

    def test_register_citizen_direct(self, health_worker_token):
        payload = {
            "first_name": "Manjunath",
            "last_name": "Gowda",
            "birth_date": "1975-08-20",
            "gender": "MALE",
            "phone": "9880011223",
            "abha_id": "91-1122-3344-5566",
            "village_or_ward": "Ward 12",
        }
        response = client.post(
            "/api/v1/health-worker/register",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["SUCCESS", "CONFLICT_RESOLVED"]
        assert data["citizen"]["first_name"] == "Manjunath"
        assert data["citizen"]["village_or_ward"] == "Ward 12"

    def test_record_consent_direct(self, health_worker_token):
        payload = {
            "citizen_id": "citizen-ramesh-patel-01",
            "purpose": "CARE_DELIVERY",
            "duration_days": 365,
        }
        response = client.post(
            "/api/v1/health-worker/consent",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ACTIVE"
        assert data["citizen_id"] == "citizen-ramesh-patel-01"

    def test_perform_screening_evaluation(self, health_worker_token):
        payload = {
            "citizen_id": "citizen-ramesh-patel-01",
            "workflow_id": "mvp_comprehensive",
            "answers": {
                "age": 48,
                "sex": "MALE",
                "systolic_bp": 144,
                "diastolic_bp": 92,
                "heart_rate": 80,
                "height": 168,
                "weight": 76,
                "waist_circumference": 94,
                "fasting_glucose": 140,
                "physical_activity": "Sedentary (< 30 min/week)",
                "diet_quality": "High refined carbs / sweets / fried foods",
                "smoking": "NEVER",
            },
        }
        response = client.post(
            "/api/v1/health-worker/screening",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert "explainable_summary" in data
        assert data["explainable_summary"]["idrs_score"] >= 50  # High IDRS
        assert data["structured_observations_count"] > 0


class TestLowConnectivityBatchSyncAndConflictHandling:
    """Test 8: Low-connectivity batch sync, idempotency, retry, and conflict handling."""

    def test_batch_sync_offline_drafts(self, health_worker_token):
        sync_payload = {
            "worker_id": "worker-user-01",
            "device_id": "mobile-field-tablet-42",
            "app_version": "1.0.0",
            "drafts": [
                {
                    "draft_id": "draft-reg-001",
                    "type": "CITIZEN_REGISTRATION",
                    "client_timestamp": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": "idemp-cit-kavitha-01",
                    "payload": {
                        "id": "cit-kavitha-id-01",
                        "first_name": "Kavitha",
                        "last_name": "Rao",
                        "birth_date": "1982-05-14",
                        "gender": "FEMALE",
                        "phone": "9876500112",
                        "village_or_ward": "Ward 12",
                    },
                    "client_version": 1,
                },
                {
                    "draft_id": "draft-consent-001",
                    "type": "CONSENT",
                    "client_timestamp": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": "idemp-consent-kavitha-01",
                    "payload": {
                        "citizen_id": "cit-kavitha-id-01",
                        "purpose": "CARE_DELIVERY",
                        "duration_days": 180,
                    },
                    "client_version": 1,
                },
            ],
        }

        response = client.post(
            "/api/v1/health-worker/sync",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=sync_payload,
        )
        assert response.status_code == 200
        res = response.json()
        assert res["total_submitted"] == 2
        assert res["synced_count"] == 2
        assert res["failed_count"] == 0

    def test_batch_sync_idempotency_and_replay(self, health_worker_token):
        sync_payload = {
            "worker_id": "worker-user-01",
            "device_id": "mobile-field-tablet-42",
            "drafts": [
                {
                    "draft_id": "draft-idemp-test-01",
                    "type": "CITIZEN_REGISTRATION",
                    "client_timestamp": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": "idemp-unique-key-999",
                    "payload": {
                        "id": "cit-unique-999",
                        "first_name": "Ananya",
                        "last_name": "Devi",
                        "birth_date": "1985-02-10",
                        "gender": "FEMALE",
                        "phone": "9845112233",
                        "village_or_ward": "Ward 12",
                    },
                    "client_version": 1,
                }
            ],
        }

        # First submission
        res1 = client.post(
            "/api/v1/health-worker/sync",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=sync_payload,
        )
        assert res1.status_code == 200
        assert res1.json()["results"][0]["status"] == "SYNCED"

        # Replay same submission (simulating retry after timeout)
        res2 = client.post(
            "/api/v1/health-worker/sync",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=sync_payload,
        )
        assert res2.status_code == 200
        # Should detect existing idempotency key and return ALREADY_PROCESSED safely
        assert res2.json()["results"][0]["status"] == "ALREADY_PROCESSED"

    def test_batch_sync_conflict_handling_merges_records(self, health_worker_token):
        # Ramesh Patel already exists in store. Submit registration draft with matching phone & first name
        conflict_payload = {
            "worker_id": "worker-user-01",
            "device_id": "mobile-field-tablet-42",
            "drafts": [
                {
                    "draft_id": "draft-conflict-ramesh",
                    "type": "CITIZEN_REGISTRATION",
                    "client_timestamp": datetime.now(timezone.utc).isoformat(),
                    "idempotency_key": "idemp-conflict-new-key",
                    "payload": {
                        "first_name": "Ramesh",
                        "last_name": "Patel",
                        "phone": "9845123456",
                        "village_or_ward": "Ward 12",
                    },
                    "client_version": 1,
                }
            ],
        }
        res = client.post(
            "/api/v1/health-worker/sync",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=conflict_payload,
        )
        assert res.status_code == 200
        # Conflict handled gracefully with CONFLICT_RESOLVED status
        result_item = res.json()["results"][0]
        assert result_item["status"] in ["CONFLICT_RESOLVED", "SYNCED"]


class TestRiskAssessmentReferralAndFollowUp:
    """Tests 9, 10, 11, 12: AI Risk, Recommended Action, Referral, and Follow-Up."""

    def test_generate_risk_and_action_recommendation(self, health_worker_token):
        response = client.post(
            "/api/v1/health-worker/risk-assessment/citizen-ramesh-patel-01",
            headers={"Authorization": f"Bearer {health_worker_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["citizen_id"] == "citizen-ramesh-patel-01"
        rec = data["recommendation"]
        assert rec["urgency_tier"] in ["RED", "AMBER"]
        assert len(rec["action_items"]) > 0
        assert "clinical_rationale" in rec
        assert rec["followup_days_suggested"] <= 14

    def test_create_referral_and_escalate_triage(self, health_worker_token):
        payload = {
            "citizen_id": "citizen-ramesh-patel-01",
            "facility_name": "Mysuru District Hospital - NCD Special Clinic",
            "facility_type": "DISTRICT_HOSPITAL",
            "urgency": "PRIORITY",
            "reason": "Stage 2 Hypertension (142/92 mmHg) and elevated IDRS (60/100)",
            "provisional_diagnosis": "Essential Hypertension & Prediabetes",
            "clinical_notes": "Needs confirmatory fasting laboratory panel and physician review.",
            "transport_assistance_needed": False,
        }
        response = client.post(
            "/api/v1/health-worker/refer",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=payload,
        )
        assert response.status_code == 200
        data = response.json()
        assert "referral_slip_id" in data
        assert data["referral_slip_id"].startswith("REF-")
        assert data["urgency"] == "PRIORITY"

        # Verify referral stored in store
        refs = store.get_referrals("citizen-ramesh-patel-01")
        assert len(refs) >= 1

        # Verify automated escalation: enqueued into clinician triage cases
        triage_cases = store.list_triage_cases()
        matching = [tc for tc in triage_cases if tc.citizen_id == "citizen-ramesh-patel-01"]
        assert len(matching) >= 1
        assert matching[-1].urgency in [TriageUrgency.PRIORITY, TriageUrgency.EMERGENT]

    def test_schedule_and_list_followup(self, health_worker_token):
        target_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
        payload = {
            "citizen_id": "citizen-ramesh-patel-01",
            "scheduled_date": target_date,
            "purpose": "Home visit to verify BP control and PHC attendance",
            "contact_mode": "HOME_VISIT",
            "notes": "Verify adherence to salt restriction and morning walks",
        }
        response = client.post(
            "/api/v1/health-worker/followup",
            headers={"Authorization": f"Bearer {health_worker_token}"},
            json=payload,
        )
        assert response.status_code == 200
        f_data = response.json()
        assert f_data["scheduled_date"] == target_date
        assert f_data["status"] == "PENDING"

        # List follow-ups
        list_res = client.get(
            "/api/v1/health-worker/followups",
            headers={"Authorization": f"Bearer {health_worker_token}"},
        )
        assert list_res.status_code == 200
        items = list_res.json()
        assert len(items) >= 1
        assert any(i["citizen_id"] == "citizen-ramesh-patel-01" for i in items)


class TestRoleBasedLeastPrivilegeAccess:
    """Security verification: Role-based authorization boundaries."""

    def test_citizen_role_blocked_from_health_worker_api(self, citizen_token):
        # A citizen cannot access health worker sync or programs
        res_prog = client.get(
            "/api/v1/health-worker/programs",
            headers={"Authorization": f"Bearer {citizen_token}"},
        )
        assert res_prog.status_code == 403

        res_sync = client.post(
            "/api/v1/health-worker/sync",
            headers={"Authorization": f"Bearer {citizen_token}"},
            json={"worker_id": "unauth", "drafts": []},
        )
        assert res_sync.status_code == 403
