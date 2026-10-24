import pytest
from starlette.testclient import TestClient

from services.api.main import app
from scripts.seed_data import seed_all_demo_data
from packages.types.enums import UserRole
from services.store import store, UserRecord, CitizenRecord

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_data():
    seed_all_demo_data()


def get_token_for(email: str, password: str = "password123") -> str:
    res = client.post("/api/v1/auth/token", json={"email": email, "password": password})
    assert res.status_code == 200, f"Login failed for {email}: {res.text}"
    return res.json()["access_token"]


# 1. Citizen Self-Access vs Citizen Isolation
def test_citizen_self_access_allowed():
    token = get_token_for("citizen@sevahealth.ai")
    # Ramesh Patel accessing his own record
    res = client.get(
        "/api/v1/citizens/citizen-ramesh-patel-01",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json()["first_name"] == "Ramesh"


def test_citizen_cross_access_forbidden():
    token = get_token_for("citizen@sevahealth.ai")
    # Ramesh Patel attempting to access Lakshmi Devi's record
    res = client.get(
        "/api/v1/citizens/citizen-lakshmi-devi-02",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 403
    assert "Citizens can only access their own medical records" in res.json()["detail"]


def test_citizen_listing_scoped_to_self():
    token = get_token_for("citizen@sevahealth.ai")
    res = client.get(
        "/api/v1/citizens",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == "citizen-ramesh-patel-01"


# 2. Clinician Care Context & Patient Panel Access
def test_clinician_accesses_assigned_patient():
    token = get_token_for("doctor@sevahealth.ai")
    # Ramesh is in Dr. Kulkarni's assigned panel
    res = client.get(
        "/api/v1/citizens/citizen-ramesh-patel-01",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json()["first_name"] == "Ramesh"


def test_clinician_accesses_triage_patient():
    token = get_token_for("doctor@sevahealth.ai")
    # Lakshmi Devi is in the clinical triage queue
    res = client.get(
        "/api/v1/citizens/citizen-lakshmi-devi-02",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json()["first_name"] == "Lakshmi"


def test_clinician_blocked_from_unassigned_patient_without_consent():
    token = get_token_for("doctor@sevahealth.ai")
    # Priya Sharma is not assigned, not in triage, and has not granted consent
    res = client.get(
        "/api/v1/citizens/citizen-priya-sharma-04",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 403
    assert "not assigned to this patient's care context" in res.json()["detail"]


# 3. Patient Consent Directives (ABDM / DISHA Alignment)
def test_consent_grant_and_revoke_lifecycle():
    priya_token = get_token_for("priya@sevahealth.ai")
    doctor_token = get_token_for("doctor@sevahealth.ai")

    # Step A: Doctor denied before consent
    res_before = client.get(
        "/api/v1/citizens/citizen-priya-sharma-04",
        headers={"Authorization": f"Bearer {doctor_token}"}
    )
    assert res_before.status_code == 403

    # Step B: Priya grants consent to Dr. Anand Kulkarni
    grant_res = client.post(
        "/api/v1/auth/consent/grant",
        headers={"Authorization": f"Bearer {priya_token}"},
        json={
            "citizen_id": "citizen-priya-sharma-04",
            "grantee_id": "doctor-user-01",
            "grantee_role": "CLINICIAN",
            "purpose": "CARE_DELIVERY",
            "duration_days": 30
        }
    )
    assert grant_res.status_code == 201
    consent_id = grant_res.json()["consent_id"]

    # Step C: Doctor now has legitimate care context access!
    res_after_grant = client.get(
        "/api/v1/citizens/citizen-priya-sharma-04",
        headers={"Authorization": f"Bearer {doctor_token}"}
    )
    assert res_after_grant.status_code == 200
    assert res_after_grant.json()["first_name"] == "Priya"

    # Step D: Priya revokes consent
    revoke_res = client.post(
        "/api/v1/auth/consent/revoke",
        headers={"Authorization": f"Bearer {priya_token}"},
        json={
            "citizen_id": "citizen-priya-sharma-04",
            "consent_id": consent_id
        }
    )
    assert revoke_res.status_code == 200

    # Step E: Doctor access is immediately forbidden again
    res_after_revoke = client.get(
        "/api/v1/citizens/citizen-priya-sharma-04",
        headers={"Authorization": f"Bearer {doctor_token}"}
    )
    assert res_after_revoke.status_code == 403


# 4. Health Worker Jurisdiction Boundaries
def test_health_worker_jurisdiction_scoping():
    worker_token = get_token_for("worker@sevahealth.ai")

    # Ramesh is in Ward 12 (Sunita Devi's assigned jurisdiction)
    res_inside = client.get(
        "/api/v1/citizens/citizen-ramesh-patel-01",
        headers={"Authorization": f"Bearer {worker_token}"}
    )
    assert res_inside.status_code == 200
    assert res_inside.json()["village_or_ward"] == "Ward 12"

    # Vikram Singh is in "Highway Corridor Ward", outside jurisdiction
    res_outside = client.get(
        "/api/v1/citizens/citizen-vikram-singh-03",
        headers={"Authorization": f"Bearer {worker_token}"}
    )
    assert res_outside.status_code == 403
    assert "outside Health Worker assigned jurisdiction" in res_outside.json()["detail"]


# 5. Public Health Administrator Least-Privilege Isolation
def test_public_health_admin_blocked_from_identifiable_records():
    admin_token = get_token_for("admin@sevahealth.ai")

    # Blocked from individual record
    res_individual = client.get(
        "/api/v1/citizens/citizen-ramesh-patel-01",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_individual.status_code == 403
    assert "Public Health Administrators are restricted to aggregated population intelligence" in res_individual.json()["detail"]

    # Blocked from raw citizen registry list
    res_list = client.get(
        "/api/v1/citizens",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_list.status_code == 403


def test_public_health_admin_allowed_aggregate_population_endpoints():
    admin_token = get_token_for("admin@sevahealth.ai")

    res_metrics = client.get(
        "/api/v1/population/metrics",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_metrics.status_code == 200
    assert "prevalence_rates" in res_metrics.json()
    assert "risk_tier_breakdown" in res_metrics.json()

    res_delta = client.get(
        "/api/v1/population/outcome-delta",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res_delta.status_code == 200
    assert "cohorts" in res_delta.json()


# 6. Organization / Tenant Boundary Isolation
def test_cross_tenant_isolation():
    # Register an external tenant worker
    store.add_user(UserRecord(
        id="worker-mandya-99",
        tenant_id="mandya_district_health",
        email="worker@mandya-health.gov.in",
        hashed_password=store.users["worker@sevahealth.ai"].hashed_password,
        role=UserRole.HEALTH_WORKER,
        full_name="Mandya Field Nurse",
        assigned_jurisdiction="Ward 4",
    ))

    # Add a citizen belonging to a completely separate tenant
    store.add_citizen(CitizenRecord(
        id="citizen-mysuru-isolated-01",
        tenant_id="mysuru_district_health",
        user_id="user-mysuru-01",
        abha_id="91-0000-0000-0001",
        first_name="Ananya",
        last_name="Gowda",
        birth_date="1995-05-15",
        gender="FEMALE",
        phone="+91 99999 00000",
        state="Karnataka",
        district="Mysuru",
        sub_district="Mysuru",
        village_or_ward="Ward 4",
    ))

    mandya_token = get_token_for("worker@mandya-health.gov.in")
    res = client.get(
        "/api/v1/citizens/citizen-mysuru-isolated-01",
        headers={"Authorization": f"Bearer {mandya_token}"}
    )
    assert res.status_code == 403
    assert "Cross-tenant access prohibited" in res.json()["detail"]


# 7. Token Revocation & Logout
def test_logout_revokes_token():
    token = get_token_for("citizen@sevahealth.ai")

    # Verify token works initially
    res_before = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res_before.status_code == 200

    # Call logout
    logout_res = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_res.status_code == 200
    assert logout_res.json()["status"] == "success"

    # Subsequent request using same token must fail with 401
    res_after = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res_after.status_code == 401
    assert "Token has been revoked" in res_after.json()["detail"]


# 8. Token Refresh & Token Rotation
def test_refresh_token_lifecycle():
    login_res = client.post(
        "/api/v1/auth/token",
        json={"email": "doctor@sevahealth.ai", "password": "password123"}
    )
    refresh_token = login_res.json()["refresh_token"]

    # Use refresh token to get new token pair
    ref_res = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert ref_res.status_code == 200
    new_access_token = ref_res.json()["access_token"]
    new_refresh_token = ref_res.json()["refresh_token"]
    assert new_access_token is not None

    # Verify new access token works
    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
    assert me_res.status_code == 200

    # Verify old refresh token is revoked (replay attack prevention)
    replay_res = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert replay_res.status_code == 401


# 9. Password Strength & User Registration
def test_registration_password_strength_and_uniqueness():
    # Ensure clean state for test user
    keys_to_del = [k for k, u in store.users.items() if getattr(u, "email", None) == "newcitizen@sevahealth.ai"]
    for k in keys_to_del:
        del store.users[k]

    # Weak password rejected
    weak_res = client.post("/api/v1/auth/register", json={
        "email": "weak@sevahealth.ai",
        "password": "short",
        "full_name": "Weak User",
    })
    assert weak_res.status_code == 400
    assert "Insecure password" in weak_res.json()["detail"]

    # Strong password accepted
    strong_res = client.post("/api/v1/auth/register", json={
        "email": "newcitizen@sevahealth.ai",
        "password": "StrongPassword2026!",
        "full_name": "Aarav Gupta",
        "role": "CITIZEN",
    })
    assert strong_res.status_code == 201
    assert strong_res.json()["status"] == "success"
    assert "citizen_id" in strong_res.json()

    # Duplicate registration rejected
    dup_res = client.post("/api/v1/auth/register", json={
        "email": "newcitizen@sevahealth.ai",
        "password": "StrongPassword2026!",
        "full_name": "Aarav Gupta Duplicate",
    })
    assert dup_res.status_code == 400
    assert "already registered" in dup_res.json()["detail"]


# 10. Account Recovery Flow
def test_account_recovery_flow():
    email = "newcitizen@sevahealth.ai"

    # Step 1: Request recovery token
    req_res = client.post("/api/v1/auth/recover/request", json={"email": email})
    assert req_res.status_code == 200
    token = req_res.json()["recovery_token"]
    assert token is not None

    # Step 2: Attempt reset with weak password
    weak_reset = client.post("/api/v1/auth/recover/reset", json={
        "email": email,
        "recovery_token": token,
        "new_password": "123",
    })
    assert weak_reset.status_code == 400

    # Step 3: Reset with strong password
    success_reset = client.post("/api/v1/auth/recover/reset", json={
        "email": email,
        "recovery_token": token,
        "new_password": "NewStrongPass99!",
    })
    assert success_reset.status_code == 200
    assert "successfully reset" in success_reset.json()["message"]

    # Step 4: Login with old password fails
    old_login = client.post("/api/v1/auth/token", json={
        "email": email,
        "password": "StrongPassword2026!",
    })
    assert old_login.status_code == 401

    # Step 5: Login with new password succeeds
    new_login = client.post("/api/v1/auth/token", json={
        "email": email,
        "password": "NewStrongPass99!",
    })
    assert new_login.status_code == 200
    assert "access_token" in new_login.json()
