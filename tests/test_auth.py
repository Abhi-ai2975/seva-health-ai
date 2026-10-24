import pytest
from packages.types.enums import UserRole
from packages.auth.jwt import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token,
)


def test_password_hashing():
    raw = "secure-clinical-password"
    hashed = get_password_hash(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_jwt_lifecycle():
    token = create_access_token(
        subject="user-123",
        tenant_id="karnataka_health",
        role=UserRole.CLINICIAN,
        scopes=["clinical:read", "clinical:write"],
    )
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload.sub == "user-123"
    assert payload.tenant_id == "karnataka_health"
    assert payload.role == UserRole.CLINICIAN
    assert "clinical:read" in payload.scopes


@pytest.mark.asyncio
async def test_get_current_user_token_optional_with_valid_token():
    from packages.auth.jwt import get_current_user_token_optional
    token = create_access_token(
        subject="doctor-456",
        tenant_id="tenant-mysuru",
        role=UserRole.CLINICIAN,
        scopes=["screening:read", "careplan:write"],
    )
    payload = await get_current_user_token_optional(token=token)
    assert payload.sub == "doctor-456"
    assert payload.role == UserRole.CLINICIAN
    assert payload.tenant_id == "tenant-mysuru"


@pytest.mark.asyncio
async def test_get_current_user_token_optional_invalid_token_raises_401():
    from fastapi import HTTPException
    from packages.auth.jwt import get_current_user_token_optional
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_token_optional(token="invalid.bearer.token")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_token_optional_production_raises_401():
    from fastapi import HTTPException
    from packages.auth.jwt import get_current_user_token_optional
    from packages.config.settings import settings
    original_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user_token_optional(token=None)
        assert exc_info.value.status_code == 401
        assert "Authentication token required" in exc_info.value.detail
    finally:
        settings.ENVIRONMENT = original_env


@pytest.mark.asyncio
async def test_get_current_user_token_optional_development_returns_mock():
    from packages.auth.jwt import get_current_user_token_optional
    from packages.config.settings import settings
    original_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "development"
        payload = await get_current_user_token_optional(token=None)
        assert payload.sub == "worker-asha-01"
        assert payload.role == UserRole.HEALTH_WORKER
        assert "screening:create" in payload.scopes
    finally:
        settings.ENVIRONMENT = original_env


def test_screening_and_intervention_routes_reject_unauthenticated_requests():
    from starlette.testclient import TestClient
    from services.api.main import app
    client = TestClient(app)

    # 1. Configurable screening route must reject unauthenticated requests
    resp_scr = client.post(
        "/api/v1/screening/evaluate-configurable",
        json={
            "citizen_id": "citizen-ramesh-patel-01",
            "answers": {"age": 50, "systolic_bp": 130},
        },
    )
    assert resp_scr.status_code == 401

    # 2. Comprehensive intervention generation route must reject unauthenticated requests
    resp_int = client.post(
        "/api/v1/intervention/generate-comprehensive/citizen-ramesh-patel-01",
    )
    assert resp_int.status_code == 401

    # 3. Standard care plan generation route must reject unauthenticated requests
    resp_plan = client.post(
        "/api/v1/intervention/generate/citizen-ramesh-patel-01",
    )
    assert resp_plan.status_code == 401

    # 4. Invalid tokens must NOT bypass authentication
    bad_headers = {"Authorization": "Bearer fake-invalid-or-forged-token"}
    assert client.post(
        "/api/v1/screening/evaluate-configurable",
        json={"citizen_id": "citizen-ramesh-patel-01", "answers": {}},
        headers=bad_headers,
    ).status_code == 401
    assert client.post(
        "/api/v1/intervention/generate-comprehensive/citizen-ramesh-patel-01",
        headers=bad_headers,
    ).status_code == 401
    assert client.post(
        "/api/v1/intervention/generate/citizen-ramesh-patel-01",
        headers=bad_headers,
    ).status_code == 401


def test_password_plaintext_verification_disallowed():
    from packages.auth.jwt import verify_password
    plain = "SuperSecurePassword123"
    # Plaintext comparison must return False even if stored string equals plaintext
    assert verify_password(plain, plain) is False
    assert verify_password("wrong", plain) is False


def test_password_legacy_sha256_verification_and_migration():
    import hashlib
    from starlette.testclient import TestClient
    from packages.auth.jwt import verify_password, is_bcrypt_hash
    from services.store import store, UserRecord
    from services.api.main import app
    from packages.types.enums import UserRole

    raw_password = "LegacyUserPassword123!"
    legacy_sha256_hash = hashlib.sha256(raw_password.encode("utf-8")).hexdigest()

    # Verify legacy sha256 can be verified
    assert verify_password(raw_password, legacy_sha256_hash) is True
    assert verify_password("WrongPassword123!", legacy_sha256_hash) is False
    assert is_bcrypt_hash(legacy_sha256_hash) is False

    # Seed a user with legacy SHA-256 hash
    test_email = "legacy_user@sevahealth.ai"
    legacy_user = UserRecord(
        id="usr-legacy-001",
        tenant_id="tenant-karnataka-moh",
        email=test_email,
        hashed_password=legacy_sha256_hash,
        role=UserRole.CLINICIAN,
        full_name="Legacy Clinician",
    )
    store.add_user(legacy_user)

    # Log in through API
    client = TestClient(app)
    resp = client.post("/api/v1/auth/token", json={"email": test_email, "password": raw_password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"

    # Verify hash has been automatically migrated to bcrypt in store
    updated_user = store.get_user(test_email)
    assert updated_user is not None
    assert is_bcrypt_hash(updated_user.hashed_password) is True
    assert updated_user.hashed_password != legacy_sha256_hash
    assert verify_password(raw_password, updated_user.hashed_password) is True


def test_cors_explicit_origins_configuration():
    from starlette.testclient import TestClient
    from packages.config.settings import settings
    from services.api.main import app

    client = TestClient(app)
    # Ensure wildcard '*' is not in allowed origins
    assert "*" not in settings.ALLOWED_ORIGINS
    assert "http://localhost:3000" in settings.ALLOWED_ORIGINS

    # Authorized origin receives CORS headers
    resp_allowed = client.options(
        "/api/v1/auth/token",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp_allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp_allowed.headers.get("access-control-allow-credentials") == "true"

    # Unauthorized origin is not reflected in access-control-allow-origin
    resp_unauthorized = client.options(
        "/api/v1/auth/token",
        headers={
            "Origin": "http://evil-attacker-site.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp_unauthorized.headers.get("access-control-allow-origin") != "http://evil-attacker-site.com"


def test_production_secret_key_security_validation():
    from packages.config.settings import Settings, INSECURE_DEV_SECRET

    # 1. Insecure default secret in production raises ValueError
    with pytest.raises(ValueError) as exc1:
        Settings(ENVIRONMENT="production", SECRET_KEY=INSECURE_DEV_SECRET, DEBUG=False)
    assert "Insecure default development SECRET_KEY cannot be used in production" in str(exc1.value)

    # 2. Secret key shorter than 32 chars in production raises ValueError
    with pytest.raises(ValueError) as exc2:
        Settings(ENVIRONMENT="production", SECRET_KEY="short-secret-key-12345", DEBUG=False)
    assert "Production SECRET_KEY must be at least 32 characters long" in str(exc2.value)

    # 3. DEBUG=True in production raises ValueError
    with pytest.raises(ValueError) as exc3:
        Settings(ENVIRONMENT="production", SECRET_KEY="a" * 32, DEBUG=True)
    assert "DEBUG mode must be disabled" in str(exc3.value)

    # 4. Valid production settings succeed

def test_idor_clinical_document_and_ai_endpoints():
    from starlette.testclient import TestClient
    from services.api.main import app
    from services.store import store
    from scripts.seed_data import seed_all_demo_data
    from packages.auth.jwt import create_access_token

    seed_all_demo_data()
    client = TestClient(app)

    # Legitimate owner citizen
    owner_token = create_access_token(
        subject="citizen-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    # Attacking citizen
    attacker_token = create_access_token(
        subject="citizen-lakshmi-id",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )
    attacker_headers = {"Authorization": f"Bearer {attacker_token}"}

    # Seed a document for Ramesh Patel
    doc_id = "doc-test-idor-01"
    store.document_files[doc_id] = {
        "document_id": doc_id,
        "citizen_id": "citizen-ramesh-patel-01",
        "filename": "confidential_biopsy.pdf",
        "content_type": "application/pdf",
        "size_bytes": 100,
        "sha256_hash": "dummyhash123",
        "storage_path": "/storage/vault/citizen-ramesh-patel-01/doc-test-idor-01/confidential_biopsy.pdf",
        "raw_bytes": b"%PDF-1.4 test biopsy document content",
    }
    store.documents["citizen-ramesh-patel-01"] = [
        {"id": doc_id, "filename": "confidential_biopsy.pdf", "citizen_id": "citizen-ramesh-patel-01"}
    ]

    # 1. Attacker attempts to download owner's document via /download/{doc_id} -> 403
    resp_dl_atk = client.get(f"/api/v1/documents/download/{doc_id}", headers=attacker_headers)
    assert resp_dl_atk.status_code == 403, f"Expected 403, got {resp_dl_atk.status_code}"

    # Owner downloads their own document -> 200
    resp_dl_owner = client.get(f"/api/v1/documents/download/{doc_id}", headers=owner_headers)
    assert resp_dl_owner.status_code == 200

    # 2. Attacker attempts to list owner's documents via /{citizen_id} -> 403
    resp_list_atk = client.get("/api/v1/documents/citizen-ramesh-patel-01", headers=attacker_headers)
    assert resp_list_atk.status_code == 403

    # Owner lists their own documents -> 200
    resp_list_owner = client.get("/api/v1/documents/citizen-ramesh-patel-01", headers=owner_headers)
    assert resp_list_owner.status_code == 200
    assert len(resp_list_owner.json()) >= 1

    # 3. Attacker attempts to request AI explanation for owner -> 403
    resp_ai_atk = client.post("/api/v1/ai/explain/citizen-ramesh-patel-01", headers=attacker_headers)
    assert resp_ai_atk.status_code == 403

    # Owner requests AI explanation for self -> 200
    resp_ai_owner = client.post("/api/v1/ai/explain/citizen-ramesh-patel-01", headers=owner_headers)
    assert resp_ai_owner.status_code == 200


def test_false_denial_citizen_user_id_vs_citizen_id_resolution():
    from starlette.testclient import TestClient
    from services.api.main import app
    from services.store import store, CitizenRecord
    from packages.types.enums import Gender
    from packages.auth.jwt import create_access_token

    client = TestClient(app)

    # Citizen with differing user_id and citizen_id
    cit_rec = CitizenRecord(
        id="cit-uuid-1234",
        tenant_id="karnataka_state_health",
        user_id="usr-auth-account-5678",
        abha_id="91-9999-8888-7777",
        first_name="Suresh",
        last_name="Gowda",
        birth_date="1982-05-10",
        gender=Gender.MALE,
        phone="+919876543299",
        state="Karnataka",
        district="Mandya",
        sub_district="Mandya",
        village_or_ward="Ward 5",
    )
    store.add_citizen(cit_rec)

    # Document stored under registry ID cit-uuid-1234
    safe_path = "/storage/vault/cit-uuid-1234/doc-suresh-01/report.pdf"
    store.document_files["doc-suresh-01"] = {
        "document_id": "doc-suresh-01",
        "citizen_id": "cit-uuid-1234",
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 50,
        "sha256_hash": "hash-suresh",
        "storage_path": safe_path,
        "raw_bytes": b"%PDF-1.4 report bytes",
    }

    # Citizen token sub contains user_id ("usr-auth-account-5678")
    citizen_token = create_access_token(
        subject="usr-auth-account-5678",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )
    headers = {"Authorization": f"Bearer {citizen_token}"}

    # Retrieve-by-path must succeed without false denial (200 OK)
    resp = client.get(f"/api/v1/documents/retrieve-by-path?path={safe_path}", headers=headers)
    assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.text}"

    # Another citizen attempting access is blocked (403 Forbidden)
    other_token = create_access_token(
        subject="usr-other-citizen-9999",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )
    resp_cross = client.get(f"/api/v1/documents/retrieve-by-path?path={safe_path}", headers={"Authorization": f"Bearer {other_token}"})
    assert resp_cross.status_code == 403


def test_role_based_least_privilege_token_scopes():
    from starlette.testclient import TestClient
    from services.api.main import app
    from packages.auth.jwt import decode_access_token
    from scripts.seed_data import seed_all_demo_data

    seed_all_demo_data()
    client = TestClient(app)

    # 1. Citizen login
    r_cit = client.post("/api/v1/auth/token", json={"email": "citizen@sevahealth.ai", "password": "password123"})
    assert r_cit.status_code == 200
    cit_payload = decode_access_token(r_cit.json()["access_token"])
    assert "*" not in cit_payload.scopes
    assert "citizen:read_self" in cit_payload.scopes
    assert "consent:manage" in cit_payload.scopes

    # 2. Health worker login
    r_hw = client.post("/api/v1/auth/token", json={"email": "worker@sevahealth.ai", "password": "password123"})
    assert r_hw.status_code == 200
    hw_payload = decode_access_token(r_hw.json()["access_token"])
    assert "*" not in hw_payload.scopes
    assert "screening:create" in hw_payload.scopes
    assert "citizen:read" in hw_payload.scopes

    # 3. Clinician login
    r_doc = client.post("/api/v1/auth/token", json={"email": "doctor@sevahealth.ai", "password": "password123"})
    assert r_doc.status_code == 200
    doc_payload = decode_access_token(r_doc.json()["access_token"])
    assert "*" not in doc_payload.scopes
    assert "clinical:read" in doc_payload.scopes
    assert "screening:review" in doc_payload.scopes

    # 4. System Admin login
    r_admin = client.post("/api/v1/auth/token", json={"email": "sysadmin@sevahealth.ai", "password": "password123"})
    assert r_admin.status_code == 200
    admin_payload = decode_access_token(r_admin.json()["access_token"])
    assert "*" in admin_payload.scopes


def test_unilateral_consent_override_blocked_without_proxy_artifact():
    from starlette.testclient import TestClient
    from services.api.main import app
    from packages.auth.jwt import create_access_token
    from scripts.seed_data import seed_all_demo_data

    seed_all_demo_data()
    client = TestClient(app)

    hw_token = create_access_token(
        subject="worker-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.HEALTH_WORKER,
    )
    hw_headers = {"Authorization": f"Bearer {hw_token}"}

    # 1. Health worker attempts unilateral consent grant without proxy artifact -> 403 Forbidden
    payload_no_proxy = {
        "subject": "citizen-ramesh-patel-01",
        "category": "wearable_data",
        "purpose": "Unilateral override test",
        "scope": ["vitals", "labs"],
        "recipient": "PRIMARY_CARE_TEAM",
    }
    r_unilateral = client.post("/api/v1/privacy/consents", json=payload_no_proxy, headers=hw_headers)
    assert r_unilateral.status_code == 403, f"Expected 403 but got {r_unilateral.status_code}"
    assert "proxy" in r_unilateral.json()["detail"].lower()

    # 2. Health worker provides authenticated proxy mandate / ABDM artifact with signature -> 201 Created
    payload_with_proxy = {
        "subject": "citizen-ramesh-patel-01",
        "category": "clinical_care",
        "purpose": "Assisted care consent",
        "scope": ["vitals", "labs"],
        "recipient": "PRIMARY_CARE_TEAM",
        "consent_artifact": {
            "artifact_id": "art-abdm-valid-01",
            "citizen_id": "citizen-ramesh-patel-01",
            "signature": "SHA256-RSA:abdm-signature-ok",
        },
    }
    r_proxy = client.post("/api/v1/privacy/consents", json=payload_with_proxy, headers=hw_headers)
    assert r_proxy.status_code == 201
    created_id = r_proxy.json()["id"]

    # 3. Health worker attempts unilateral revocation without proxy artifact -> 403 Forbidden
    r_revoke_unilateral = client.post(
        f"/api/v1/privacy/consents/{created_id}/revoke",
        json={"reason": "Worker unilateral revocation"},
        headers=hw_headers,
    )
    assert r_revoke_unilateral.status_code == 403

    # 4. Citizen data principal revokes directly -> 200 OK
    cit_token = create_access_token(
        subject="citizen-user-01",
        tenant_id="karnataka_state_health",
        role=UserRole.CITIZEN,
    )
    r_revoke_owner = client.post(
        f"/api/v1/privacy/consents/{created_id}/revoke",
        json={"reason": "Citizen self revocation"},
        headers={"Authorization": f"Bearer {cit_token}"},
    )
    assert r_revoke_owner.status_code == 200
    assert r_revoke_owner.json()["status"] == "REVOKED"


def test_ai_risk_explanation_age_calculation_runtime_fix():
    from starlette.testclient import TestClient
    from services.api.main import app
    from services.store import store
    from packages.auth.jwt import create_access_token
    from packages.types.enums import UserRole
    from datetime import datetime

    client = TestClient(app)
    citizen = store.get_citizen("citizen-ramesh-patel-01")
    assert citizen is not None
    assert citizen.birth_date is not None

    expected_age = datetime.now().year - int(citizen.birth_date.split("-")[0])
    assert expected_age > 0

    token = create_access_token(
        subject=citizen.user_id,
        tenant_id=citizen.tenant_id,
        role=UserRole.CITIZEN,
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(f"/api/v1/ai/explain/{citizen.id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "plain_summary" in data




