"""Test Suite for TC-CLN-004 and TC-CLN-005:
Clinician Review, Referrals, and Notifications
- TC-CLN-004: Failed notification delivery & retry policy (Priority: P1)
- TC-CLN-005: Duplicate escalation event & idempotency deduplication (Priority: P1)
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.types.enums import Gender, UserRole, ClinicianReviewStatus, TriageUrgency, NotificationDeliveryStatus
from packages.auth.jwt import create_access_token

client = TestClient(app)

TENANT_ID = "tenant-karnataka-moh"
CITIZEN_ID = "citizen-cln-notif-01"
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


@pytest.fixture(autouse=True)
def setup_citizen_and_clear_store():
    """Ensure a synthetic citizen exists in store with assignment and consent, and clear state."""
    c = CitizenRecord(
        id=CITIZEN_ID,
        tenant_id=TENANT_ID,
        user_id="user-cln-notif-01",
        abha_id="91-7777-8888-9999",
        first_name="Deepa",
        last_name="AlertNair",
        birth_date="1982-11-20",
        gender=Gender.FEMALE,
        phone="+919876543277",
        state="Karnataka",
        district="Bengaluru Urban",
        sub_district="Bengaluru South",
        village_or_ward="Ward 150",
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
        id="consent-cln-notif-01",
        citizen_id=CITIZEN_ID,
        grantee_id=CLINICIAN_ID,
        grantee_role=UserRole.CLINICIAN,
        purpose="CARE_DELIVERY",
        scope=["CLINICAL_REVIEW", "REFERRAL", "OBSERVATIONS", "RISK_ASSESSMENT"],
    )
    store.add_consent(consent)

    store.clear_triage_cases(CITIZEN_ID)
    store.clear_clinical_notifications(CITIZEN_ID)
    store.clear_escalation_idempotency()


# ==============================================================================
# TC-CLN-004: Failed Notification Delivery (Priority: P1)
# ==============================================================================

class TestTCCLN004FailedNotificationDelivery:
    """TC-CLN-004: Failed notification delivery
    Steps:
      1. Trigger a notification.
      2. Simulate provider failure.
      3. Inspect the notification status.
      4. Retry using the configured policy.
    Expected:
      The delivery failure is recorded and retry behavior follows policy.
      The associated clinical review is not silently marked complete.
    """

    def test_trigger_notification_recorded_failure_and_review_not_silently_completed(self, clinician_auth_headers):
        # Setup: Seed an emergent clinical triage case
        seed_resp = client.post(
            "/api/v1/clinician/triage/seed",
            json={
                "citizen_id": CITIZEN_ID,
                "rule_type": "SEVERE_HYPERTENSION",
                "custom_urgency": "EMERGENT",
                "custom_reason": "Acute BP elevation (170/105 mmHg) awaiting physician evaluation",
            },
            headers=clinician_auth_headers,
        )
        assert seed_resp.status_code == 200
        case_id = seed_resp.json()["id"]

        # Step 1 & 2: Trigger notification with simulated provider failure (503 Service Unavailable)
        trigger_payload = {
            "citizen_id": CITIZEN_ID,
            "associated_resource_id": case_id,
            "associated_resource_type": "ClinicalTriageCase",
            "channel": "SMS",
            "urgency": "EMERGENT",
            "title": "EMERGENCY: Acute Hypertensive Crisis",
            "body": "Citizen Deepa AlertNair presents with severe BP 170/105 mmHg. Immediate clinical review required.",
            "simulate_provider_failure": True,
            "failure_status_code": 503,
            "failure_error_message": "Telecom gateway 503 Service Unavailable: simulated upstream provider failure",
        }
        trig_resp = client.post(
            "/api/v1/clinician/notifications/trigger",
            json=trigger_payload,
            headers=clinician_auth_headers,
        )
        assert trig_resp.status_code == 200, trig_resp.text
        notif = trig_resp.json()
        notif_id = notif["id"]

        # Step 3: Inspect notification status
        assert notif["status"] == "FAILED"
        assert notif["delivery_attempts"] == 1
        assert "503 Service Unavailable" in notif["failure_reason"]
        assert notif["last_attempt_at"] is not None
        assert notif["delivered_at"] is None
        assert len(notif["attempts_history"]) == 1
        assert notif["attempts_history"][0]["status"] == "FAILED"
        assert notif["attempts_history"][0]["response_code"] == 503

        # Step 3 Guardrail: The associated clinical review is NOT silently marked complete!
        case_resp = client.get(
            f"/api/v1/clinician/triage/{case_id}",
            headers=clinician_auth_headers,
        )
        assert case_resp.status_code == 200
        case_data = case_resp.json()
        assert case_data["status"] == "PENDING", "Clinical review must remain PENDING upon notification failure"
        assert case_data["is_acknowledged"] is False, "Review item must not be silently acknowledged"
        assert case_data["decision"] is None, "Review item must not have premature decision"
        assert notif_id in case_data["associated_notification_ids"]

        # Step 4: Retry using configured policy (simulating provider recovery)
        retry_resp = client.post(
            f"/api/v1/clinician/notifications/{notif_id}/retry",
            json={"simulate_provider_failure": False, "notes": "Provider recovered; retrying delivery."},
            headers=clinician_auth_headers,
        )
        assert retry_resp.status_code == 200, retry_resp.text
        retried_notif = retry_resp.json()
        assert retried_notif["status"] == "DELIVERED"
        assert retried_notif["delivery_attempts"] == 2
        assert retried_notif["delivered_at"] is not None
        assert retried_notif["failure_reason"] is None
        assert len(retried_notif["attempts_history"]) == 2
        assert retried_notif["attempts_history"][1]["status"] == "SUCCESS"

        # Step 4 Guardrail Recheck: Clinical review STILL not silently completed after notification delivery
        case_after_retry = client.get(
            f"/api/v1/clinician/triage/{case_id}",
            headers=clinician_auth_headers,
        ).json()
        assert case_after_retry["status"] == "PENDING", "Clinical review must still remain PENDING awaiting physician review"
        assert case_after_retry["is_acknowledged"] is False

    def test_retry_exhaustion_transitions_to_dead_letter_while_preserving_triage_item(self, clinician_auth_headers):
        """Verify that multiple delivery failures transition to DEAD_LETTER after max_retries
        without dismissing or corrupting the clinician review queue item.
        """
        # Seed triage case
        seed_resp = client.post(
            "/api/v1/clinician/triage/seed",
            json={"citizen_id": CITIZEN_ID, "rule_type": "CRITICAL_GLYCEMIC"},
            headers=clinician_auth_headers,
        )
        case_id = seed_resp.json()["id"]

        # Attempt 1: Failed
        notif_resp = client.post(
            "/api/v1/clinician/notifications/trigger",
            json={
                "citizen_id": CITIZEN_ID,
                "associated_resource_id": case_id,
                "simulate_provider_failure": True,
            },
            headers=clinician_auth_headers,
        )
        notif_id = notif_resp.json()["id"]
        assert notif_resp.json()["status"] == "FAILED"
        assert notif_resp.json()["delivery_attempts"] == 1

        # Attempt 2: Failed retry
        retry_2 = client.post(
            f"/api/v1/clinician/notifications/{notif_id}/retry",
            json={"simulate_provider_failure": True, "failure_error_message": "Upstream timeout on attempt 2"},
            headers=clinician_auth_headers,
        )
        assert retry_2.status_code == 200
        assert retry_2.json()["status"] == "FAILED"
        assert retry_2.json()["delivery_attempts"] == 2

        # Attempt 3: Failed retry (reaches max_retries = 3) -> DEAD_LETTER
        retry_3 = client.post(
            f"/api/v1/clinician/notifications/{notif_id}/retry",
            json={"simulate_provider_failure": True, "failure_error_message": "Gateway persistent outage"},
            headers=clinician_auth_headers,
        )
        assert retry_3.status_code == 200
        final_notif = retry_3.json()
        assert final_notif["status"] == "DEAD_LETTER"
        assert final_notif["delivery_attempts"] == 3
        assert len(final_notif["attempts_history"]) == 3

        # Subsequent retry rejected with 400 Bad Request
        retry_4 = client.post(
            f"/api/v1/clinician/notifications/{notif_id}/retry",
            headers=clinician_auth_headers,
        )
        assert retry_4.status_code == 400
        assert "cannot be retried" in retry_4.json()["detail"]

        # Ensure clinical review item is still PENDING in queue
        case_check = client.get(
            f"/api/v1/clinician/triage/{case_id}",
            headers=clinician_auth_headers,
        ).json()
        assert case_check["status"] == "PENDING", "Dead-letter notification must never dismiss clinical review"


# ==============================================================================
# TC-CLN-005: Duplicate Escalation Event (Priority: P1)
# ==============================================================================

class TestTCCLN005DuplicateEscalationEvent:
    """TC-CLN-005: Duplicate escalation event
    Steps:
      1. Submit the same escalation event twice.
      2. Retrieve the resulting alerts and review items.
    Expected:
      Duplicate-event handling prevents unintended duplicate work items or clearly
      identifies repeated events according to the documented policy.
    """

    def test_duplicate_escalation_prevents_duplicate_work_items_and_links_event(self, clinician_auth_headers):
        idempotency_key = "idemp-acute-spike-20261011-001"
        payload = {
            "citizen_id": CITIZEN_ID,
            "event_type": "ACUTE_BIOMETRIC_CRISIS",
            "idempotency_key": idempotency_key,
            "urgency": "EMERGENT",
            "reason": "Acute Stage 2 Hypertensive Crisis: SBP 178 mmHg, DBP 108 mmHg with dizziness",
            "key_observations": {"systolic_bp": 178, "diastolic_bp": 108, "heart_rate": 96},
        }

        # Step 1a: Submit first escalation event
        resp_1 = client.post(
            "/api/v1/clinician/triage/escalate",
            json=payload,
            headers=clinician_auth_headers,
        )
        assert resp_1.status_code == 200, resp_1.text
        data_1 = resp_1.json()
        case_id_1 = data_1["case_id"]
        assert data_1["is_duplicate"] is False
        assert data_1["duplicate_action"] == "CREATED"
        assert data_1["duplicate_count"] == 0

        # Step 1b: Submit the exact same escalation event twice
        resp_2 = client.post(
            "/api/v1/clinician/triage/escalate",
            json=payload,
            headers=clinician_auth_headers,
        )
        assert resp_2.status_code == 200, resp_2.text
        data_2 = resp_2.json()
        assert data_2["case_id"] == case_id_1, "Must return the existing case ID"
        assert data_2["is_duplicate"] is True, "Must clearly identify the event as duplicate"
        assert data_2["duplicate_action"] == "SUPPRESSED_AND_LINKED"
        assert data_2["duplicate_count"] == 1
        assert "Duplicate escalation event" in data_2["message"]

        # Step 2: Retrieve the resulting review items in the clinician triage queue
        queue_resp = client.get(
            f"/api/v1/clinician/triage?citizen_id={CITIZEN_ID}",
            headers=clinician_auth_headers,
        )
        assert queue_resp.status_code == 200
        queue_items = queue_resp.json()

        # EXPECTED RESULT: Duplicate handling prevents unintended duplicate work items!
        assert len(queue_items) == 1, f"Expected exactly 1 triage item in queue, found {len(queue_items)}"
        item = queue_items[0]
        assert item["id"] == case_id_1
        assert item["duplicate_submission_count"] == 1
        assert item["status"] == "PENDING"
        assert len(item["duplicate_events"]) == 1
        assert item["duplicate_events"][0]["idempotency_key"] == idempotency_key

        # Retrieve alerts and verify repeated event is clearly marked
        alerts = store.get_alerts(CITIZEN_ID)
        assert len(alerts) >= 2
        dup_alert = next((a for a in alerts if a.get("alert_type") == "DUPLICATE_ESCALATION_SUPPRESSED"), None)
        assert dup_alert is not None, "Alert registry must record suppressed duplicate event"
        assert dup_alert["duplicate_suppressed"] is True
        assert dup_alert["linked_case_id"] == case_id_1

    def test_third_duplicate_submission_increments_counter_and_maintains_single_item(self, clinician_auth_headers):
        """Verify repeated duplicate submissions continue to increment duplicate_submission_count
        while strictly preventing duplicate review work items.
        """
        idempotency_key = "idemp-glycemic-crisis-002"
        payload = {
            "citizen_id": CITIZEN_ID,
            "event_type": "CRITICAL_HYPERGLYCEMIA",
            "idempotency_key": idempotency_key,
            "urgency": "PRIORITY",
            "reason": "Blood glucose spike 310 mg/dL",
            "key_observations": {"fasting_glucose": 310},
        }

        # 1st call
        res1 = client.post("/api/v1/clinician/triage/escalate", json=payload, headers=clinician_auth_headers).json()
        assert res1["is_duplicate"] is False
        case_id = res1["case_id"]

        # 2nd call
        res2 = client.post("/api/v1/clinician/triage/escalate", json=payload, headers=clinician_auth_headers).json()
        assert res2["is_duplicate"] is True
        assert res2["duplicate_count"] == 1

        # 3rd call
        res3 = client.post("/api/v1/clinician/triage/escalate", json=payload, headers=clinician_auth_headers).json()
        assert res3["is_duplicate"] is True
        assert res3["duplicate_count"] == 2

        # Check single case in queue with counter = 2
        queue = client.get(f"/api/v1/clinician/triage?citizen_id={CITIZEN_ID}", headers=clinician_auth_headers).json()
        matching = [c for c in queue if c["id"] == case_id]
        assert len(matching) == 1
        assert matching[0]["duplicate_submission_count"] == 2
        assert len(matching[0]["duplicate_events"]) == 2
