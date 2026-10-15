"""TC-CON-001, TC-CON-002, and TC-CON-003 P0 Test Suite.

TC-CON-001: Consent capture (SYS-CON-001)
  - Register synthetic citizen.
  - Present applicable consent notice.
  - Record consent.
  - Retrieve consent record.
  - Expected: Purpose, scope, version, status, and timestamp are persisted.

TC-CON-002: Revoked wearable consent
  - Connect synthetic wearable provider.
  - Import test observations.
  - Revoke wearable-data access.
  - Attempt another synchronization (blocked, no false success report).
  - Attempt AI access to connected wearable data (blocked).
  - Expected: Future access blocked, revocation recorded, retention policy upheld.

TC-CON-003: Consent-purpose mismatch
  - Create consent for permitted screening workflow.
  - Attempt to use authorization for unrelated research workflow.
  - Inspect authorization result.
  - Expected: System denies processing outside granted scope.
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient

from services.api.main import app
from services.store import store, CitizenRecord, UserRecord, ConsentDirective
from packages.clinical_models.consent import (
    consent_manager,
    ConsentCategory,
    ConsentStatus,
    HealthcareConsent,
)
from packages.auth.jwt import create_access_token, decode_access_token
from packages.types.enums import UserRole, Gender
from packages.auth.access_control import HealthcareAuthorizationEngine
from services.wearable.adapter import wearable_adapter, MockWearableProvider
from services.wearable.models import WearableProvider
from services.ai_agent.tools import get_wearable_summary, agent_telemetry, _authorize_and_get_citizen

client = TestClient(app)


def make_synthetic_citizen(
    citizen_id: str,
    user_id: str = "user-synth",
    first_name: str = "Deepa",
    last_name: str = "Nagaraj",
    tenant_id: str = "mysuru_district_health",
) -> CitizenRecord:
    """Helper creating a valid CitizenRecord in store."""
    citizen = CitizenRecord(
        id=citizen_id,
        tenant_id=tenant_id,
        user_id=user_id,
        abha_id=f"12-3456-7890-{uuid.uuid4().hex[:4]}",
        first_name=first_name,
        last_name=last_name,
        birth_date="1985-06-15",
        gender=Gender.FEMALE,
        phone="9876543210",
        state="Karnataka",
        district="Mysuru",
        sub_district="Nanjangud",
        village_or_ward="Ward 04",
        primary_language="kn",
    )
    store.add_citizen(citizen)
    return citizen


@pytest.fixture(autouse=True)
def clean_test_environment():
    """Ensures clean state between test runs."""
    agent_telemetry.clear()
    store.clear_security_incidents()
    yield


@pytest.fixture
def auth_tokens():
    """Generates valid tokens for testing."""
    citizen_token = create_access_token(
        subject="user-synthetic-citizen-01",
        tenant_id="mysuru_district_health",
        role=UserRole.CITIZEN,
    )
    hw_token = create_access_token(
        subject="user-hw-sarita-01",
        tenant_id="mysuru_district_health",
        role=UserRole.HEALTH_WORKER,
    )
    clinician_token = create_access_token(
        subject="user-doc-anand-01",
        tenant_id="mysuru_district_health",
        role=UserRole.CLINICIAN,
    )
    admin_token = create_access_token(
        subject="user-admin-01",
        tenant_id="mysuru_district_health",
        role=UserRole.SYSTEM_ADMIN,
    )
    return {
        "citizen": citizen_token,
        "health_worker": hw_token,
        "clinician": clinician_token,
        "admin": admin_token,
    }


# ==============================================================================
# TC-CON-001: Consent Capture (Requirement SYS-CON-001)
# ==============================================================================

class TestTC_CON_001_ConsentCapture:
    """TC-CON-001: Consent capture lifecycle verification."""

    def test_tc_con_001_end_to_end_consent_capture_and_persistence(self, auth_tokens):
        """Steps:
        1. Register a synthetic citizen.
        2. Present the applicable consent notice.
        3. Record consent.
        4. Retrieve the consent record.
        Expected: The consent purpose, scope, version, status and timestamp are persisted.
        """
        # Step 1: Register synthetic citizen
        synthetic_citizen_id = f"citizen-synth-{uuid.uuid4().hex[:8]}"
        synthetic_user_id = f"user-{synthetic_citizen_id}"
        make_synthetic_citizen(synthetic_citizen_id, user_id=synthetic_user_id)

        citizen_token = create_access_token(
            subject=synthetic_user_id,
            tenant_id="mysuru_district_health",
            role=UserRole.CITIZEN,
        )
        headers = {"Authorization": f"Bearer {citizen_token}"}

        # Step 2: Present the applicable consent notice (SYS-CON-001)
        notice_resp = client.get("/api/v1/privacy/notices/SYS-CON-001", headers=headers)
        assert notice_resp.status_code == 200, f"Failed to retrieve consent notice: {notice_resp.text}"
        notice = notice_resp.json()

        assert notice["requirement_id"] == "SYS-CON-001"
        assert notice["notice_id"] == "SYS-CON-001-NOTICE-V1"
        assert notice["version"] == "v1.2.0"
        assert "Clinical & Health Screening Consent Notice" in notice["title"]
        assert len(notice["data_scope_disclosed"]) > 0
        assert "retention_policy" in notice
        assert "revocation_rights" in notice
        assert "non_diagnostic_disclaimer" in notice

        # Step 3: Record consent
        consent_payload = {
            "subject": synthetic_citizen_id,
            "category": "health_screening",
            "purpose": "HEALTH_SCREENING",
            "scope": ["vitals", "screening_answers", "lifestyle", "risk_scores"],
            "recipient": "PRIMARY_CARE_TEAM",
            "version": notice["version"],
            "notice_id": notice["notice_id"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        }

        grant_resp = client.post("/api/v1/privacy/consents", json=consent_payload, headers=headers)
        assert grant_resp.status_code == 201, f"Failed to record consent: {grant_resp.text}"
        granted = grant_resp.json()

        consent_id = granted["id"]
        assert consent_id is not None
        assert granted["status"] == "ACTIVE"
        assert granted["purpose"] == "HEALTH_SCREENING"
        assert granted["scope"] == ["vitals", "screening_answers", "lifestyle", "risk_scores"]
        assert granted["version"] == "v1.2.0"
        assert granted["notice_id"] == "SYS-CON-001-NOTICE-V1"
        assert granted["created_at"] is not None

        # Step 4: Retrieve the consent record
        view_resp = client.get(f"/api/v1/privacy/consents/{consent_id}", headers=headers)
        assert view_resp.status_code == 200, f"Failed to retrieve consent record: {view_resp.text}"
        persisted = view_resp.json()

        # Expected Result Verifications:
        # 1. Purpose persisted
        assert persisted["purpose"] == "HEALTH_SCREENING"
        # 2. Scope persisted
        assert persisted["scope"] == ["vitals", "screening_answers", "lifestyle", "risk_scores"]
        # 3. Version persisted
        assert persisted["version"] == "v1.2.0"
        # 4. Status persisted
        assert persisted["status"] == "ACTIVE"
        # 5. Timestamp persisted
        assert persisted["created_at"] is not None
        assert "T" in persisted["created_at"]  # ISO-8601 formatted
        # Additional SYS-CON-001 auditability checks
        assert persisted["notice_id"] == "SYS-CON-001-NOTICE-V1"
        assert len(persisted["audit_trail"]) >= 1
        assert persisted["audit_trail"][0]["action"] == "GRANT"

    def test_tc_con_001_health_worker_mobile_consent_capture_and_retrieval(self, auth_tokens):
        """Verifies frontline worker app consent capture flow."""
        synthetic_citizen_id = f"citizen-hw-{uuid.uuid4().hex[:8]}"
        make_synthetic_citizen(synthetic_citizen_id, user_id="unregistered")

        hw_headers = {"Authorization": f"Bearer {auth_tokens['health_worker']}"}

        # 1. Present notice to frontline health worker
        notice_resp = client.get("/api/v1/health-worker/consent-notice", headers=hw_headers)
        assert notice_resp.status_code == 200
        assert notice_resp.json()["notice_id"] == "SYS-CON-001-NOTICE-V1"

        # 2. Record consent
        hw_payload = {
            "citizen_id": synthetic_citizen_id,
            "purpose": "CARE_DELIVERY",
            "scope": ["vitals", "screening_answers", "risk_scores"],
            "version": "v1.2.0",
            "notice_id": "SYS-CON-001-NOTICE-V1",
            "duration_days": 180,
        }
        rec_resp = client.post("/api/v1/health-worker/consent", json=hw_payload, headers=hw_headers)
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()

        consent_id = rec_data["consent_id"]
        assert rec_data["status"] == "ACTIVE"
        assert rec_data["purpose"] == "CARE_DELIVERY"
        assert rec_data["scope"] == ["vitals", "screening_answers", "risk_scores"]
        assert rec_data["version"] == "v1.2.0"
        assert rec_data["timestamp"] is not None

        # 3. Retrieve consent record
        get_resp = client.get(f"/api/v1/health-worker/consent/{consent_id}", headers=hw_headers)
        assert get_resp.status_code == 200
        retrieved = get_resp.json()
        assert retrieved["purpose"] == "CARE_DELIVERY"
        assert retrieved["scope"] == ["vitals", "screening_answers", "risk_scores"]
        assert retrieved["version"] == "v1.2.0"
        assert retrieved["status"] == "ACTIVE"
        assert retrieved["granted_at"] is not None


# ==============================================================================
# TC-CON-002: Revoked Wearable Consent
# ==============================================================================

class TestTC_CON_002_RevokedWearableConsent:
    """TC-CON-002: Revoked wearable data access and retention enforcement."""

    @pytest.mark.asyncio
    async def test_tc_con_002_wearable_revocation_lifecycle(self, auth_tokens):
        """Steps:
        1. Connect a synthetic wearable provider.
        2. Import test observations.
        3. Revoke wearable-data access.
        4. Attempt another synchronization (blocked, no false success).
        5. Attempt AI access to the connected wearable data (blocked).
        Expected:
        - Future access blocked.
        - System records revocation.
        - Existing records retained/deleted per retention policy.
        - Application does NOT falsely report a successful sync.
        """
        citizen_id = f"citizen-wearable-{uuid.uuid4().hex[:8]}"
        user_id = f"user-{citizen_id}"
        make_synthetic_citizen(citizen_id, user_id=user_id)

        citizen_token = create_access_token(
            subject=user_id,
            tenant_id="mysuru_district_health",
            role=UserRole.CITIZEN,
        )
        headers = {"Authorization": f"Bearer {citizen_token}"}

        # Step 1: Connect a synthetic wearable provider (Garmin)
        connect_resp = client.post(
            "/api/v1/wearables/connect/garmin",
            json={"citizen_id": citizen_id, "access_token": "oauth_token_garmin_test"},
            headers=headers,
        )
        assert connect_resp.status_code == 200, f"Connect failed: {connect_resp.text}"
        conn_data = connect_resp.json()
        assert conn_data["status"] in ["CONNECTED", "ACTIVE"]
        assert conn_data["consent_granted_at"] is not None
        assert conn_data["consent_revoked_at"] is None

        # Step 2: Import test observations (Initial sync succeeds)
        sync_resp_1 = client.post(
            f"/api/v1/wearables/sync/{citizen_id}?provider=garmin&trend=DEFAULT",
            headers=headers,
        )
        assert sync_resp_1.status_code == 200, f"Initial sync failed: {sync_resp_1.text}"
        sync_data_1 = sync_resp_1.json()
        assert sync_data_1["status"] == "SUCCESS"
        assert sync_data_1["synced_records_count"] > 0
        assert sync_data_1["projection"] is not None
        initial_synced_count = sync_data_1["synced_records_count"]

        # Step 3: Revoke wearable-data access
        revoke_resp = client.post(
            "/api/v1/privacy/wearables/revoke",
            params={"reason": "Citizen revoked continuous smartwatch biometrics stream"},
            headers=headers,
        )
        assert revoke_resp.status_code == 200, f"Revocation failed: {revoke_resp.text}"
        revoke_data = revoke_resp.json()
        assert revoke_data["status"] == "SUCCESS"
        assert revoke_data["wearable_access_status"] == "REVOKED"
        assert revoke_data["retention_policy"] == "RETAINED_FOR_AUDIT_TRAIL"

        # Verify system records revocation timestamp in adapter connection state
        conn_state = wearable_adapter.connections[citizen_id]["garmin"]
        assert conn_state.status == "REVOKED"
        assert conn_state.consent_revoked_at is not None

        # Step 4: Attempt another synchronization (MUST BE BLOCKED)
        sync_resp_2 = client.post(
            f"/api/v1/wearables/sync/{citizen_id}?provider=garmin&trend=DEFAULT",
            headers=headers,
        )
        # Expected: HTTP 403 Forbidden, and NEVER reports a false successful sync
        assert sync_resp_2.status_code == 403, (
            f"Sync after revocation should be 403, got {sync_resp_2.status_code}: {sync_resp_2.text}"
        )
        assert "REVOKED" in sync_resp_2.json()["detail"].upper()
        # Verify application did not report success
        assert sync_resp_2.json().get("status") != "SUCCESS"

        # Verify adapter sync method itself raises PermissionError directly
        with pytest.raises(PermissionError) as exc_info:
            await wearable_adapter.sync(citizen_id=citizen_id, provider=WearableProvider.GARMIN)
        assert "revoked" in str(exc_info.value).lower()

        # Step 5: Attempt AI access to the connected wearable data (MUST BE BLOCKED)
        token_payload = decode_access_token(citizen_token)

        with pytest.raises(PermissionError) as ai_exc:
            get_wearable_summary(citizen_id=citizen_id, actor=token_payload)
        assert "revoked" in str(ai_exc.value).lower()

        # Check telemetry logged the blocked tool call
        recent_tool_logs = agent_telemetry.get_logs_for_citizen(citizen_id)
        assert any(log["status"] == "CONSENT_REVOKED" for log in recent_tool_logs)

        # Step 6: Existing records are retained according to retention policy
        retention_resp = client.get(f"/api/v1/wearables/retention-policy/{citizen_id}", headers=headers)
        assert retention_resp.status_code == 200
        ret_data = retention_resp.json()
        assert ret_data["is_revoked"] is True
        assert ret_data["future_sync_blocked"] is True
        assert ret_data["existing_records_count"] >= initial_synced_count
        assert ret_data["retention_policy"] == "RETAINED_FOR_AUDIT_TRAIL"

        # Test Right-to-be-Forgotten deletion purge
        purge_resp = client.delete(f"/api/v1/wearables/data/{citizen_id}", headers=headers)
        assert purge_resp.status_code == 200
        assert purge_resp.json()["status"] == "DATA_PURGED"

        # After purge, stored count is 0
        assert len(wearable_adapter.raw_records.get(citizen_id, [])) == 0


# ==============================================================================
# TC-CON-003: Consent-Purpose Mismatch
# ==============================================================================

class TestTC_CON_003_ConsentPurposeMismatch:
    """TC-CON-003: Purpose limitation enforcement outside granted scope."""

    def test_tc_con_003_consent_purpose_mismatch_denied(self, auth_tokens):
        """Steps:
        1. Create consent for a permitted screening workflow.
        2. Attempt to use the same authorization for an unrelated research workflow.
        3. Inspect the authorization result.
        Expected: The system denies processing outside the granted scope.
        """
        citizen_id = f"citizen-scope-{uuid.uuid4().hex[:8]}"
        user_id = f"user-{citizen_id}"
        citizen = make_synthetic_citizen(citizen_id, user_id=user_id)

        citizen_token = create_access_token(
            subject=user_id,
            tenant_id="mysuru_district_health",
            role=UserRole.CITIZEN,
        )
        citizen_headers = {"Authorization": f"Bearer {citizen_token}"}
        clinician_headers = {"Authorization": f"Bearer {auth_tokens['clinician']}"}

        # Step 1: Create consent for a permitted screening workflow
        grant_resp = client.post(
            "/api/v1/privacy/consents",
            json={
                "subject": citizen_id,
                "category": "health_screening",
                "purpose": "HEALTH_SCREENING",
                "scope": ["vitals", "screening_answers"],
                "recipient": "user-doc-anand-01",
                "version": "v1.2.0",
                "notice_id": "SYS-CON-001-NOTICE-V1",
            },
            headers=citizen_headers,
        )
        assert grant_resp.status_code == 201
        assert grant_resp.json()["purpose"] == "HEALTH_SCREENING"

        # Verify permitted screening workflow is authorized
        verify_permitted_resp = client.post(
            "/api/v1/privacy/verify-access",
            json={
                "subject": citizen_id,
                "category": "health_screening",
                "requested_purpose": "HEALTH_SCREENING",
                "recipient": "user-doc-anand-01",
                "required_scope": "vitals",
            },
            headers=clinician_headers,
        )
        assert verify_permitted_resp.status_code == 200
        permitted_result = verify_permitted_resp.json()
        assert permitted_result["allowed"] is True
        assert permitted_result["decision"] == "PERMITTED"

        # Step 2 & 3: Attempt to use the same authorization for unrelated research workflow
        # Inspect the authorization result
        verify_research_resp = client.post(
            "/api/v1/privacy/verify-access",
            json={
                "subject": citizen_id,
                "category": "research_analytics",
                "requested_purpose": "RESEARCH_ANALYTICS",
                "recipient": "user-doc-anand-01",
                "required_scope": "vitals",
            },
            headers=clinician_headers,
        )
        assert verify_research_resp.status_code == 200
        research_result = verify_research_resp.json()

        # Expected Result: The system denies processing outside the granted scope!
        assert research_result["allowed"] is False
        assert research_result["decision"] == "DENIED"
        assert "purpose mismatch" in research_result["reason"].lower() or "no active consent" in research_result["reason"].lower()

        # Verify via HealthcareAuthorizationEngine layer directly
        clinician_payload = decode_access_token(auth_tokens["clinician"])

        allowed, reason = HealthcareAuthorizationEngine.can_access_citizen_record(
            actor=clinician_payload,
            citizen=citizen,
            purpose="RESEARCH_ANALYTICS",
        )
        assert allowed is False
        assert "purpose mismatch" in reason.lower() or "outside granted" in reason.lower()

    def test_tc_con_003_data_scope_mismatch_denied(self, auth_tokens):
        """Verifies that attempting to access data outside granted granular scope is denied."""
        citizen_id = f"citizen-scope-test-{uuid.uuid4().hex[:8]}"
        make_synthetic_citizen(citizen_id, user_id="synth-user-01")

        citizen_token = create_access_token(
            subject="synth-user-01",
            tenant_id="mysuru_district_health",
            role=UserRole.CITIZEN,
        )

        # Grant consent ONLY for vitals (not genomic or wearable data)
        grant_resp = client.post(
            "/api/v1/privacy/consents",
            json={
                "subject": citizen_id,
                "category": "health_screening",
                "purpose": "HEALTH_SCREENING",
                "scope": ["vitals"],
                "recipient": "PRIMARY_CARE_TEAM",
            },
            headers={"Authorization": f"Bearer {citizen_token}"},
        )
        assert grant_resp.status_code == 201

        # Attempt to access 'genomic_sequence' data scope using this consent
        verify_resp = client.post(
            "/api/v1/privacy/verify-access",
            json={
                "subject": citizen_id,
                "category": "health_screening",
                "requested_purpose": "HEALTH_SCREENING",
                "required_scope": "genomic_sequence",
            },
            headers={"Authorization": f"Bearer {auth_tokens['clinician']}"},
        )
        assert verify_resp.status_code == 200
        res = verify_resp.json()
        assert res["allowed"] is False
        assert res["decision"] == "DENIED"
        assert "scope mismatch" in res["reason"].lower() or "outside permitted" in res["reason"].lower()
